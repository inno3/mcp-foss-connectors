"""Tests unitaires offline pour le connecteur Nextcloud.

Tous les appels HTTP/WebDAV sont mockés : la suite tourne sans réseau ni credentials réels.
"""

import asyncio
import base64
import hashlib
import json
import mimetypes
import os
import urllib.parse
from contextlib import contextmanager
from unittest.mock import AsyncMock, patch

import httpx
import pytest

os.environ.setdefault("NEXTCLOUD_URL", "https://cloud.example.test")
os.environ.setdefault("NEXTCLOUD_USER", "alice")
os.environ.setdefault("NEXTCLOUD_APP_PASSWORD", "app-pass-xxxx")

import nextcloud_mcp.server as srv  # noqa: E402
from nextcloud_mcp.server import (  # noqa: E402
    _compact,
    _extract_ocs_data,
    _parse_propfind,
    nextcloud_list_files,
)

PROPFIND_XML = """<?xml version="1.0"?>
<d:multistatus xmlns:d="DAV:">
  <d:response>
    <d:href>/remote.php/dav/files/alice/Documents/</d:href>
    <d:propstat>
      <d:prop>
        <d:displayname>Documents</d:displayname>
        <d:resourcetype><d:collection/></d:resourcetype>
      </d:prop>
      <d:status>HTTP/1.1 200 OK</d:status>
    </d:propstat>
  </d:response>
  <d:response>
    <d:href>/remote.php/dav/files/alice/Documents/rapport.pdf</d:href>
    <d:propstat>
      <d:prop>
        <d:displayname>rapport.pdf</d:displayname>
        <d:getcontenttype>application/pdf</d:getcontenttype>
        <d:getcontentlength>2048</d:getcontentlength>
        <d:resourcetype/>
      </d:prop>
      <d:status>HTTP/1.1 200 OK</d:status>
    </d:propstat>
  </d:response>
</d:multistatus>"""


class TestHelpers:
    def test_parse_propfind_extracts_entries(self) -> None:
        items = _parse_propfind(PROPFIND_XML)
        assert len(items) == 2
        folder, pdf = items
        assert folder["is_directory"] is True
        assert pdf["filename"] == "rapport.pdf"
        assert pdf["size"] == 2048
        assert pdf["content_type"] == "application/pdf"
        assert pdf["is_directory"] is False

    def test_parse_propfind_invalid_xml_returns_empty(self) -> None:
        assert _parse_propfind("<not xml") == []

    def test_extract_ocs_data_unwraps_envelope(self) -> None:
        resp = {"ocs": {"data": {"id": 1}}}
        assert _extract_ocs_data(resp) == {"id": 1}

    def test_compact(self) -> None:
        assert _compact({"a": 1}) == '{"a":1}'


@pytest.mark.asyncio
class TestListFiles:
    async def test_drops_self_entry_at_depth1(self) -> None:
        with patch(
            "nextcloud_mcp.server._webdav",
            new_callable=AsyncMock,
            return_value=PROPFIND_XML,
        ) as mock_dav:
            raw = await nextcloud_list_files("/Documents", depth=1)
        assert mock_dav.call_args[0][0] == "PROPFIND"
        data = json.loads(raw)
        # depth=1 strips the folder itself -> only the pdf remains.
        # Le retrait se fait désormais par comparaison de chemin, plus par
        # position : trier avant de retirer la première entrée écartait un
        # élément au hasard.
        assert data["total"] == 1
        assert data["returned"] == 1
        assert data["has_more"] is False
        assert data["items"][0]["filename"] == "rapport.pdf"


# --------------------------------------------------------------------------
# Pagination, tri, ecriture locale, recherche
# Portes avec l'implementation le 11/08/2026. TestListFiles du connecteur
# prive est renommee TestListFilesPagination : la classe homonyme d'ici
# couvre le retrait de l'entree du dossier, les deux sont conservees.
# --------------------------------------------------------------------------


class _FakeClient:
    """AsyncClient minimal : journalise les appels et rejoue de vraies Response.

    On mocke au niveau du transport plutôt qu'au niveau de `_request` pour que
    la construction d'URL, les en-têtes et `raise_for_status()` restent du code
    réellement exercé (le 206 d'une lecture par plage en dépend).
    """

    def __init__(self, handler, calls: list) -> None:
        self._handler = handler
        self._calls = calls

    async def __aenter__(self) -> "_FakeClient":
        return self

    async def __aexit__(self, *exc_info) -> bool:
        return False

    async def request(self, method: str, url: str, **kwargs):
        self._calls.append({"method": method, "url": url, **kwargs})
        status, body, headers = self._handler(method, url, kwargs)
        return httpx.Response(
            status_code=status,
            content=body,
            headers=headers or {},
            request=httpx.Request(method, url),
        )


@contextmanager
def mock_http(handler):
    """Patche httpx.AsyncClient et rend la liste des appels effectués."""
    calls: list = []
    with patch("httpx.AsyncClient", lambda *a, **kw: _FakeClient(handler, calls)):
        yield calls


def xml_handler(xml: str):
    """Handler renvoyant un multistatus WebDAV."""
    def _handler(method, url, kwargs):
        return 207, xml.encode("utf-8"), {"content-type": "application/xml; charset=utf-8"}
    return _handler


def json_handler(payload: dict, status: int = 200):
    """Handler renvoyant une réponse OCS JSON."""
    def _handler(method, url, kwargs):
        return status, json.dumps(payload).encode("utf-8"), {"content-type": "application/json"}
    return _handler


def bytes_handler(payload: bytes, status: int = 206, headers: dict | None = None):
    """Handler renvoyant un corps binaire (téléchargement)."""
    def _handler(method, url, kwargs):
        return status, payload, headers or {"content-type": "text/plain"}
    return _handler


def _response(href: str, displayname: str, modified: str, *, is_dir=False, size=0, ctype="text/plain") -> str:
    restype = "<d:collection/>" if is_dir else ""
    length = "" if is_dir else f"<d:getcontentlength>{size}</d:getcontentlength>"
    content_type = "" if is_dir else f"<d:getcontenttype>{ctype}</d:getcontenttype>"
    return f"""  <d:response>
    <d:href>{href}</d:href>
    <d:propstat>
      <d:prop>
        <d:displayname>{displayname}</d:displayname>
        {content_type}
        {length}
        <d:getlastmodified>{modified}</d:getlastmodified>
        <d:resourcetype>{restype}</d:resourcetype>
      </d:prop>
      <d:status>HTTP/1.1 200 OK</d:status>
    </d:propstat>
  </d:response>
"""


def multistatus(*responses: str) -> str:
    return (
        '<?xml version="1.0"?>\n<d:multistatus xmlns:d="DAV:">\n'
        + "".join(responses)
        + "</d:multistatus>\n"
    )


_ROOT = "/remote.php/dav/files/tester"


FOLDER_XML = multistatus(
    _response(f"{_ROOT}/Notes/", "Notes", "Tue, 29 Jul 2026 08:12:33 GMT", is_dir=True),
    _response(f"{_ROOT}/Notes/avril.txt", "avril.txt", "Wed, 01 Apr 2026 10:00:00 GMT", size=11),
    _response(f"{_ROOT}/Notes/juillet.txt", "juillet.txt", "Tue, 29 Jul 2026 08:12:33 GMT", size=22),
    _response(f"{_ROOT}/Notes/mars.txt", "mars.txt", "Sun, 01 Mar 2026 10:00:00 GMT", size=33),
)


