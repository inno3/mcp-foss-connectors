# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 inno³ and contributors
"""Téléchargement Nextcloud : par flux, avec reprise, et jamais incomplet en silence.

Constaté en réel le 05/11/2026 sur un PDF de 512 219 octets : chaque GET rendait
206, puis `RemoteProtocolError (received 32768 bytes, expected 512219)`. Le corps
étant bufferisé, les 32 768 octets reçus étaient perdus et les trois tentatives
repartaient de zéro pour le même résultat.

Ces tests passent par un vrai socket local plutôt que par un faux client : c'est
h11 qui lève l'exception, et une troncature qu'il ne voit pas (corps délimité
par la fermeture de la connexion) reste un cas réel, pas une simulation.
"""

import asyncio
import hashlib
import json
import os
import random
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import AsyncMock

import pytest

import nextcloud_mcp.server as srv

PDF_SIZE = 512_219
PDF = b"%PDF-1.7\n" + random.Random(42).randbytes(PDF_SIZE - 16) + b"\n%%EOF\n"
REMOTE = "/Finances/rapport.pdf"


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def coupure_a_32768(_numero: int, start: int, _ranged: bool) -> int:
    """Le défaut réel : aucun octet au-delà de 32 768 ne sort, quelle que soit la plage."""
    return max(0, 32768 - start)


class _State:
    """Scénario du faux Nextcloud, et journal de ce qu'il a reçu."""

    def __init__(self) -> None:
        self.payload = PDF
        self.cut = lambda _numero, _start, _ranged: None
        self.honor_range = True
        self.close_framing = False
        self.statuses: dict[int, int] = {}
        self.etags = ['"etag-1"']
        self.webdav_size: int | None = None
        self.propfind_status = 207
        self.is_directory = False
        self.no_content_type = False
        self.gets: list[dict] = []
        self.propfinds = 0


class _Dav(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args) -> None:
        pass

    def do_PROPFIND(self) -> None:  # noqa: N802
        state = self.server.state
        self.rfile.read(int(self.headers.get("Content-Length") or 0))
        state.propfinds += 1
        if state.propfind_status != 207:
            self._respond(state.propfind_status, b"indisponible")
            return
        size = len(state.payload) if state.webdav_size is None else state.webdav_size
        resource = "<d:collection/>" if state.is_directory else ""
        body = (
            '<?xml version="1.0"?><d:multistatus xmlns:d="DAV:"><d:response>'
            f"<d:href>/remote.php/dav/files/alice{REMOTE}</d:href>"
            "<d:propstat><d:prop>"
            f"<d:getcontentlength>{size}</d:getcontentlength>"
            f"<d:resourcetype>{resource}</d:resourcetype>"
            "</d:prop><d:status>HTTP/1.1 200 OK</d:status></d:propstat>"
            "</d:response></d:multistatus>"
        ).encode()
        self._respond(207, body, {"Content-Type": "application/xml; charset=utf-8"})

    def do_GET(self) -> None:  # noqa: N802
        state = self.server.state
        numero = len(state.gets)
        range_header = self.headers.get("Range")
        state.gets.append({
            "range": range_header,
            "accept_encoding": self.headers.get("Accept-Encoding"),
        })
        if numero in state.statuses:
            self._respond(state.statuses[numero], b"refus")
            return

        total = len(state.payload)
        status, start, end = 200, 0, total - 1
        if range_header and state.honor_range:
            first, last = range_header.removeprefix("bytes=").split("-")
            start = int(first)
            if start >= total:
                self._respond(416, b"", {"Content-Range": f"bytes */{total}"})
                return
            end = min(int(last), total - 1) if last else total - 1
            status = 206

        part = state.payload[start:end + 1]
        self.send_response(status)
        if not state.no_content_type:
            self.send_header("Content-Type", "application/pdf")
        self.send_header("ETag", state.etags[min(numero, len(state.etags) - 1)])
        if status == 206:
            self.send_header("Content-Range", f"bytes {start}-{end}/{total}")
        if state.close_framing:
            self.send_header("Connection", "close")
            self.close_connection = True
        else:
            self.send_header("Content-Length", str(len(part)))
        self.end_headers()

        allowed = state.cut(numero, start, bool(range_header))
        if allowed is None or allowed >= len(part):
            self.wfile.write(part)
            self.wfile.flush()
            return
        self.wfile.write(part[:allowed])
        self.wfile.flush()
        self.close_connection = True
        self.connection.shutdown(socket.SHUT_RDWR)

    def _respond(self, status: int, body: bytes, headers: dict | None = None) -> None:
        self.send_response(status)
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


