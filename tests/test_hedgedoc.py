"""Tests unitaires offline pour le connecteur HedgeDoc.

Tous les appels HTTP sont mockés : la suite tourne sans réseau ni credentials réels.
"""

import asyncio
import json
import os
from unittest.mock import AsyncMock, patch

import pytest

os.environ.setdefault("HEDGEDOC_URL", "https://pad.example.test")
os.environ.setdefault("HEDGEDOC_API_TOKEN", "hd-token-xxxx")
os.environ.setdefault("HEDGEDOC_USER", "tester@example.test")
os.environ.setdefault("HEDGEDOC_PASSWORD", "test-password")

from hedgedoc_mcp import server  # noqa: E402
from hedgedoc_mcp.server import (  # noqa: E402
    _dumps,
    _extract_note_id_from_url,
    hedgedoc_get_note,
    hedgedoc_get_note_info,
    hedgedoc_update_note,
)


class _FakeResponse:
    def __init__(self, status_code=200, text="", payload=None, headers=None):
        self.status_code = status_code
        self.text = text
        self._payload = payload
        self.headers = headers or {}

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class TestHelpers:
    def test_extract_note_id_strips_base_and_anchor(self) -> None:
        assert _extract_note_id_from_url("/aBC123#") == "aBC123"
        assert _extract_note_id_from_url("https://pad.example.test/xYz789") == "xYz789"
        assert _extract_note_id_from_url("/s/mon-alias") == "s/mon-alias"

    def test_dumps_compact(self) -> None:
        assert _dumps({"a": 1}) == '{"a":1}'


@pytest.mark.asyncio
class TestGetNote:
    async def test_returns_markdown(self) -> None:
        with patch(
            "hedgedoc_mcp.server._hd_request",
            new_callable=AsyncMock,
            return_value=_FakeResponse(status_code=200, text="# Titre\ncorps"),
        ) as mock_req:
            raw = await hedgedoc_get_note("aBC123")
        assert mock_req.call_args[0][1] == "/aBC123/download"
        data = json.loads(raw)
        assert data["content"].startswith("# Titre")
        assert data["truncated"] is False

    async def test_redirect_is_reported(self) -> None:
        with patch(
            "hedgedoc_mcp.server._hd_request",
            new_callable=AsyncMock,
            return_value=_FakeResponse(
                status_code=302, headers={"location": "/login"}
            ),
        ):
            raw = await hedgedoc_get_note("priv")
        assert "error" in json.loads(raw)


@pytest.mark.asyncio
class TestGetNoteInfo:
    async def test_extracts_metadata(self) -> None:
        payload = {
            "title": "Note",
            "tags": ["a"],
            "createTime": "2026-01-01",
            "viewcount": 7,
        }
        with patch(
            "hedgedoc_mcp.server._hd_request",
            new_callable=AsyncMock,
            return_value=_FakeResponse(status_code=200, payload=payload),
        ) as mock_req:
            raw = await hedgedoc_get_note_info("aBC123")
        assert mock_req.call_args[0][1] == "/aBC123/info"
        data = json.loads(raw)
        assert data["title"] == "Note"
        assert data["viewcount"] == 7


# --------------------------------------------------------------------------
# Session, re-login 403, echecs honnetes (portes avec le connecteur, 11/08/2026)
# --------------------------------------------------------------------------


class _Resp:
    """Réponse HTTP minimale (status_code + text suffisent au connecteur)."""

    def __init__(self, status_code: int, text: str = "") -> None:
        self.status_code = status_code
        self.text = text
        self.headers: dict[str, str] = {}

    def raise_for_status(self) -> None:
        raise AssertionError("raise_for_status ne doit pas être atteint ici")