def list_files(**kwargs) -> dict:
    return json.loads(asyncio.run(srv.nextcloud_list_files(**kwargs)))


def download(**kwargs) -> dict:
    return json.loads(asyncio.run(srv.nextcloud_download_file(**kwargs)))


def search(**kwargs) -> dict:
    return json.loads(asyncio.run(srv.nextcloud_search(**kwargs)))


class TestParsePropfind:
    def test_fields_and_relative_path(self) -> None:
        items = srv._parse_propfind(FOLDER_XML)
        assert len(items) == 4
        entry = items[1]
        # href (préfixe WebDAV constant) remplacé par un chemin utilisateur.
        assert "href" not in entry
        assert entry["path"] == "/Notes/avril.txt"
        assert entry["filename"] == "avril.txt"
        assert entry["size"] == 11
        assert entry["is_directory"] is False
        assert entry["content_type"] == "text/plain"
        assert entry["last_modified"] == "Wed, 01 Apr 2026 10:00:00 GMT"

    def test_directory_detection(self) -> None:
        folder = srv._parse_propfind(FOLDER_XML)[0]
        assert folder["is_directory"] is True
        assert folder["path"] == "/Notes"
        assert folder["content_type"] == "httpd/unix-directory"

    def test_displayname_omitted_when_redundant(self) -> None:
        """Le cas de très loin le plus fréquent : displayname == filename."""
        items = srv._parse_propfind(FOLDER_XML)
        assert all("displayname" not in it for it in items)

    def test_displayname_kept_when_informative(self) -> None:
        xml = multistatus(
            _response(f"{_ROOT}/f/id42", "Rapport final.pdf", "Tue, 29 Jul 2026 08:12:33 GMT", size=5)
        )
        assert srv._parse_propfind(xml)[0]["displayname"] == "Rapport final.pdf"

    def test_percent_encoded_href_is_decoded(self) -> None:
        xml = multistatus(
            _response(
                f"{_ROOT}/Docs/rapport%20%C3%A9t%C3%A9.pdf",
                "rapport été.pdf",
                "Tue, 29 Jul 2026 08:12:33 GMT",
                size=7,
            )
        )
        entry = srv._parse_propfind(xml)[0]
        assert entry["filename"] == "rapport été.pdf"
        assert entry["path"] == "/Docs/rapport été.pdf"

    def test_non_200_propstat_ignored(self) -> None:
        """Nextcloud renvoie un propstat 404 pour les propriétés absentes."""
        xml = multistatus(
            """  <d:response>
    <d:href>/remote.php/dav/files/tester/Notes/a.txt</d:href>
    <d:propstat>
      <d:prop><d:getlastmodified>Tue, 29 Jul 2026 08:12:33 GMT</d:getlastmodified></d:prop>
      <d:status>HTTP/1.1 200 OK</d:status>
    </d:propstat>
    <d:propstat>
      <d:prop><d:displayname>NE DOIT PAS APPARAITRE</d:displayname></d:prop>
      <d:status>HTTP/1.1 404 Not Found</d:status>
    </d:propstat>
  </d:response>
"""
        )
        entry = srv._parse_propfind(xml)[0]
        assert "displayname" not in entry

    def test_invalid_size_falls_back_to_zero(self) -> None:
        xml = multistatus(
            _response(f"{_ROOT}/a.txt", "a.txt", "Tue, 29 Jul 2026 08:12:33 GMT", size="abc")
        )
        assert srv._parse_propfind(xml)[0]["size"] == 0

    def test_malformed_xml_returns_empty(self) -> None:
        assert srv._parse_propfind("<pas du xml") == []

    def test_unknown_webdav_root_keeps_href(self) -> None:
        """Autre montage WebDAV : mieux vaut un chemin brut qu'une info perdue."""
        xml = multistatus(_response("/dav/autre/a.txt", "a.txt", "Tue, 29 Jul 2026 08:12:33 GMT"))
        assert srv._parse_propfind(xml)[0]["path"] == "/dav/autre/a.txt"


class TestDateHelpers:
    def test_rfc1123_parsed(self) -> None:
        parsed = srv._parse_rfc1123("Tue, 29 Jul 2026 08:12:33 GMT")
        assert (parsed.year, parsed.month, parsed.day) == (2026, 7, 29)
        assert parsed.tzinfo is not None

    @pytest.mark.parametrize("bad", ["", "pas une date", "2026-07-29"])
    def test_rfc1123_invalid_returns_none(self, bad) -> None:
        assert srv._parse_rfc1123(bad) is None

    def test_rfc1123_order_is_not_lexicographic(self) -> None:
        """Le piège que le tri doit éviter : « Wed, 01 Apr » > « Tue, 29 Jul »."""
        avril = "Wed, 01 Apr 2026 10:00:00 GMT"
        juillet = "Tue, 29 Jul 2026 08:12:33 GMT"
        assert avril > juillet                                     # lexicographie
        assert srv._parse_rfc1123(avril) < srv._parse_rfc1123(juillet)  # chronologie

    @pytest.mark.parametrize("value", ["2026-07-01", "2026-07-01T08:00:00", "2026-07-01T08:00:00Z"])
    def test_iso_accepts_date_and_datetime(self, value) -> None:
        parsed = srv._parse_iso_datetime(value)
        assert (parsed.year, parsed.month, parsed.day) == (2026, 7, 1)
        assert parsed.tzinfo is not None  # comparable aux dates WebDAV (GMT)

    def test_iso_invalid_raises(self) -> None:
        with pytest.raises(ValueError):
            srv._parse_iso_datetime("le 1er juillet")

    def test_content_range_total(self) -> None:
        assert srv._parse_content_range_total("bytes 0-99/12345") == 12345
        assert srv._parse_content_range_total("bytes 0-99/*") is None
        assert srv._parse_content_range_total("") is None