@pytest.fixture
def dav(monkeypatch, tmp_path):
    state = _State()
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), _Dav)
    httpd.daemon_threads = True
    httpd.state = state
    thread = threading.Thread(target=httpd.serve_forever, args=(0.01,), daemon=True)
    thread.start()
    monkeypatch.setattr(srv, "_NC_URL", f"http://127.0.0.1:{httpd.server_address[1]}")
    monkeypatch.setattr(srv, "_NC_USER", "alice")
    monkeypatch.setattr(srv, "_NC_PASSWORD", "secret")
    monkeypatch.setattr(srv, "_NC_DOWNLOAD_DIR", str(tmp_path / "dl"))
    monkeypatch.setattr(srv, "_NC_SAVE_DIRS", [], raising=False)
    monkeypatch.setattr(srv, "_RETRY_DELAY", 0)
    yield state
    httpd.shutdown()
    httpd.server_close()
    thread.join(timeout=5)


def fetch(**kwargs) -> dict:
    kwargs.setdefault("path", REMOTE)
    return json.loads(asyncio.run(srv.nextcloud_download_file(**kwargs)))


def files_under(directory) -> list[str]:
    return sorted(
        os.path.relpath(os.path.join(root, name), directory)
        for root, _dirs, names in os.walk(directory)
        for name in names
    )


class TestPdf512Ko:
    """Le critère d'acceptation : le PDF arrive entier, ou l'erreur le dit."""

    def test_coupure_unique_reprise_par_range(self, dav, tmp_path) -> None:
        dav.cut = lambda numero, _start, _ranged: 32768 if numero == 0 else None
        out = fetch(save_path="rapport.pdf")
        assert out["ok"] is True, out
        saved = (tmp_path / "dl" / "rapport.pdf").read_bytes()
        assert len(saved) == PDF_SIZE
        assert sha256(saved) == sha256(PDF)
        assert out["bytes_written"] == PDF_SIZE
        assert out["partial"] is False
        assert out["resumes"] == 1
        assert dav.gets[1]["range"].startswith("bytes=32768-")
        assert out["size_verified"] is True
        assert out["size_checks"] == {
            "content_length": PDF_SIZE,
            "content_range_total": PDF_SIZE,
            "webdav_getcontentlength": PDF_SIZE,
        }
        assert files_under(tmp_path / "dl") == ["rapport.pdf"]

    def test_coupure_tous_les_32_ko_progresse_sans_delai(self, dav, tmp_path, monkeypatch) -> None:
        """Chaque requête progresse : attendre ne ferait que risquer le délai de l'outil."""
        dav.cut = lambda _numero, _start, _ranged: 32768
        monkeypatch.setattr(srv.asyncio, "sleep", AsyncMock(side_effect=AssertionError("attente")))
        out = fetch(save_path="rapport.pdf")
        assert out["ok"] is True, out
        assert sha256((tmp_path / "dl" / "rapport.pdf").read_bytes()) == sha256(PDF)
        assert len(dav.gets) == 16

    def test_plage_illisible_mais_get_sans_range_entier(self, dav, tmp_path) -> None:
        """Si seule la lecture par plage est cassée, un GET simple peut encore aboutir."""
        dav.cut = lambda numero, start, ranged: coupure_a_32768(numero, start, ranged) if ranged else None
        out = fetch(save_path="rapport.pdf")
        assert out["ok"] is True, out
        assert sha256((tmp_path / "dl" / "rapport.pdf").read_bytes()) == sha256(PDF)
        assert dav.gets[-1]["range"] is None
        assert out["range_fallback"] is True

    def test_octet_32768_illisible_partout_erreur_explicite(self, dav, tmp_path) -> None:
        dav.cut = coupure_a_32768
        out = fetch(save_path="rapport.pdf")
        assert "ok" not in out
        assert out["incomplete"] is True
        assert out["bytes_received"] == 32768
        assert out["expected_size"] == PDF_SIZE
        assert "32768" in out["error"] and str(PDF_SIZE) in out["error"]
        assert "serveur" in out["error"]
        assert out["last_error"].startswith("RemoteProtocolError")
        assert files_under(tmp_path / "dl") == []
        assert len(dav.gets) <= 2 * (srv._MAX_RETRIES + 1) + 1

    def test_echec_laisse_intact_le_fichier_existant(self, dav, tmp_path) -> None:
        (tmp_path / "dl").mkdir()
        (tmp_path / "dl" / "rapport.pdf").write_bytes(b"version saine")
        dav.cut = coupure_a_32768
        out = fetch(save_path="rapport.pdf", overwrite=True)
        assert out["incomplete"] is True
        assert (tmp_path / "dl" / "rapport.pdf").read_bytes() == b"version saine"
        assert files_under(tmp_path / "dl") == ["rapport.pdf"]

    def test_lecture_en_memoire_reprend_aussi(self, dav) -> None:
        dav.payload = ("é" * 25_000).encode("utf-8")
        dav.cut = lambda numero, _start, _ranged: 32767 if numero == 0 else None
        out = fetch()
        assert out["content"] == "é" * 25_000
        assert out["size"] == 50_000