def _patch_requests(monkeypatch, put_status: int, info_status: int) -> None:
    async def fake(method, path, content_type="application/json", *args, **kwargs):
        if method == "PUT":
            return _Resp(put_status, f"Cannot PUT {path}")
        if method == "GET" and path.endswith("/info"):
            return _Resp(info_status, "{}" if info_status == 200 else "not found")
        raise AssertionError(f"appel inattendu : {method} {path}")

    monkeypatch.setattr(server, "_hd_request", fake)


class TestUpdateNoteEchecHonnete:
    """L'échec doit nommer la limite de HedgeDoc, pas mentir sur un 404."""

    def test_note_existante_erreur_explicite(self, monkeypatch) -> None:
        _patch_requests(monkeypatch, put_status=404, info_status=200)
        out = json.loads(asyncio.run(hedgedoc_update_note("VGHh8yR0QUKDRyg8xk4IOQ", "# v2")))
        assert out["success"] is False
        assert out["error"] == "hedgedoc_update_unsupported"
        assert out["note_exists"] is True
        assert "HedgeDoc 1.x" in out["message"]
        assert "hedgedoc_create_note" in out["message"]
        # Le message trompeur d'origine ne doit plus apparaître
        assert "note non trouvée dans HedgeDoc" not in out["message"]

    def test_note_absente_reste_un_404_honnete(self, monkeypatch) -> None:
        _patch_requests(monkeypatch, put_status=404, info_status=404)
        out = json.loads(asyncio.run(hedgedoc_update_note("inexistante", "# v2")))
        assert out["error"] == "note_not_found"
        assert "introuvable" in out["message"]

    def test_405_traite_comme_route_absente(self, monkeypatch) -> None:
        _patch_requests(monkeypatch, put_status=405, info_status=200)
        out = json.loads(asyncio.run(hedgedoc_update_note("abc123", "# v2")))
        assert out["error"] == "hedgedoc_update_unsupported"
        assert out["http_status"] == 405

    def test_succes_si_une_instance_expose_l_update(self, monkeypatch) -> None:
        """Compatibilité ascendante : un 200 reste un succès."""
        async def fake(method, path, content_type="application/json", *args, **kwargs):
            return _Resp(200)

        monkeypatch.setattr(server, "_hd_request", fake)
        out = json.loads(asyncio.run(hedgedoc_update_note("abc123", "# v2")))
        assert out["success"] is True
        assert out["note_id"] == "abc123"


class _JsonResp(_Resp):
    """Reponse avec corps JSON (pour /me)."""

    def __init__(self, status_code: int, payload: dict) -> None:
        super().__init__(status_code, json.dumps(payload))
        self._payload = payload

    def json(self) -> dict:
        return self._payload


class TestReloginSur403:
    """HedgeDoc 1.x renvoie 403, pas 401, quand la session n'est pas etablie."""

    def test_403_declenche_un_relogin_et_un_retry(self, monkeypatch) -> None:
        calls: list[tuple[str, str]] = []
        logins: list[int] = []

        async def fake_login() -> bool:
            logins.append(1)
            server._session_valid = True
            return True

        class _Client:
            def __init__(self, *a, **k) -> None:
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *a) -> None:
                return None

            async def request(self, method, url, headers=None, **kwargs):
                calls.append((method, url))
                # 403 la premiere fois, 200 apres re-login
                return _Resp(403 if len(calls) == 1 else 200)

        monkeypatch.setattr(server, "_login", fake_login)
        monkeypatch.setattr(server, "_ensure_session", lambda: _noop())
        monkeypatch.setattr(server.httpx, "AsyncClient", _Client)

        resp = asyncio.run(server._hd_request("DELETE", "/history/abc", content_type=""))
        assert resp.status_code == 200
        assert len(logins) == 1, "le 403 doit relancer le login"
        assert len(calls) == 2, "la requete doit etre rejouee apres le login"


async def _noop() -> None:
    return None