class TestListFilesPagination:
    def test_self_entry_removed_and_sorted_by_date_desc(self) -> None:
        with mock_http(xml_handler(FOLDER_XML)) as calls:
            out = list_files(path="/Notes")
        assert calls[0]["method"] == "PROPFIND"
        assert calls[0]["headers"]["Depth"] == "1"
        # Le dossier interrogé lui-même ne fait pas partie de son contenu.
        assert [it["filename"] for it in out["items"]] == ["juillet.txt", "avril.txt", "mars.txt"]
        assert out["total"] == 3
        assert out["returned"] == 3
        assert out["has_more"] is False
        assert out["sort"] == "last_modified desc"

    def test_pagination_reports_total_and_has_more(self) -> None:
        with mock_http(xml_handler(FOLDER_XML)):
            page1 = list_files(path="/Notes", limit=2)
            page2 = list_files(path="/Notes", limit=2, offset=2)
        assert page1["returned"] == 2 and page1["total"] == 3 and page1["has_more"] is True
        assert [it["filename"] for it in page1["items"]] == ["juillet.txt", "avril.txt"]
        assert page2["returned"] == 1 and page2["has_more"] is False
        assert [it["filename"] for it in page2["items"]] == ["mars.txt"]

    def test_offset_beyond_total_is_empty_not_error(self) -> None:
        with mock_http(xml_handler(FOLDER_XML)):
            out = list_files(path="/Notes", offset=99)
        assert out["items"] == [] and out["total"] == 3 and out["has_more"] is False

    def test_limit_is_capped(self) -> None:
        with mock_http(xml_handler(FOLDER_XML)):
            out = list_files(path="/Notes", limit=10_000)
        assert out["limit"] == srv._MAX_LIST_LIMIT

    def test_limit_below_one_refused(self) -> None:
        with mock_http(xml_handler(FOLDER_XML)):
            out = list_files(path="/Notes", limit=0)
        assert "error" in out

    def test_negative_offset_clamped(self) -> None:
        with mock_http(xml_handler(FOLDER_XML)):
            out = list_files(path="/Notes", offset=-5)
        assert out["offset"] == 0 and out["returned"] == 3

    def test_glob_filters_on_filename(self) -> None:
        xml = multistatus(
            _response(f"{_ROOT}/D/", "D", "Tue, 29 Jul 2026 08:12:33 GMT", is_dir=True),
            _response(f"{_ROOT}/D/a.pdf", "a.pdf", "Tue, 29 Jul 2026 08:12:33 GMT"),
            _response(f"{_ROOT}/D/b.txt", "b.txt", "Tue, 29 Jul 2026 08:12:33 GMT"),
        )
        with mock_http(xml_handler(xml)):
            out = list_files(path="/D", glob="*.pdf")
        assert [it["filename"] for it in out["items"]] == ["a.pdf"]
        assert out["total"] == 1  # le total reflète le filtre, pas le brut

    def test_glob_is_case_insensitive(self) -> None:
        """Le résultat ne doit pas dépendre de la casse du système local."""
        xml = multistatus(
            _response(f"{_ROOT}/D/", "D", "Tue, 29 Jul 2026 08:12:33 GMT", is_dir=True),
            _response(f"{_ROOT}/D/RAPPORT.PDF", "RAPPORT.PDF", "Tue, 29 Jul 2026 08:12:33 GMT"),
        )
        with mock_http(xml_handler(xml)):
            out = list_files(path="/D", glob="*.pdf")
        assert [it["filename"] for it in out["items"]] == ["RAPPORT.PDF"]

    def test_modified_since_filters_chronologically(self) -> None:
        with mock_http(xml_handler(FOLDER_XML)):
            out = list_files(path="/Notes", modified_since="2026-04-01")
        # avril.txt est daté du 01/04 à 10:00 : conservé (borne incluse).
        assert [it["filename"] for it in out["items"]] == ["juillet.txt", "avril.txt"]

    def test_modified_since_accepts_iso_datetime(self) -> None:
        with mock_http(xml_handler(FOLDER_XML)):
            out = list_files(path="/Notes", modified_since="2026-04-01T12:00:00Z")
        assert [it["filename"] for it in out["items"]] == ["juillet.txt"]

    def test_modified_since_invalid_is_explicit(self) -> None:
        with mock_http(xml_handler(FOLDER_XML)):
            out = list_files(path="/Notes", modified_since="hier")
        assert "error" in out and "modified_since" in out["error"]

    def test_unparsable_dates_sorted_last_not_dropped(self) -> None:
        xml = multistatus(
            _response(f"{_ROOT}/D/", "D", "Tue, 29 Jul 2026 08:12:33 GMT", is_dir=True),
            _response(f"{_ROOT}/D/sansdate.txt", "sansdate.txt", ""),
            _response(f"{_ROOT}/D/mars.txt", "mars.txt", "Sun, 01 Mar 2026 10:00:00 GMT"),
        )
        with mock_http(xml_handler(xml)):
            out = list_files(path="/D")
        assert [it["filename"] for it in out["items"]] == ["mars.txt", "sansdate.txt"]

    def test_modified_since_drops_undated_entries(self) -> None:
        xml = multistatus(
            _response(f"{_ROOT}/D/", "D", "Tue, 29 Jul 2026 08:12:33 GMT", is_dir=True),
            _response(f"{_ROOT}/D/sansdate.txt", "sansdate.txt", ""),
        )
        with mock_http(xml_handler(xml)):
            out = list_files(path="/D", modified_since="2020-01-01")
        assert out["total"] == 0

    def test_depth_zero_keeps_the_folder_itself(self) -> None:
        xml = multistatus(
            _response(f"{_ROOT}/Notes/", "Notes", "Tue, 29 Jul 2026 08:12:33 GMT", is_dir=True)
        )
        with mock_http(xml_handler(xml)) as calls:
            out = list_files(path="/Notes", depth=0)
        assert calls[0]["headers"]["Depth"] == "0"
        assert out["total"] == 1 and out["items"][0]["filename"] == "Notes"

    def test_empty_folder_yields_no_items(self) -> None:
        xml = multistatus(
            _response(f"{_ROOT}/Vide/", "Vide", "Tue, 29 Jul 2026 08:12:33 GMT", is_dir=True)
        )
        with mock_http(xml_handler(xml)):
            out = list_files(path="/Vide")
        assert out["total"] == 0 and out["items"] == []

    def test_http_error_is_reported(self) -> None:
        def handler(method, url, kwargs):
            return 404, b'{"message":"Not Found"}', {"content-type": "application/json"}

        with mock_http(handler):
            out = list_files(path="/Absent")
        assert "error" in out and "404" in out["error"]


class TestResolveSavePath:
    """Toute écriture est confinée à NEXTCLOUD_DOWNLOAD_DIR."""

    def test_relative_path_resolves_inside_root(self, tmp_path, monkeypatch) -> None:
        monkeypatch.setattr(srv, "_NC_DOWNLOAD_DIR", str(tmp_path))
        target = srv._resolve_save_path("nextcloud/rapport.pdf")
        assert target == os.path.join(os.path.realpath(str(tmp_path)), "nextcloud", "rapport.pdf")

    def test_absolute_path_is_refused(self, tmp_path, monkeypatch) -> None:
        monkeypatch.setattr(srv, "_NC_DOWNLOAD_DIR", str(tmp_path))
        with pytest.raises(ValueError, match="relatif"):
            srv._resolve_save_path("/home/bjean/.ssh/authorized_keys")

    def test_home_shorthand_is_refused(self, tmp_path, monkeypatch) -> None:
        monkeypatch.setattr(srv, "_NC_DOWNLOAD_DIR", str(tmp_path))
        with pytest.raises(ValueError, match="relatif"):
            srv._resolve_save_path("~/.bashrc")

    @pytest.mark.parametrize("evil", ["../../.bashrc", "sub/../../escape.txt", "..\\..\\windows.txt"])
    def test_traversal_is_refused(self, evil, tmp_path, monkeypatch) -> None:
        monkeypatch.setattr(srv, "_NC_DOWNLOAD_DIR", str(tmp_path))
        with pytest.raises(ValueError):
            srv._resolve_save_path(evil)

    def test_symlink_escaping_root_is_refused(self, tmp_path, monkeypatch) -> None:
        """Un symlink déjà présent dans la racine ne doit pas servir d'échappatoire."""
        root = tmp_path / "dl"
        root.mkdir()
        outside = tmp_path / "outside"
        outside.mkdir()
        os.symlink(str(outside), str(root / "escape"))
        monkeypatch.setattr(srv, "_NC_DOWNLOAD_DIR", str(root))
        with pytest.raises(ValueError, match="sort de la racine"):
            srv._resolve_save_path("escape/loot.txt")

    @pytest.mark.parametrize("blank", ["", "   "])
    def test_empty_path_is_refused(self, blank, tmp_path, monkeypatch) -> None:
        monkeypatch.setattr(srv, "_NC_DOWNLOAD_DIR", str(tmp_path))
        with pytest.raises(ValueError, match="vide"):
            srv._resolve_save_path(blank)