class TestControleDeTaille:
    def test_getcontentlength_different_refuse_le_fichier(self, dav, tmp_path) -> None:
        dav.webdav_size = 600_000
        out = fetch(save_path="rapport.pdf")
        assert out["incomplete"] is True
        assert out["expected_size"] == 600_000
        assert out["size_checks"]["webdav_getcontentlength"] == 600_000
        assert "getcontentlength" in out["error"]
        assert files_under(tmp_path / "dl") == []

    def test_troncature_sans_erreur_ni_content_length(self, dav, tmp_path) -> None:
        """Corps délimité par la fermeture : h11 ne voit rien, seul PROPFIND le révèle."""
        dav.honor_range = False
        dav.close_framing = True
        dav.cut = lambda _numero, _start, _ranged: 100_000
        out = fetch(save_path="rapport.pdf")
        assert out["incomplete"] is True
        assert out["bytes_received"] == 100_000
        assert out["expected_size"] == PDF_SIZE
        assert files_under(tmp_path / "dl") == []

    def test_troncature_sans_erreur_reprise_si_total_connu(self, dav, tmp_path) -> None:
        dav.close_framing = True
        dav.cut = lambda numero, _start, _ranged: 100_000 if numero == 0 else None
        out = fetch(save_path="rapport.pdf")
        assert out["ok"] is True, out
        assert sha256((tmp_path / "dl" / "rapport.pdf").read_bytes()) == sha256(PDF)
        assert out["resumes"] == 1

    def test_propfind_indisponible_est_signale_sans_echouer(self, dav, tmp_path) -> None:
        dav.propfind_status = 404
        out = fetch(save_path="rapport.pdf")
        assert out["ok"] is True, out
        assert out["size_verified"] is True
        assert "webdav_getcontentlength" not in out["size_checks"]
        assert "PROPFIND" in out["warning"]

    def test_rien_de_verifiable_est_dit(self, dav) -> None:
        dav.honor_range = False
        dav.close_framing = True
        dav.propfind_status = 404
        dav.payload = b"sans aucun repere"
        out = fetch()
        assert out["content"] == "sans aucun repere"
        assert out["size_verified"] is False
        assert "warning" in out

    def test_chemin_distant_qui_est_un_dossier(self, dav, tmp_path) -> None:
        dav.is_directory = True
        out = fetch(save_path="dossier.bin")
        assert "dossier" in out["error"]
        assert files_under(tmp_path / "dl") == []


