"""Relecture apres creation, deplacement, fermeture et reouverture.

Les cinq regressions d'etat de mcp-inno3 sont portees en 0.18.0.
`success` conserve la reponse de l'API ; seul `verified` prouve l'etat attendu.
Une relecture impossible ou un ecart ne provoque jamais de rejeu d'ecriture.
Tous les appels sont simules, y compris les erreurs de transport.
"""

import json
import os
from unittest.mock import AsyncMock, call, patch

import pytest

os.environ.setdefault("KANBOARD_URL", "https://kb.test.local/jsonrpc.php")
os.environ.setdefault("KANBOARD_TOKEN", "test-token")
os.environ.setdefault("KANBOARD_USER", "alice")

from kanboard_mcp.server import (
    KanboardTransportError,
    _verified_payload,
    _verify_task_state,
    kanboard_close_task,
    kanboard_create_task,
    kanboard_move_task,
    kanboard_open_task,
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


class TestMoveTaskVerified:
    def _kb(self, post_column: str, post_swimlane: str = "1"):
        """Lecture initiale, resolution, ecriture, puis etat relu distinct."""
        reads = 0

        async def _call(method, params=None, as_user=""):
            nonlocal reads
            if method == "getColumns":
                return COLUMNS
            if method == "getActiveSwimlanes":
                return [{"id": "2", "name": "Urgent"}]
            if method == "moveTaskPosition":
                return True
            if method == "getTask":
                reads += 1
                return {
                    "id": "7", "project_id": "262", "swimlane_id": "1" if reads == 1 else post_swimlane,
                    "column_id": "11" if reads == 1 else post_column, "title": "t", "is_active": "1",
                }
            raise AssertionError(f"Appel inattendu : {method}")

        return AsyncMock(side_effect=_call)

    @pytest.mark.asyncio
    async def test_deplacement_reel_est_verifie(self) -> None:
        with patch("kanboard_mcp.server.kb_call", new=self._kb(post_column="12")) as rpc:
            out = json.loads(await kanboard_move_task(task_id=7, column_name="En cours"))
        assert out["success"] is True
        assert out["verified"] is True
        assert out["moved_to"] == "En cours"
        assert "warning" not in out
        assert out["task_id"] == 7
        assert out["verification_method"] == "getTask"
        assert out["post_state"]["column_id"] == "12"
        assert out["post_state"]["swimlane_id"] == "1"
        assert "mismatch" not in out
        assert rpc.await_args_list == [
            call("getTask", {"task_id": 7}, as_user=""),
            call("getColumns", {"project_id": 262}, as_user=""),
            call("moveTaskPosition", {
                "project_id": 262, "task_id": 7, "column_id": 12, "position": 1, "swimlane_id": 1,
            }, as_user=""),
            call("getTask", {"task_id": 7}, as_user=""),
        ]

    @pytest.mark.asyncio
    async def test_silent_failure_est_signale(self) -> None:
        """L'API dit true, la tache n'a pas bouge."""
        with patch("kanboard_mcp.server.kb_call", new=self._kb(post_column="11")) as rpc:
            out = json.loads(await kanboard_move_task(task_id=7, column_name="En cours"))
        assert out["success"] is True, "on ne masque pas la réponse de l'API"
        assert out["verified"] is False
        assert "column_id" in out["mismatch"]
        assert "warning" in out
        assert out["task_id"] == 7
        assert out["moved_to"] == "En cours"
        assert out["verification_method"] == "getTask"
        assert out["post_state"]["column_id"] == "11"
        assert [c.args[0] for c in rpc.await_args_list] == [
            "getTask", "getColumns", "moveTaskPosition", "getTask",
        ]

    @pytest.mark.parametrize("post_swimlane", ["1", "2"])
    @pytest.mark.asyncio
    async def test_swimlane_demande_est_verifie(self, post_swimlane) -> None:
        with patch("kanboard_mcp.server.kb_call", new=self._kb("12", post_swimlane)) as rpc:
            out = json.loads(await kanboard_move_task(
                task_id=7, column_name="En cours", swimlane_name="Urgent", as_user="agent",
            ))
        assert out["success"] is True
        assert out["task_id"] == 7
        assert out["moved_to"] == "En cours"
        assert out["swimlane"] == "Urgent"
        assert out["verified"] is (post_swimlane == "2")
        assert out["verification_method"] == "getTask"
        assert out["post_state"]["swimlane_id"] == post_swimlane
        if post_swimlane == "1":
            assert out["mismatch"] == ["swimlane_id"]
            assert "swimlane_id" in out["warning"]
        else:
            assert "mismatch" not in out
            assert "warning" not in out
        assert rpc.await_args_list == [
            call("getTask", {"task_id": 7}, as_user="agent"),
            call("getColumns", {"project_id": 262}, as_user="agent"),
            call("getActiveSwimlanes", {"project_id": 262}, as_user="agent"),
            call("moveTaskPosition", {
                "project_id": 262, "task_id": 7, "column_id": 12, "position": 1, "swimlane_id": 2,
            }, as_user="agent"),
            call("getTask", {"task_id": 7}, as_user="agent"),
        ]


class TestCloseOpenVerified:
    def _kb(self, post_active: str):
        async def _call(method, params=None, as_user=""):
            if method in ("closeTask", "openTask"):
                return True
            if method == "getTask":
                return {"id": "7", "is_active": post_active, "title": "t"}
            raise AssertionError(f"Appel inattendu : {method}")

        return AsyncMock(side_effect=_call)

    @pytest.mark.asyncio
    async def test_close_verifie(self) -> None:
        with patch("kanboard_mcp.server.kb_call", new=self._kb("0")) as rpc:
            out = json.loads(await kanboard_close_task(task_id=7))
        assert out["verified"] is True
        assert out["action"] == "closed"
        assert out["success"] is True
        assert out["task_id"] == 7
        assert out["verification_method"] == "getTask"
        assert out["post_state"]["is_active"] == "0"
        assert "warning" not in out
        assert "mismatch" not in out
        assert rpc.await_args_list == [
            call("closeTask", {"task_id": 7}, as_user=""),
            call("getTask", {"task_id": 7}, as_user=""),
        ]

    @pytest.mark.asyncio
    async def test_close_sans_effet_est_signale(self) -> None:
        with patch("kanboard_mcp.server.kb_call", new=self._kb("1")) as rpc:
            out = json.loads(await kanboard_close_task(task_id=7))
        assert out["verified"] is False
        assert out["mismatch"] == ["is_active"]
        assert out["success"] is True
        assert out["task_id"] == 7
        assert out["action"] == "closed"
        assert out["post_state"]["is_active"] == "1"
        assert "is_active" in out["warning"]
        assert [c.args[0] for c in rpc.await_args_list] == ["closeTask", "getTask"]

    @pytest.mark.asyncio
    async def test_open_verifie(self) -> None:
        with patch("kanboard_mcp.server.kb_call", new=self._kb("1")) as rpc:
            out = json.loads(await kanboard_open_task(task_id=7, as_user="agent"))
        assert out["verified"] is True
        assert out["action"] == "opened"
        assert out["success"] is True
        assert out["task_id"] == 7
        assert out["verification_method"] == "getTask"
        assert out["post_state"]["is_active"] == "1"
        assert "warning" not in out
        assert "mismatch" not in out
        assert rpc.await_args_list == [
            call("openTask", {"task_id": 7}, as_user="agent"),
            call("getTask", {"task_id": 7}, as_user="agent"),
        ]

    @pytest.mark.asyncio
    async def test_open_sans_effet_est_signale(self) -> None:
        with patch("kanboard_mcp.server.kb_call", new=self._kb("0")) as rpc:
            out = json.loads(await kanboard_open_task(task_id=7))
        assert out["success"] is True
        assert out["task_id"] == 7
        assert out["action"] == "opened"
        assert out["verified"] is False
        assert out["mismatch"] == ["is_active"]
        assert out["post_state"]["is_active"] == "0"
        assert "is_active" in out["warning"]
        assert [c.args[0] for c in rpc.await_args_list] == ["openTask", "getTask"]


@pytest.mark.parametrize(("tool", "write_method", "params", "before", "failure"), [
    pytest.param(
        kanboard_move_task, "moveTaskPosition", {"task_id": 7, "column_name": "En cours"},
        [{"id": "7", "project_id": "262", "column_id": "11", "swimlane_id": "1"}, COLUMNS],
        "Echec du deplacement (la tache est peut-etre deja dans cette colonne).", id="deplacement",
    ),
    pytest.param(
        kanboard_close_task, "closeTask", {"task_id": 7}, [],
        "Echec de la fermeture de la tache.", id="fermeture",
    ),
    pytest.param(
        kanboard_open_task, "openTask", {"task_id": 7}, [],
        "Echec de la reouverture de la tache.", id="reouverture",
    ),
])
@pytest.mark.asyncio
class TestStateWriteFailures:
    async def test_relecture_en_erreur_sans_rejeu(self, tool, write_method, params, before, failure):
        rpc = AsyncMock(side_effect=[*before, True, RuntimeError("lecture indisponible")])
        with patch("kanboard_mcp.server.kb_call", new=rpc):
            out = json.loads(await tool(**params, as_user="agent"))
        assert out["success"] is True
        assert out["task_id"] == 7
        assert out["verified"] is False
        assert out["verification_method"] == "relecture impossible (lecture indisponible)"
        assert "le succes n'est pas confirme" in out["warning"]
        assert "post_state" not in out
        assert "mismatch" not in out
        assert rpc.await_count == len(before) + 2
        assert rpc.await_args_list[-2].args[0] == write_method
        assert rpc.await_args_list[-1] == call("getTask", {"task_id": 7}, as_user="agent")
        assert all(c.kwargs["as_user"] == "agent" for c in rpc.await_args_list)

    async def test_erreur_ecriture_conserve_le_contrat(self, tool, write_method, params, before, failure):
        error = KanboardTransportError("ReadTimeout", write_method, 1, False)
        rpc = AsyncMock(side_effect=[*before, error])
        with patch("kanboard_mcp.server.kb_call", new=rpc):
            out = await tool(**params, as_user="agent")
        assert out == f"Erreur réseau {error}"
        assert "inconnu" in out
        assert "non rejouée" in out
        assert rpc.await_count == len(before) + 1
        assert rpc.await_args_list[-1].args[0] == write_method
        assert all(c.kwargs["as_user"] == "agent" for c in rpc.await_args_list)

    async def test_refus_ecriture_conserve_le_message(self, tool, write_method, params, before, failure):
        rpc = AsyncMock(side_effect=[*before, False])
        with patch("kanboard_mcp.server.kb_call", new=rpc):
            out = await tool(**params, as_user="agent")
        assert out == failure
        assert rpc.await_count == len(before) + 1
        assert rpc.await_args_list[-1].args[0] == write_method


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