class TestWriteLocalFile:
    def test_writes_and_creates_parent(self, tmp_path) -> None:
        target = str(tmp_path / "a" / "b" / "file.bin")
        srv._write_local_file(target, b"hello", overwrite=False)
        with open(target, "rb") as fh:
            assert fh.read() == b"hello"

    def test_does_not_overwrite_by_default(self, tmp_path) -> None:
        target = str(tmp_path / "file.bin")
        srv._write_local_file(target, b"first", overwrite=False)
        with pytest.raises(ValueError, match="existe déjà"):
            srv._write_local_file(target, b"second", overwrite=False)
        with open(target, "rb") as fh:
            assert fh.read() == b"first"

    def test_overwrite_when_explicit(self, tmp_path) -> None:
        target = str(tmp_path / "file.bin")
        srv._write_local_file(target, b"first", overwrite=False)
        srv._write_local_file(target, b"second", overwrite=True)
        with open(target, "rb") as fh:
            assert fh.read() == b"second"

    def test_size_cap_enforced(self, tmp_path, monkeypatch) -> None:
        monkeypatch.setattr(srv, "_MAX_SAVE_BYTES", 10)
        with pytest.raises(ValueError, match="plafond"):
            srv._write_local_file(str(tmp_path / "big.bin"), b"x" * 11, overwrite=False)

    def test_no_partial_file_left_on_refusal(self, tmp_path, monkeypatch) -> None:
        """Un .part oublié passerait pour un téléchargement réussi."""
        monkeypatch.setattr(srv, "_MAX_SAVE_BYTES", 10)
        target = str(tmp_path / "big.bin")
        with pytest.raises(ValueError):
            srv._write_local_file(target, b"x" * 11, overwrite=False)
        assert not os.path.exists(target)
        assert not os.path.exists(target + ".part")


class TestDownloadFile:
    def test_range_header_bounds_the_read(self) -> None:
        """Sans Range, le corps entier arrive en RAM avant tout contrôle."""
        with mock_http(bytes_handler(b"bonjour")) as calls:
            out = download(path="/Notes/a.txt")
        assert calls[0]["headers"]["Range"] == f"bytes=0-{srv._MAX_FILE_SIZE}"
        assert out["content"] == "bonjour"

    def test_explicit_range_is_forwarded(self) -> None:
        headers = {"content-type": "text/plain", "content-range": "bytes 10-14/500"}
        with mock_http(bytes_handler(b"abcde", headers=headers)) as calls:
            out = download(path="/Notes/a.txt", offset=10, length=5)
        assert calls[0]["headers"]["Range"] == "bytes=10-14"
        assert out["content"] == "abcde"
        assert out["offset"] == 10
        assert out["total_size"] == 500
        assert out["partial"] is True

    def test_length_beyond_context_cap_refused(self) -> None:
        with mock_http(bytes_handler(b"")):
            out = download(path="/Notes/a.txt", length=srv._MAX_FILE_SIZE + 1)
        assert "error" in out and "plafond" in out["error"]

    def test_negative_offset_refused(self) -> None:
        with mock_http(bytes_handler(b"")):
            out = download(path="/Notes/a.txt", offset=-1)
        assert "error" in out

    def test_oversized_file_points_to_save_path(self, monkeypatch) -> None:
        monkeypatch.setattr(srv, "_MAX_FILE_SIZE", 10)
        with mock_http(bytes_handler(b"x" * 11)):
            out = download(path="/Notes/gros.txt")
        assert "error" in out and "save_path" in out["error"]

    def test_binary_without_save_path_is_explicit(self) -> None:
        with mock_http(bytes_handler(b"\xff\xfe\x00binaire")):
            out = download(path="/Photos/x.png")
        assert "error" in out and "save_path" in out["error"]

    def test_truncated_multibyte_is_not_called_binary(self) -> None:
        """Une plage coupe un caractère UTF-8 : ce n'est pas un fichier binaire."""
        # b"\xc3\xa9t\xc3" : le « é » final est tronqué à mi-caractère.
        headers = {"content-type": "text/plain", "content-range": "bytes 0-3/10"}
        with mock_http(bytes_handler("été".encode("utf-8")[:4], headers=headers)):
            out = download(path="/Notes/a.txt", offset=0, length=4)
        assert "error" not in out
        assert out["content"].startswith("ét")
        assert out["decode_warning"]

    def test_empty_file_is_not_an_error(self) -> None:
        """Range sur un fichier de 0 octet → 416, ce qui n'est pas une panne."""
        with mock_http(bytes_handler(b"", status=416)):
            out = download(path="/Notes/vide.txt")
        assert "error" not in out
        assert out["content"] == "" and out["size"] == 0

    def test_offset_past_eof_is_reported(self) -> None:
        with mock_http(bytes_handler(b"", status=416)):
            out = download(path="/Notes/a.txt", offset=5000)
        assert "error" in out and "5000" in out["error"]

    def test_save_path_writes_file_and_keeps_content_out_of_context(
        self, tmp_path, monkeypatch
    ) -> None:
        monkeypatch.setattr(srv, "_NC_DOWNLOAD_DIR", str(tmp_path))
        with mock_http(bytes_handler(b"\x89PNG binaire")) as calls:
            out = download(path="/Photos/logo.png", save_path="nc/logo.png")
        assert out["ok"] is True
        assert "content" not in out
        assert out["bytes_written"] == 12
        with open(tmp_path / "nc" / "logo.png", "rb") as fh:
            assert fh.read() == b"\x89PNG binaire"
        # Le plafond appliqué est celui du disque, pas celui du contexte.
        assert calls[0]["headers"]["Range"] == f"bytes=0-{srv._MAX_SAVE_BYTES}"

    def test_save_path_traversal_refused_and_writes_nothing(self, tmp_path, monkeypatch) -> None:
        monkeypatch.setattr(srv, "_NC_DOWNLOAD_DIR", str(tmp_path))
        with mock_http(bytes_handler(b"malveillant")):
            out = download(path="/x.txt", save_path="../../.bashrc")
        assert "error" in out
        assert out["download_root"] == str(tmp_path)
        assert not os.path.exists(str(tmp_path.parent.parent / ".bashrc"))

    def test_save_path_absolute_refused(self, tmp_path, monkeypatch) -> None:
        monkeypatch.setattr(srv, "_NC_DOWNLOAD_DIR", str(tmp_path))
        with mock_http(bytes_handler(b"malveillant")):
            out = download(path="/x.txt", save_path=str(tmp_path / "hors" / "cible.txt"))
        assert "error" in out and "relatif" in out["error"]

    def test_save_path_does_not_overwrite_by_default(self, tmp_path, monkeypatch) -> None:
        monkeypatch.setattr(srv, "_NC_DOWNLOAD_DIR", str(tmp_path))
        (tmp_path / "deja.txt").write_bytes(b"original")
        with mock_http(bytes_handler(b"remplacant")):
            out = download(path="/x.txt", save_path="deja.txt")
        assert "error" in out and "existe déjà" in out["error"]
        with open(tmp_path / "deja.txt", "rb") as fh:
            assert fh.read() == b"original"

    def test_save_path_overwrite_when_explicit(self, tmp_path, monkeypatch) -> None:
        monkeypatch.setattr(srv, "_NC_DOWNLOAD_DIR", str(tmp_path))
        (tmp_path / "deja.txt").write_bytes(b"original")
        with mock_http(bytes_handler(b"remplacant")):
            out = download(path="/x.txt", save_path="deja.txt", overwrite=True)
        assert out["ok"] is True
        with open(tmp_path / "deja.txt", "rb") as fh:
            assert fh.read() == b"remplacant"

    def test_save_path_size_cap_leaves_no_file(self, tmp_path, monkeypatch) -> None:
        monkeypatch.setattr(srv, "_NC_DOWNLOAD_DIR", str(tmp_path))
        monkeypatch.setattr(srv, "_MAX_SAVE_BYTES", 10)
        with mock_http(bytes_handler(b"x" * 11)):
            out = download(path="/x.bin", save_path="gros.bin")
        assert "error" in out
        assert not os.path.exists(tmp_path / "gros.bin")
        assert not os.path.exists(str(tmp_path / "gros.bin") + ".part")