class TestReprise:
    def test_identite_demandee_pour_que_les_tailles_soient_comparables(self, dav) -> None:
        dav.payload = b"bonjour"
        fetch()
        assert dav.gets[0]["accept_encoding"] == "identity"

    def test_fichier_modifie_pendant_la_reprise(self, dav, tmp_path) -> None:
        dav.etags = ['"etag-1"', '"etag-2"']
        dav.cut = lambda numero, _start, _ranged: 32768 if numero == 0 else None
        out = fetch(save_path="rapport.pdf")
        assert out["incomplete"] is True
        assert "modifié" in out["error"]
        assert files_under(tmp_path / "dl") == []

    def test_range_ignore_saute_les_octets_deja_recus(self, dav, tmp_path) -> None:
        dav.honor_range = False
        dav.cut = lambda numero, _start, _ranged: 32768 if numero == 0 else None
        out = fetch(save_path="rapport.pdf")
        assert out["ok"] is True, out
        assert sha256((tmp_path / "dl" / "rapport.pdf").read_bytes()) == sha256(PDF)

    def test_range_ignore_ne_rend_pas_le_debut_du_fichier_pour_un_offset(self, dav) -> None:
        dav.honor_range = False
        dav.payload = b"0123456789"
        out = fetch(offset=4, length=3)
        assert out["content"] == "456"

    def test_plage_coupee_reprend_et_s_arrete_a_la_longueur(self, dav, tmp_path) -> None:
        dav.cut = lambda numero, _start, _ranged: 32768 if numero == 0 else None
        out = fetch(save_path="plage.bin", offset=1000, length=100_000)
        assert out["ok"] is True, out
        assert (tmp_path / "dl" / "plage.bin").read_bytes() == PDF[1000:101_000]
        assert out["bytes_written"] == 100_000
        assert out["partial"] is True
        assert out["total_size"] == PDF_SIZE
        assert dav.propfinds == 0

    def test_reprise_sans_content_type_garde_le_content_length_initial(self, dav, tmp_path) -> None:
        """Le Content-Length contrôlé est celui de la lecture, pas celui du dernier segment."""
        dav.no_content_type = True
        dav.cut = lambda numero, _start, _ranged: 32768 if numero == 0 else None
        out = fetch(save_path="rapport.pdf")
        assert out["ok"] is True, out
        assert out["size_checks"]["content_length"] == PDF_SIZE

    def test_nom_d_utilisateur_inconnu_reste_refuse(self, dav, tmp_path) -> None:
        out = fetch(save_path="~compte-inexistant-zz/a.bin")
        assert "relatif" in out["error"]
        assert dav.gets == []

    def test_erreur_serveur_entre_deux_segments_est_rejouee(self, dav, tmp_path) -> None:
        dav.cut = lambda numero, _start, _ranged: 32768 if numero == 0 else None
        dav.statuses = {1: 503}
        out = fetch(save_path="rapport.pdf")
        assert out["ok"] is True, out
        assert sha256((tmp_path / "dl" / "rapport.pdf").read_bytes()) == sha256(PDF)

    def test_416_en_reprise_n_est_pas_un_fichier_vide(self, dav, tmp_path) -> None:
        dav.cut = lambda numero, _start, _ranged: 32768 if numero == 0 else None
        dav.statuses = {1: 416}
        out = fetch(save_path="rapport.pdf")
        assert out["incomplete"] is True
        assert out["bytes_received"] == 32768
        assert files_under(tmp_path / "dl") == []

    def test_erreur_serveur_persistante_reste_une_erreur_http(self, dav) -> None:
        dav.statuses = {0: 503, 1: 503, 2: 503}
        out = fetch(save_path="rapport.pdf")
        assert "503" in out["error"]
        assert "incomplete" not in out
        assert len(dav.gets) == srv._MAX_RETRIES + 1

    def test_connexion_impossible_reste_une_erreur_reseau(self, dav, monkeypatch) -> None:
        """Rien n'a répondu : ni repli sans Range, ni « fichier incomplet »."""
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        monkeypatch.setattr(srv, "_NC_URL", f"http://127.0.0.1:{port}")
        out = fetch(save_path="rapport.pdf")
        assert out["error"].startswith("Erreur réseau ConnectError")
        assert f"{srv._MAX_RETRIES + 1} tentatives" in out["error"]
        assert "incomplete" not in out

    def test_404_initial_reste_une_erreur_http(self, dav) -> None:
        dav.statuses = {0: 404}
        out = fetch(save_path="rapport.pdf")
        assert "404" in out["error"]
        assert "incomplete" not in out

    def test_plafond_refuse_des_les_en_tetes(self, dav, tmp_path, monkeypatch) -> None:
        """Le total annoncé suffit : inutile de lire le plafond entier pour refuser."""
        monkeypatch.setattr(srv, "_MAX_SAVE_BYTES", 1000)
        out = fetch(save_path="rapport.pdf")
        assert "trop volumineux" in out["error"]
        assert out["size"] == PDF_SIZE
        assert files_under(tmp_path / "dl") == []


