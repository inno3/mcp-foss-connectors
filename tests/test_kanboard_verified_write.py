"""Relecture de creation, telle que livree par l'extension 0.17.1.

Sous-ensemble de mcp-inno3/main:tests/test_kanboard_verified_write.py.
Les cinq cas de deplacement/fermeture/reouverture exigent un comportement
absent du source installe ; ils ne sont pas portes dans ce lot d'alignement.
Les helpers et la verification de creation sont bien exposes par l'installe.
Tous les appels sont simules.
"""

import json
import os
from unittest.mock import AsyncMock, patch

import pytest

os.environ.setdefault("KANBOARD_URL", "https://kb.test.local/jsonrpc.php")
os.environ.setdefault("KANBOARD_TOKEN", "test-token")
os.environ.setdefault("KANBOARD_USER", "alice")

from kanboard_mcp.server import (
    _verified_payload,
    _verify_task_state,
    kanboard_close_task,
    kanboard_create_task,
)

COLUMNS = [
    {"id": "11", "title": "A faire"},
    {"id": "12", "title": "En cours"},
]


class TestVerifyTaskState:
    """Le helper de relecture ne doit jamais conclure au succès par défaut."""

    @pytest.mark.asyncio
    async def test_etat_conforme(self) -> None:
        with patch("kanboard_mcp.server.kb_call", new=AsyncMock(return_value={
            "id": "7", "column_id": "12", "is_active": "0", "title": "t",
        })):
            verified, post, mismatch, method = await _verify_task_state(
                7, {"column_id": 12, "is_active": 0}
            )
        assert verified is True
        assert mismatch == []
        assert method == "getTask"
        assert post["column_id"] == "12"

    @pytest.mark.asyncio
    async def test_ecart_detecte(self) -> None:
        with patch("kanboard_mcp.server.kb_call", new=AsyncMock(return_value={
            "id": "7", "column_id": "11", "is_active": "1",
        })):
            verified, _post, mismatch, _m = await _verify_task_state(
                7, {"column_id": 12, "is_active": 0}
            )
        assert verified is False
        assert set(mismatch) == {"column_id", "is_active"}

    @pytest.mark.asyncio
    async def test_int_et_str_ne_sont_pas_un_ecart(self) -> None:
        """Kanboard renvoie ses entiers tantôt en int tantôt en string : comparer
        brut produirait de faux écarts à chaque appel."""
        with patch("kanboard_mcp.server.kb_call", new=AsyncMock(return_value={
            "id": 7, "column_id": 12,
        })):
            verified, _post, mismatch, _m = await _verify_task_state(7, {"column_id": "12"})
        assert verified is True
        assert mismatch == []

    @pytest.mark.asyncio
    async def test_tache_introuvable_donne_non_verifie(self) -> None:
        with patch("kanboard_mcp.server.kb_call", new=AsyncMock(return_value=None)):
            verified, post, mismatch, method = await _verify_task_state(7, {"is_active": 0})
        assert verified is False
        assert post == {}
        assert "introuvable" in method

    @pytest.mark.asyncio
    async def test_relecture_en_erreur_donne_non_verifie(self) -> None:
        """Une relecture qui casse ne doit pas se lire comme un succès."""
        with patch("kanboard_mcp.server.kb_call", new=AsyncMock(side_effect=RuntimeError("boom"))):
            verified, _post, _mismatch, method = await _verify_task_state(7, {"is_active": 0})
        assert verified is False
        assert "relecture impossible" in method


class TestVerifiedPayload:
    def test_succes_verifie_sans_warning(self) -> None:
        out = json.loads(_verified_payload({"success": True}, True, {"id": "7"}, [], "getTask"))
        assert out["verified"] is True
        assert out["verification_method"] == "getTask"
        assert "warning" not in out
        assert "mismatch" not in out

    def test_ecart_produit_mismatch_et_warning(self) -> None:
        out = json.loads(
            _verified_payload({"success": True}, False, {"id": "7"}, ["column_id"], "getTask")
        )
        assert out["verified"] is False
        assert out["mismatch"] == ["column_id"]
        assert "column_id" in out["warning"]

    def test_relecture_impossible_produit_un_warning_distinct(self) -> None:
        out = json.loads(
            _verified_payload({"success": True}, False, {}, [], "relecture impossible (boom)")
        )
        assert out["verified"] is False
        assert "n'a pas pu etre relu" in out["warning"]
        assert "mismatch" not in out






class TestCreateTaskVerified:
    def _kb(self, post_task: dict | None):
        async def _call(method, params=None, as_user=""):
            if method == "getColumns":
                return COLUMNS
            if method == "createTask":
                return 42
            if method == "getTask":
                return post_task
            return True

        return AsyncMock(side_effect=_call)

    @pytest.mark.asyncio
    async def test_creation_verifiee(self) -> None:
        post = {"id": "42", "project_id": "262", "title": "Nouvelle", "column_id": "11"}
        with patch("kanboard_mcp.server.kb_call", new=self._kb(post)):
            out = json.loads(await kanboard_create_task(project_id=262, title="Nouvelle"))
        assert out["success"] is True
        assert out["task_id"] == 42
        assert out["verified"] is True

    @pytest.mark.asyncio
    async def test_colonne_ignoree_en_silence_est_signalee(self) -> None:
        """createTask renvoie un ID même si le column_id demandé est ignoré."""
        post = {"id": "42", "project_id": "262", "title": "Nouvelle", "column_id": "11"}
        with patch("kanboard_mcp.server.kb_call", new=self._kb(post)):
            out = json.loads(
                await kanboard_create_task(
                    project_id=262, title="Nouvelle", column_name="En cours"
                )
            )
        assert out["success"] is True
        assert out["verified"] is False
        assert "column_id" in out["mismatch"]

    @pytest.mark.asyncio
    async def test_tache_absente_apres_creation(self) -> None:
        with patch("kanboard_mcp.server.kb_call", new=self._kb(None)):
            out = json.loads(await kanboard_create_task(project_id=262, title="Nouvelle"))
        assert out["verified"] is False
        assert "introuvable" in out["verification_method"]


class TestNonRegressionContrat:
    """`success` garde sa sémantique : aucun appelant existant ne casse."""

    @pytest.mark.asyncio
    async def test_success_toujours_present_et_vrai(self) -> None:
        async def _call(method, params=None, as_user=""):
            if method == "closeTask":
                return True
            if method == "getTask":
                return {"id": "7", "is_active": "1"}  # écart volontaire
            return True

        with patch("kanboard_mcp.server.kb_call", new=AsyncMock(side_effect=_call)):
            out = json.loads(await kanboard_close_task(task_id=7))
        assert out["success"] is True
        assert out["task_id"] == 7