class TestSearch:
    def test_docstring_no_longer_claims_full_text(self) -> None:
        """Régression : la docstring promettait « Full-Text Search si activé »."""
        doc = srv.nextcloud_search.__doc__ or ""
        assert "Full-Text" not in doc
        assert "NOM DE FICHIER" in doc
        assert "SOUS-CHAÎNE" in doc
        # Le symptôme d'origine doit rester documenté noir sur blanc.
        assert "transcription" in doc and "Transcripts" in doc

    def _entries(self, count: int, cursor: str | None = None) -> dict:
        data = {
            "entries": [
                {
                    "title": f"f{i}.txt",
                    "subline": "/Notes",
                    "resourceUrl": f"https://nc.example.test/f/{i}",
                    "thumbnailUrl": f"https://nc.example.test/thumb/{i}",
                    "attributes": {"fileId": str(i), "path": f"/Notes/f{i}.txt"},
                }
                for i in range(count)
            ]
        }
        if cursor:
            data["cursor"] = cursor
        return {"ocs": {"meta": {"status": "ok"}, "data": data}}

    def test_requests_the_server_cap(self) -> None:
        with mock_http(json_handler(self._entries(2))) as calls:
            search(query="rapport")
        assert calls[0]["params"]["limit"] == srv._SEARCH_LIMIT

    def test_short_page_is_not_truncated(self) -> None:
        with mock_http(json_handler(self._entries(2))):
            out = search(query="rapport")
        assert out["count"] == 2
        assert out["truncated"] is False
        assert "cursor" not in out

    def test_full_page_signals_truncation_and_exposes_cursor(self) -> None:
        """25 entrées = plafond serveur : la suite existe probablement."""
        with mock_http(json_handler(self._entries(srv._SEARCH_LIMIT, cursor="c42"))):
            out = search(query="rapport")
        assert out["truncated"] is True
        assert out["cursor"] == "c42"

    def test_cursor_is_forwarded(self) -> None:
        with mock_http(json_handler(self._entries(1))) as calls:
            search(query="rapport", cursor="c42")
        assert calls[0]["params"]["cursor"] == "c42"

    def test_results_are_flattened(self) -> None:
        with mock_http(json_handler(self._entries(1))):
            out = search(query="rapport")
        item = out["results"][0]
        assert item["path"] == "/Notes/f0.txt"
        # thumbnailUrl et attributs bruts ne servent à rien au modèle.
        assert "thumbnailUrl" not in item and "attributes" not in item

    def test_path_filter_is_applied_client_side(self) -> None:
        payload = {
            "ocs": {"data": {"entries": [
                {"title": "a.txt", "attributes": {"path": "/Notes/a.txt"}},
                {"title": "b.txt", "attributes": {"path": "/Autre/b.txt"}},
            ]}}
        }
        with mock_http(json_handler(payload)):
            out = search(query="txt", path="/Notes")
        assert [r["title"] for r in out["results"]] == ["a.txt"]
        # Le comptage serveur reste visible : une page entièrement filtrée ne
        # doit pas se lire comme une fin de résultats.
        assert out["server_entries"] == 2
        assert out["path_filter"] == "/Notes"

    def test_path_filter_does_not_match_sibling_prefix(self) -> None:
        payload = {
            "ocs": {"data": {"entries": [
                {"title": "piege.txt", "attributes": {"path": "/NotesBis/piege.txt"}},
            ]}}
        }
        with mock_http(json_handler(payload)):
            out = search(query="txt", path="/Notes")
        assert out["results"] == []

    def test_empty_result_set(self) -> None:
        with mock_http(json_handler({"ocs": {"data": {"entries": []}}})):
            out = search(query="introuvable")
        assert out["count"] == 0 and out["truncated"] is False


# ── upload_file : mode binaire (défaut constaté le 24/09/2026) ───────────
#
# Dépôt des pièces Innov'up ConCyOS : nextcloud_upload_file n'acceptait qu'un
# `content: str`, encodé en UTF-8 avant le PUT. Aucun PDF, ODT, XLSX ou DOCX ne
# pouvait donc être déposé — ni par chemin local, ni en base64 — alors que
# nextcloud_download_file savait déjà écrire un binaire sur disque via
# save_path. Blocage complet, contourné à la main par ~/Sync/inno3 et le client
# de synchronisation, hors de tout contrôle de l'outil.


def upload(**kwargs) -> dict:
    return json.loads(asyncio.run(srv.nextcloud_upload_file(**kwargs)))


def file_info(**kwargs) -> dict:
    return json.loads(asyncio.run(srv.nextcloud_get_file_info(**kwargs)))


# Un vrai PDF minimal : en-tête binaire (%PDF-1.4 + octet 0x80 hors ASCII),
# corps rembourré pour peser quelques Ko, et marqueur de fin.
def _pdf_bytes(size: int = 4096) -> bytes:
    head = b"%PDF-1.4\n%\x80\x81\x82\x83\n"
    tail = b"\n%%EOF\n"
    return head + b"0" * (size - len(head) - len(tail)) + tail


class _FakeNextcloud:
    """Instance Nextcloud en mémoire : PUT stocke, PROPFIND relit.

    Le type MIME est déduit de l'extension, comme le fait Nextcloud pour sa
    colonne mimetype : c'est ce qui permet de comparer `content_type` de part
    et d'autre de l'aller-retour, et pas seulement la taille.
    """

    def __init__(self, files: dict[str, bytes] | None = None) -> None:
        self.files: dict[str, bytes] = dict(files or {})

    @staticmethod
    def _dav_root() -> str:
        return f"/remote.php/dav/files/{srv._NC_USER}"

    def _path(self, url: str) -> str:
        return urllib.parse.unquote(url.split(self._dav_root(), 1)[1])

    def __call__(self, method, url, kwargs):
        path = self._path(url)
        if method == "PUT":
            if kwargs.get("headers", {}).get("If-None-Match") == "*" and path in self.files:
                return 412, b"", {}
            body = kwargs.get("content")
            if not isinstance(body, bytes):
                # Le serveur ne reçoit jamais autre chose que des octets ; un
                # str ici signalerait un encodage laissé à httpx.
                raise AssertionError(f"corps PUT non binaire : {type(body)!r}")
            self.files[path] = body
            return 201, b"", {}
        if method == "PROPFIND":
            if path not in self.files:
                return 404, b"Not Found", {}
            payload = self.files[path]
            ctype = mimetypes.guess_type(path)[0] or "application/octet-stream"
            xml = multistatus(
                _response(
                    f"{self._dav_root()}{path}",
                    path.rsplit("/", 1)[-1],
                    "Thu, 24 Sep 2026 09:00:00 GMT",
                    size=len(payload),
                    ctype=ctype,
                )
            )
            return 207, xml.encode("utf-8"), {"content-type": "application/xml; charset=utf-8"}
        raise AssertionError(f"méthode inattendue : {method}")