class TestSavePathDossiersAutorises:
    def test_absolu_sous_la_racine_de_telechargement(self, dav, tmp_path) -> None:
        dav.payload = b"contenu"
        out = fetch(save_path=str(tmp_path / "dl" / "sous" / "a.bin"))
        assert out["ok"] is True, out
        assert (tmp_path / "dl" / "sous" / "a.bin").read_bytes() == b"contenu"

    def test_absolu_sous_un_dossier_autorise(self, dav, tmp_path, monkeypatch) -> None:
        dav.payload = b"contenu"
        monkeypatch.setattr(srv, "_NC_SAVE_DIRS", [str(tmp_path / "mission")])
        out = fetch(save_path=str(tmp_path / "mission" / "a.bin"))
        assert out["ok"] is True, out
        assert out["saved_to"] == os.path.join(os.path.realpath(tmp_path), "mission", "a.bin")
        assert (tmp_path / "mission" / "a.bin").read_bytes() == b"contenu"

    def test_tilde_sous_un_dossier_autorise(self, dav, tmp_path, monkeypatch) -> None:
        dav.payload = b"contenu"
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.setattr(srv, "_NC_SAVE_DIRS", ["~/mission"])
        out = fetch(save_path="~/mission/a.bin")
        assert out["ok"] is True, out
        assert (tmp_path / "mission" / "a.bin").read_bytes() == b"contenu"

    def test_relatif_reste_sous_la_racine_de_telechargement(self, dav, tmp_path, monkeypatch) -> None:
        dav.payload = b"contenu"
        monkeypatch.setattr(srv, "_NC_SAVE_DIRS", [str(tmp_path / "mission")])
        out = fetch(save_path="a.bin")
        assert out["ok"] is True, out
        assert (tmp_path / "dl" / "a.bin").read_bytes() == b"contenu"

    def test_hors_racines_refuse_sans_reseau(self, dav, tmp_path, monkeypatch) -> None:
        monkeypatch.setattr(srv, "_NC_SAVE_DIRS", [str(tmp_path / "mission")])
        out = fetch(save_path=str(tmp_path / "ailleurs" / "a.bin"))
        assert "NEXTCLOUD_SAVE_DIRS" in out["error"]
        assert out["allowed_roots"] == [
            os.path.join(os.path.realpath(tmp_path), "dl"),
            os.path.join(os.path.realpath(tmp_path), "mission"),
        ]
        assert out["download_root"] == str(tmp_path / "dl")
        assert dav.gets == []
        assert not (tmp_path / "ailleurs").exists()

    def test_prefixe_voisin_d_une_racine_refuse(self, dav, tmp_path) -> None:
        out = fetch(save_path=str(tmp_path / "dl-voisin" / "a.bin"))
        assert "error" in out
        assert not (tmp_path / "dl-voisin").exists()

    def test_remontee_refusee_meme_sous_une_racine(self, dav, tmp_path, monkeypatch) -> None:
        monkeypatch.setattr(srv, "_NC_SAVE_DIRS", [str(tmp_path / "mission")])
        out = fetch(save_path=str(tmp_path / "mission") + "/../dl/a.bin")
        assert "'..'" in out["error"]

    def test_lien_sortant_d_un_dossier_autorise_refuse(self, dav, tmp_path, monkeypatch) -> None:
        (tmp_path / "mission").mkdir()
        (tmp_path / "dehors").mkdir()
        os.symlink(tmp_path / "dehors", tmp_path / "mission" / "fuite")
        monkeypatch.setattr(srv, "_NC_SAVE_DIRS", [str(tmp_path / "mission")])
        out = fetch(save_path=str(tmp_path / "mission" / "fuite" / "a.bin"))
        assert "error" in out
        assert list((tmp_path / "dehors").iterdir()) == []

    def test_racine_atteinte_par_un_lien_reste_acceptee(self, dav, tmp_path, monkeypatch) -> None:
        dav.payload = b"contenu"
        (tmp_path / "reel").mkdir()
        os.symlink(tmp_path / "reel", tmp_path / "alias")
        monkeypatch.setattr(srv, "_NC_SAVE_DIRS", [str(tmp_path / "alias")])
        out = fetch(save_path=str(tmp_path / "alias" / "a.bin"))
        assert out["ok"] is True, out
        assert (tmp_path / "reel" / "a.bin").read_bytes() == b"contenu"

    @pytest.mark.parametrize("slash", ["", "/"])
    def test_dossier_existant_reprend_le_nom_distant(self, dav, tmp_path, monkeypatch, slash) -> None:
        dav.payload = b"contenu"
        (tmp_path / "mission").mkdir()
        monkeypatch.setattr(srv, "_NC_SAVE_DIRS", [str(tmp_path / "mission")])
        out = fetch(save_path=str(tmp_path / "mission") + slash)
        assert out["ok"] is True, out
        assert (tmp_path / "mission" / "rapport.pdf").read_bytes() == b"contenu"

    def test_racine_absente_est_traitee_comme_un_dossier(self, dav, tmp_path, monkeypatch) -> None:
        """Sans quoi le fichier prendrait le nom de la racine, et son temporaire en sortirait."""
        dav.payload = b"contenu"
        monkeypatch.setattr(srv, "_NC_SAVE_DIRS", [str(tmp_path / "mission")])
        out = fetch(save_path=str(tmp_path / "mission"))
        assert out["ok"] is True, out
        assert (tmp_path / "mission" / "rapport.pdf").read_bytes() == b"contenu"

    def test_dossier_a_creer_designe_par_le_slash_final(self, dav, tmp_path) -> None:
        dav.payload = b"contenu"
        out = fetch(save_path="nouveau/")
        assert out["ok"] is True, out
        assert (tmp_path / "dl" / "nouveau" / "rapport.pdf").read_bytes() == b"contenu"

    def test_existant_refuse_avant_tout_reseau(self, dav, tmp_path) -> None:
        (tmp_path / "dl").mkdir()
        (tmp_path / "dl" / "a.bin").write_bytes(b"original")
        out = fetch(save_path="a.bin")
        assert "existe déjà" in out["error"]
        assert dav.gets == []
        assert (tmp_path / "dl" / "a.bin").read_bytes() == b"original"

    def test_envoi_reste_confine_a_sa_propre_racine(self, tmp_path, monkeypatch) -> None:
        """Élargir l'écriture ne doit rien élargir côté lecture : ce serait une exfiltration."""
        monkeypatch.setattr(srv, "_NC_UPLOAD_DIR", str(tmp_path / "envoi"))
        monkeypatch.setattr(srv, "_NC_SAVE_DIRS", [str(tmp_path / "mission")])
        with pytest.raises(ValueError, match="relatif"):
            srv._resolve_upload_path(str(tmp_path / "mission" / "secret.txt"))

    def test_liste_de_dossiers_lue_dans_l_environnement(self) -> None:
        raw = os.pathsep.join(["/a", "  /b  ", "", "/c"])
        assert srv._parse_dir_list(raw) == ["/a", "/b", "/c"]
        assert srv._parse_dir_list("") == []

    def test_dossier_relatif_ou_gabarit_non_resolu_ignore(self) -> None:
        raw = os.pathsep.join(["relatif/x", "${user_config.nextcloud_save_dirs}", "~/mission", "/a"])
        assert srv._parse_dir_list(raw) == ["~/mission", "/a"]