class TestAuthHint:
    """L'etat de /me doit trancher entre probleme d'auth et probleme de droits."""

    def test_sans_credentials(self) -> None:
        hint = server._hd_auth_hint({"credentials_configured": False})
        assert "mode anonyme" in hint

    def test_session_non_etablie(self) -> None:
        hint = server._hd_auth_hint(
            {"credentials_configured": True, "logged_in": False}
        )
        assert "pas de droits" in hint

    def test_session_active_donc_vrai_probleme_de_droits(self) -> None:
        hint = server._hd_auth_hint(
            {"credentials_configured": True, "logged_in": True, "login": "bjean"}
        )
        assert "bien sur les droits" in hint
        assert "bjean" in hint


class TestCreateNoteAliasInterdit:
    """403 sur /new/<alias> = option d'instance, pas un souci de droits de note."""

    def test_alias_403_nomme_allowfreeurl(self, monkeypatch) -> None:
        async def fake(method, path, content_type="application/json", *args, **kwargs):
            if method == "POST":
                return _Resp(403, "Forbidden")
            if method == "GET" and path == "/me":
                return _JsonResp(200, {"isLoggedIn": True, "name": "bjean"})
            raise AssertionError(f"appel inattendu : {method} {path}")

        monkeypatch.setattr(server, "_hd_request", fake)
        out = json.loads(
            asyncio.run(server.hedgedoc_create_note("# contenu", alias="mon-alias"))
        )
        assert out["error"] == "hedgedoc_alias_forbidden"
        assert "allowFreeURL" in out["message"] or "CMD_ALLOW_FREEURL" in out["message"]
        # La session est active : le message ne doit pas accuser l'authentification
        assert out["auth"]["logged_in"] is True
        assert "bien sur les droits" in out["message"]
        assert any("sans alias" in w for w in out["workarounds"])

    def test_sans_alias_pas_de_message_alias(self, monkeypatch) -> None:
        async def fake(method, path, content_type="application/json", *args, **kwargs):
            resp = _Resp(302)
            resp.headers = {"location": "/nouvelle-note"}
            return resp

        monkeypatch.setattr(server, "_hd_request", fake)
        out = json.loads(asyncio.run(server.hedgedoc_create_note("# contenu")))
        assert out["success"] is True
        assert out["note_id"] == "nouvelle-note"


class TestDeleteHistoryDiagnostic:
    """Le 403 tombait dans « permissions insuffisantes (note privee) »."""

    def test_403_expose_l_etat_de_session(self, monkeypatch) -> None:
        async def fake(method, path, content_type="application/json", *args, **kwargs):
            if method == "DELETE":
                return _Resp(403, "Forbidden")
            if method == "GET" and path == "/me":
                return _JsonResp(403, {})
            raise AssertionError(f"appel inattendu : {method} {path}")

        monkeypatch.setattr(server, "_hd_request", fake)
        out = json.loads(asyncio.run(server.hedgedoc_delete_history_entry("abc123")))
        assert out["error"] == "hedgedoc_history_delete_denied"
        assert out["auth"]["logged_in"] is False
        assert "pas de droits" in out["message"]
        # L'ancien diagnostic trompeur a disparu
        assert "note privée" not in out["message"]

    def test_204_reste_un_succes(self, monkeypatch) -> None:
        async def fake(method, path, content_type="application/json", *args, **kwargs):
            return _Resp(204)

        monkeypatch.setattr(server, "_hd_request", fake)
        out = json.loads(asyncio.run(server.hedgedoc_delete_history_entry("abc123")))
        assert out["success"] is True


class TestFormatError403:
    """Le message generique n'affirme plus une cause unique."""

    def test_403_liste_les_causes(self) -> None:
        import httpx

        exc = httpx.HTTPStatusError(
            "403", request=httpx.Request("GET", "http://t"),
            response=httpx.Response(403, request=httpx.Request("GET", "http://t")),
        )
        msg = server._format_error(exc)
        assert "session non établie" in msg
        assert "allowFreeURL" in msg