class TestUploadHarness:
    """Étalonnage du harnais avant de s'en servir comme preuve."""

    def test_text_roundtrip_is_faithful(self) -> None:
        """Cas sain connu : le texte passait déjà, taille et type conservés."""
        nc = _FakeNextcloud()
        with mock_http(nc):
            out = upload(path="/Notes/todo.txt", content="bonjour")
            info = file_info(path="/Notes/todo.txt")
        assert out["ok"] is True
        assert info["size"] == len("bonjour".encode("utf-8"))
        assert info["content_type"] == "text/plain"

    def test_missing_file_is_a_miss_not_a_success(self) -> None:
        """Cas défectueux connu : sans PUT, la relecture doit échouer."""
        nc = _FakeNextcloud()
        with mock_http(nc):
            info = file_info(path="/Notes/jamais-deposee.pdf")
        assert "error" in info

    def test_text_param_corrupts_binary(self) -> None:
        """Le défaut d'origine, silencieux : un PDF poussé en texte est réencodé.

        C'est la seule voie qu'offrait l'outil pour un binaire, et elle produit
        un fichier plus gros que l'original sans lever la moindre erreur.
        """
        pdf = _pdf_bytes(1024)
        nc = _FakeNextcloud()
        with mock_http(nc):
            out = upload(path="/Depot/piece.pdf", content=pdf.decode("latin-1"))
            info = file_info(path="/Depot/piece.pdf")
        assert out["ok"] is True
        assert nc.files["/Depot/piece.pdf"] != pdf
        assert info["size"] != len(pdf)


class TestUploadBinary:
    """Dépôt d'un binaire : par base64 ou par chemin local."""

    def test_base64_roundtrip_preserves_size_and_type(self) -> None:

        pdf = _pdf_bytes(4096)
        nc = _FakeNextcloud()
        with mock_http(nc):
            out = upload(
                path="/Depot/piece.pdf",
                content_base64=base64.b64encode(pdf).decode("ascii"),
            )
            info = file_info(path="/Depot/piece.pdf")
        assert out["ok"] is True
        assert nc.files["/Depot/piece.pdf"] == pdf
        assert out["size"] == len(pdf) == info["size"]
        assert out["content_type"] == info["content_type"] == "application/pdf"

    def test_local_path_roundtrip_preserves_size_and_type(self, tmp_path, monkeypatch) -> None:
        pdf = _pdf_bytes(4096)
        monkeypatch.setattr(srv, "_NC_UPLOAD_DIR", str(tmp_path))
        (tmp_path / "pieces").mkdir()
        (tmp_path / "pieces" / "piece.pdf").write_bytes(pdf)
        nc = _FakeNextcloud()
        with mock_http(nc):
            out = upload(path="/Depot/piece.pdf", local_path="pieces/piece.pdf")
            info = file_info(path="/Depot/piece.pdf")
        assert nc.files["/Depot/piece.pdf"] == pdf
        assert out["size"] == len(pdf) == info["size"]
        assert out["content_type"] == info["content_type"] == "application/pdf"

    def test_read_after_write_reports_verified_size(self) -> None:

        pdf = _pdf_bytes(2048)
        nc = _FakeNextcloud()
        with mock_http(nc):
            out = upload(
                path="/Depot/piece.pdf", content_base64=base64.b64encode(pdf).decode("ascii")
            )
        assert out["verified_size"] == len(pdf)
        assert out["verified_content_type"] == "application/pdf"
        assert "size_mismatch" not in out

    def test_office_extensions_get_their_mimetype(self) -> None:

        payload = b"PK\x03\x04binaire"
        b64 = base64.b64encode(payload).decode("ascii")
        nc = _FakeNextcloud()
        with mock_http(nc) as calls:
            upload(path="/Depot/budget.xlsx", content_base64=b64)
        put = [c for c in calls if c["method"] == "PUT"][0]
        assert put["headers"]["Content-Type"] == (
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )

    def test_explicit_content_type_wins(self) -> None:

        nc = _FakeNextcloud()
        with mock_http(nc) as calls:
            upload(
                path="/Depot/sans-extension",
                content_base64=base64.b64encode(b"xx").decode("ascii"),
                content_type="application/pdf",
            )
        put = [c for c in calls if c["method"] == "PUT"][0]
        assert put["headers"]["Content-Type"] == "application/pdf"

    def test_unknown_extension_falls_back_to_octet_stream(self) -> None:
        nc = _FakeNextcloud()
        with mock_http(nc) as calls:
            upload(path="/Depot/machin.inconnu", content="x")
        put = [c for c in calls if c["method"] == "PUT"][0]
        assert put["headers"]["Content-Type"] == "application/octet-stream"


class TestUploadCompatibility:
    """L'appel texte historique ne doit pas bouger."""

    def test_positional_call_still_works(self) -> None:
        nc = _FakeNextcloud()
        with mock_http(nc):
            out = json.loads(asyncio.run(srv.nextcloud_upload_file("/Notes/a.txt", "bonjour")))
        assert out == dict(out, ok=True, path="/Notes/a.txt", size=7)
        assert nc.files["/Notes/a.txt"] == b"bonjour"

    def test_utf8_text_is_encoded_as_before(self) -> None:
        nc = _FakeNextcloud()
        with mock_http(nc):
            out = upload(path="/Notes/a.txt", content="éàü")
        assert nc.files["/Notes/a.txt"] == "éàü".encode("utf-8")
        assert out["size"] == 6

    def test_empty_string_writes_an_empty_file(self) -> None:
        """content="" reste « fichier vide », pas « source absente »."""
        nc = _FakeNextcloud()
        with mock_http(nc):
            out = upload(path="/Notes/vide.txt", content="")
        assert out["ok"] is True
        assert nc.files["/Notes/vide.txt"] == b""

    def test_replaces_by_default(self) -> None:
        nc = _FakeNextcloud({"/Notes/a.txt": b"avant"})
        with mock_http(nc):
            out = upload(path="/Notes/a.txt", content="après")
        assert out["ok"] is True
        assert nc.files["/Notes/a.txt"] == "après".encode("utf-8")


class TestUploadSourceExclusivity:
    def test_no_source_is_refused(self) -> None:
        with mock_http(_FakeNextcloud()) as calls:
            out = upload(path="/Depot/x.pdf")
        assert "error" in out
        assert calls == []

    def test_two_sources_are_refused(self) -> None:
        with mock_http(_FakeNextcloud()) as calls:
            out = upload(path="/Depot/x.pdf", content="a", content_base64="YQ==")
        assert "error" in out
        assert "exclusif" in out["error"]
        assert calls == []

    def test_invalid_base64_is_explicit_and_writes_nothing(self) -> None:
        with mock_http(_FakeNextcloud()) as calls:
            out = upload(path="/Depot/x.pdf", content_base64="ceci n'est pas du base64 !!")
        assert "base64" in out["error"]
        assert calls == []

    def test_wrapped_base64_is_accepted(self) -> None:

        pdf = _pdf_bytes(1024)
        wrapped = "\n".join(
            base64.b64encode(pdf).decode("ascii")[i : i + 76]
            for i in range(0, len(base64.b64encode(pdf)), 76)
        )
        nc = _FakeNextcloud()
        with mock_http(nc):
            out = upload(path="/Depot/x.pdf", content_base64=wrapped)
        assert out["ok"] is True
        assert nc.files["/Depot/x.pdf"] == pdf


class TestUploadLocalPathConfinement:
    """local_path est confiné, comme save_path côté téléchargement."""

    def test_absolute_path_is_refused(self, tmp_path, monkeypatch) -> None:
        monkeypatch.setattr(srv, "_NC_UPLOAD_DIR", str(tmp_path))
        with mock_http(_FakeNextcloud()) as calls:
            out = upload(path="/Depot/x", local_path="/etc/shadow")
        assert "relatif" in out["error"]
        assert out["upload_root"] == str(tmp_path)
        assert calls == []

    @pytest.mark.parametrize("evil", ["../../.ssh/id_rsa", "sub/../../escape.txt", "~/.bashrc"])
    def test_traversal_is_refused(self, evil, tmp_path, monkeypatch) -> None:
        monkeypatch.setattr(srv, "_NC_UPLOAD_DIR", str(tmp_path))
        with mock_http(_FakeNextcloud()) as calls:
            out = upload(path="/Depot/x", local_path=evil)
        assert "error" in out
        assert calls == []

    def test_symlink_escaping_root_is_refused(self, tmp_path, monkeypatch) -> None:
        root = tmp_path / "up"
        root.mkdir()
        outside = tmp_path / "outside"
        outside.mkdir()
        (outside / "secret.txt").write_bytes(b"secret")
        os.symlink(str(outside), str(root / "escape"))
        monkeypatch.setattr(srv, "_NC_UPLOAD_DIR", str(root))
        with mock_http(_FakeNextcloud()) as calls:
            out = upload(path="/Depot/x", local_path="escape/secret.txt")
        assert "sort de la racine" in out["error"]
        assert calls == []

    def test_missing_file_is_explicit(self, tmp_path, monkeypatch) -> None:
        monkeypatch.setattr(srv, "_NC_UPLOAD_DIR", str(tmp_path))
        with mock_http(_FakeNextcloud()) as calls:
            out = upload(path="/Depot/x", local_path="absent.pdf")
        assert "introuvable" in out["error"]
        assert calls == []

    def test_directory_is_not_a_file(self, tmp_path, monkeypatch) -> None:
        monkeypatch.setattr(srv, "_NC_UPLOAD_DIR", str(tmp_path))
        (tmp_path / "dossier").mkdir()
        with mock_http(_FakeNextcloud()) as calls:
            out = upload(path="/Depot/x", local_path="dossier")
        assert "error" in out
        assert calls == []


class TestUploadSizeCap:
    def test_local_file_over_cap_is_refused_before_any_call(
        self, tmp_path, monkeypatch
    ) -> None:
        monkeypatch.setattr(srv, "_NC_UPLOAD_DIR", str(tmp_path))
        monkeypatch.setattr(srv, "_MAX_UPLOAD_BYTES", 10)
        (tmp_path / "gros.bin").write_bytes(b"x" * 11)
        with mock_http(_FakeNextcloud()) as calls:
            out = upload(path="/Depot/gros.bin", local_path="gros.bin")
        assert "plafond" in out["error"]
        assert calls == []

    def test_base64_over_cap_is_refused(self, monkeypatch) -> None:

        monkeypatch.setattr(srv, "_MAX_UPLOAD_BYTES", 10)
        with mock_http(_FakeNextcloud()) as calls:
            out = upload(
                path="/Depot/gros.bin", content_base64=base64.b64encode(b"x" * 64).decode("ascii")
            )
        assert "plafond" in out["error"]
        assert calls == []

    def test_text_over_cap_is_refused(self, monkeypatch) -> None:
        monkeypatch.setattr(srv, "_MAX_UPLOAD_BYTES", 10)
        with mock_http(_FakeNextcloud()) as calls:
            out = upload(path="/Notes/gros.txt", content="x" * 11)
        assert "plafond" in out["error"]
        assert calls == []

    def test_docstring_states_cap_and_root(self) -> None:
        doc = srv.nextcloud_upload_file.__doc__ or ""
        assert "NEXTCLOUD_MAX_UPLOAD_BYTES" in doc
        assert "NEXTCLOUD_UPLOAD_DIR" in doc
        assert "25 Mo" in doc


class TestUploadOverwrite:
    def test_overwrite_false_refuses_existing_and_keeps_content(self) -> None:
        nc = _FakeNextcloud({"/Depot/piece.pdf": b"deja-la"})
        with mock_http(nc):
            out = upload(path="/Depot/piece.pdf", content="neuf", overwrite=False)
        assert "existe déjà" in out["error"]
        assert nc.files["/Depot/piece.pdf"] == b"deja-la"

    def test_overwrite_false_sends_if_none_match(self) -> None:
        """Garde atomique côté serveur, en plus du contrôle préalable."""
        nc = _FakeNextcloud()
        with mock_http(nc) as calls:
            upload(path="/Depot/neuf.pdf", content="x", overwrite=False)
        put = [c for c in calls if c["method"] == "PUT"][0]
        assert put["headers"]["If-None-Match"] == "*"

    def test_overwrite_true_does_not_send_if_none_match(self) -> None:
        nc = _FakeNextcloud({"/Depot/a.txt": b"avant"})
        with mock_http(nc) as calls:
            upload(path="/Depot/a.txt", content="après")
        put = [c for c in calls if c["method"] == "PUT"][0]
        assert "If-None-Match" not in put["headers"]


class TestUploadServerErrors:
    def test_missing_parent_folder_is_named(self) -> None:
        def handler(method, url, kwargs):
            return 409, b"Conflict", {}

        with mock_http(handler):
            out = upload(path="/Absent/piece.pdf", content="x")
        assert "dossier parent" in out["error"]

    def test_verification_failure_does_not_claim_success_blindly(self, monkeypatch) -> None:
        """PUT accepté mais relecture impossible : l'écart doit se voir."""

        def handler(method, url, kwargs):
            if method == "PUT":
                return 201, b"", {}
            return 500, b"boom", {}

        monkeypatch.setattr(srv, "_RETRY_DELAY", 0)
        with mock_http(handler):
            out = upload(path="/Depot/piece.pdf", content="x")
        assert out["ok"] is True
        assert "verify_warning" in out
        assert "verified_size" not in out

    def test_size_mismatch_is_flagged(self) -> None:
        """Le serveur annonce une autre taille que celle envoyée."""

        def handler(method, url, kwargs):
            if method == "PUT":
                return 201, b"", {}
            xml = multistatus(
                _response(
                    f"{_ROOT}/Depot/piece.pdf",
                    "piece.pdf",
                    "Thu, 24 Sep 2026 09:00:00 GMT",
                    size=1,
                    ctype="application/pdf",
                )
            )
            return 207, xml.encode("utf-8"), {"content-type": "application/xml"}

        with mock_http(handler):
            out = upload(path="/Depot/piece.pdf", content="bonjour")
        assert out["size_mismatch"] is True
        assert out["verified_size"] == 1


class TestUploadRetrySafety:
    """Ce qui arrive quand la réponse d'un dépôt se perd.

    Le scénario n'a pas changé — le PUT a écrit, la réponse s'est perdue, un
    second essai voit 412 — mais le connecteur ne produit plus ce second essai
    lui-même : depuis le portage du contrat d'erreur, une écriture n'est jamais
    rejouée automatiquement une fois la requête partie. Le 412 reste donc
    atteignable, par un rejeu décidé par l'appelant, et son message garde sa
    valeur ; c'est ce que vérifie le second test.
    """

    def test_le_put_nest_pas_rejoue_et_lerreur_dit_letat(self) -> None:
        state = {"puts": 0}

        def handler(method, url, kwargs):
            if method == "PUT":
                state["puts"] += 1
                raise httpx.ReadTimeout("coupure après écriture", request=None)
            return 404, b"Not Found", {}

        with patch.object(srv, "_RETRY_DELAY", 0), mock_http(handler):
            out = upload(path="/Depot/piece.pdf", content="x", overwrite=False)
        assert state["puts"] == 1, "rejouer un PUT dont la réponse est perdue écrase"
        assert "inconnu" in out["error"]
        assert "relire" in out["error"]

    def test_412_points_to_verification(self) -> None:
        """Le message ne doit pas se lire comme « rien n'a été déposé »."""

        def handler(method, url, kwargs):
            if method == "PUT":
                return 412, b"", {}
            return 404, b"Not Found", {}

        with patch.object(srv, "_RETRY_DELAY", 0), mock_http(handler):
            out = upload(path="/Depot/piece.pdf", content="x", overwrite=False)
        assert "existe déjà" in out["error"]
        assert "nextcloud_get_file_info" in out["error"]

# ── upload_file : ce que la vérification en production a révélé ──────────────
#
# Trois constats du 25/09/2026, sur l'instance réelle, qu'aucun test mocké ne
# pouvait donner :
#
# 1. Un base64 recopié dans la réponse d'un modèle avait perdu 16 caractères
#    (un quantum entier, 12 octets) au milieu d'une zone répétitive. Il restait
#    un multiple de 4, donc `validate=True` l'a accepté ; le fichier déposé
#    faisait 2748 octets au lieu de 2760, et `size`, `verified_size` et le
#    serveur étaient tous d'accord — sur le contenu envoyé, pas sur le contenu
#    voulu. Seul un condensat calculé à la source traverse la transcription.
# 2. Nextcloud répond 404 (Sabre\DAV\Exception\NotFound), et non 409, sur un
#    dossier parent absent.
# 3. Les erreurs WebDAV sont du XML : tronquées à 200 caractères, elles
#    coupaient le message au milieu.


class TestUploadDigest:
    def test_response_always_carries_the_digest(self) -> None:
        pdf = _pdf_bytes(2048)
        nc = _FakeNextcloud()
        with mock_http(nc):
            out = upload(path="/Depot/piece.pdf", content_base64=base64.b64encode(pdf).decode("ascii"))
        assert out["sha256"] == hashlib.sha256(pdf).hexdigest()

    def test_declared_digest_matching_is_accepted(self) -> None:
        pdf = _pdf_bytes(2048)
        nc = _FakeNextcloud()
        with mock_http(nc):
            out = upload(
                path="/Depot/piece.pdf",
                content_base64=base64.b64encode(pdf).decode("ascii"),
                sha256=hashlib.sha256(pdf).hexdigest().upper(),
            )
        assert out["ok"] is True
        assert nc.files["/Depot/piece.pdf"] == pdf

    def test_base64_amputee_dun_quantum_est_prise_par_le_condensat(self) -> None:
        """Le défaut réel du 25/09/2026, en laboratoire.

        On retire 4 caractères de base64 : la chaîne reste décodable, la taille
        reste cohérente de bout en bout, et rien d'autre que le condensat ne
        peut le voir.
        """
        pdf = _pdf_bytes(2048)
        b64 = base64.b64encode(pdf).decode("ascii")
        ampute = b64[:400] + b64[404:]
        # le tronçon reste du base64 valide, et décode sans erreur
        decoded = base64.b64decode("".join(ampute.split()), validate=True)
        assert len(decoded) == len(pdf) - 3

        nc = _FakeNextcloud()
        with mock_http(nc) as calls:
            out = upload(
                path="/Depot/piece.pdf",
                content_base64=ampute,
                sha256=hashlib.sha256(pdf).hexdigest(),
            )
        assert "condensat différent" in out["error"]
        assert "rien n'a été déposé" in out["error"]
        assert calls == []
        assert nc.files == {}

    def test_sans_condensat_la_troncature_passe_inapercue(self) -> None:
        """Témoin : sans sha256, le dépôt tronqué se présente comme un succès."""
        pdf = _pdf_bytes(2048)
        b64 = base64.b64encode(pdf).decode("ascii")
        ampute = b64[:400] + b64[404:]
        nc = _FakeNextcloud()
        with mock_http(nc):
            out = upload(path="/Depot/piece.pdf", content_base64=ampute)
        assert out["ok"] is True
        assert out["size"] == out["verified_size"] == len(pdf) - 3
        assert nc.files["/Depot/piece.pdf"] != pdf

    @pytest.mark.parametrize("bad", ["abc", "z" * 64, "0" * 63])
    def test_malformed_digest_is_refused_before_any_call(self, bad) -> None:
        with mock_http(_FakeNextcloud()) as calls:
            out = upload(path="/Depot/piece.pdf", content="x", sha256=bad)
        assert "64 caractères" in out["error"]
        assert calls == []


class TestUploadMissingParent:
    def test_404_names_the_parent_folder(self) -> None:
        """Nextcloud répond 404 sur parent absent, pas 409 (vérifié en prod)."""
        body = (
            b'<?xml version="1.0" encoding="utf-8"?>\n'
            b'<d:error xmlns:d="DAV:" xmlns:s="http://sabredav.org/ns">\n'
            b"  <s:exception>Sabre\\DAV\\Exception\\NotFound</s:exception>\n"
            b"  <s:message>File with name /Depot/absent could not be located</s:message>\n"
            b"</d:error>"
        )

        def handler(method, url, kwargs):
            return 404, body, {"content-type": "application/xml"}

        with mock_http(handler):
            out = upload(path="/Depot/absent/piece.pdf", content="x")
        assert "le dossier parent /Depot/absent n'existe pas" in out["error"]
        assert "nextcloud_create_folder" in out["error"]


class TestDavErrorMessage:
    def test_sabre_message_is_extracted_and_unescaped(self) -> None:
        body = (
            '<?xml version="1.0"?>\n<d:error xmlns:d="DAV:" xmlns:s="http://sabredav.org/ns">\n'
            "  <s:exception>Sabre\\DAV\\Exception\\Conflict</s:exception>\n"
            "  <s:message>Files &amp; folders\n  cannot be nested</s:message>\n</d:error>"
        )
        assert srv._dav_error_message(body) == "Files & folders cannot be nested"

    def test_non_dav_body_returns_none(self) -> None:
        assert srv._dav_error_message("boom") is None
        assert srv._dav_error_message("") is None

    def test_format_error_uses_it_instead_of_raw_xml(self) -> None:
        body = (
            '<?xml version="1.0"?><d:error xmlns:s="http://sabredav.org/ns">'
            "<s:exception>Sabre\\DAV\\Exception\\NotFound</s:exception>"
            "<s:message>File with name /x could not be located</s:message></d:error>"
        )
        exc = httpx.HTTPStatusError(
            "404",
            request=httpx.Request("PUT", "https://nc.example.test/x"),
            response=httpx.Response(404, content=body.encode("utf-8"),
                                    headers={"content-type": "application/xml"}),
        )
        msg = srv._format_error(exc)
        assert msg == "Erreur HTTP 404 : File with name /x could not be located"
        assert "<?xml" not in msg
