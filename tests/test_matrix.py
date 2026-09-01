"""Tests unitaires offline pour le connecteur Matrix.

Tous les appels HTTP sont mockés : la suite tourne sans réseau ni token réel.
"""

import json
import os
from unittest.mock import AsyncMock, patch

import pytest

os.environ.setdefault("MATRIX_HOMESERVER_URL", "https://matrix.example.test")
os.environ.setdefault("MATRIX_ACCESS_TOKEN", "syt_xxxx")

import matrix_mcp.server as mx  # noqa: E402
from matrix_mcp.server import (  # noqa: E402
    _matrix_url,
    _permalink,
    _ts_to_iso,
    matrix_edit_message,
    matrix_get_event_context,
    matrix_get_permalink,
    matrix_get_reactions,
    matrix_get_room_messages,
    matrix_get_thread,
    matrix_list_files,
    matrix_list_unread,
    matrix_mark_read,
    matrix_react,
    matrix_redact_message,
    matrix_reply_in_thread,
    matrix_search_messages,
    matrix_send_file,
    matrix_send_message,
    matrix_whoami,
)


class TestHelpers:
    def test_matrix_url_builds_csv3_path(self) -> None:
        base = mx.MATRIX_HOMESERVER_URL
        assert _matrix_url("account/whoami") == (
            f"{base}/_matrix/client/v3/account/whoami"
        )
        assert _matrix_url("/joined_rooms").endswith("/v3/joined_rooms")

    def test_ts_to_iso_none(self) -> None:
        assert _ts_to_iso(None) is None

    def test_ts_to_iso_converts_ms(self) -> None:
        # 0 ms = epoch
        assert _ts_to_iso(0) == "1970-01-01T00:00:00Z"


@pytest.mark.asyncio
class TestWhoami:
    async def test_returns_compact_identity(self) -> None:
        with patch(
            "matrix_mcp.server._get",
            new_callable=AsyncMock,
            return_value={"user_id": "@bot:example.test", "device_id": "DEV"},
        ) as mock_get:
            raw = await matrix_whoami()
        assert mock_get.call_args[0][0] == "account/whoami"
        data = json.loads(raw)
        assert data["user_id"] == "@bot:example.test"
        assert data["is_guest"] is False


@pytest.mark.asyncio
class TestGetRoomMessages:
    async def test_filters_message_events_only(self) -> None:
        chunk = {
            "chunk": [
                {
                    "type": "m.room.message",
                    "event_id": "$1",
                    "sender": "@a:x",
                    "content": {"body": "hi", "msgtype": "m.text"},
                    "origin_server_ts": 0,
                },
                {"type": "m.room.member", "event_id": "$2"},
            ]
        }
        with patch(
            "matrix_mcp.server._get", new_callable=AsyncMock, return_value=chunk
        ) as mock_get:
            raw = await matrix_get_room_messages("!room:x", limit=5)
        path = mock_get.call_args[0][0]
        # Le room_id part encodé : « ! » et « : » n'ont rien à faire nus dans un
        # segment d'URL, même si Synapse les tolère.
        assert path == "rooms/%21room%3Ax/messages"
        assert mock_get.call_args[1]["params"]["dir"] == "b"
        data = json.loads(raw)
        assert data["count"] == 1
        assert data["messages"][0]["body"] == "hi"

    async def test_limit_clamped(self) -> None:
        with patch(
            "matrix_mcp.server._get",
            new_callable=AsyncMock,
            return_value={"chunk": []},
        ) as mock_get:
            await matrix_get_room_messages("!r:x", limit=999)
        assert mock_get.call_args[1]["params"]["limit"] == "100"


@pytest.mark.asyncio
class TestSendMessage:
    async def test_puts_text_event(self) -> None:
        with patch(
            "matrix_mcp.server._put",
            new_callable=AsyncMock,
            return_value={"event_id": "$evt"},
        ) as mock_put:
            raw = await matrix_send_message("!r:x", "hello")
        path, body = mock_put.call_args[0][0], mock_put.call_args[0][1]
        # Le room_id part encodé, comme partout ailleurs dans le connecteur.
        assert path.startswith("rooms/%21r%3Ax/send/m.room.message/")
        assert body == {"msgtype": "m.text", "body": "hello"}
        assert json.loads(raw)["event_id"] == "$evt"


# ---------------------------------------------------------------------------
# Recherche paginée, fils, corrections, pièces jointes
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clear_room_name_cache():
    """Le nom des salons est mémorisé : repartir d'un cache vide."""
    mx._ROOM_NAMES.clear()
    yield
    mx._ROOM_NAMES.clear()


def _hit(event_id="$e1", body="rapprochement bancaire", room="!r:inno3.eu") -> dict:
    return {"result": {
        "event_id": event_id, "sender": "@bjean:inno3.eu", "room_id": room,
        "origin_server_ts": 1787011200000, "content": {"body": body, "msgtype": "m.text"},
    }, "rank": 0.9}


def _search_response(results, next_batch=None, count=None) -> dict:
    payload = {"results": results, "count": count if count is not None else len(results)}
    if next_batch:
        payload["next_batch"] = next_batch
    return {"search_categories": {"room_events": payload}}


class TestSearchPagination:
    """Le serveur rend un total et un jeton de suite ; ils étaient jetés.

    46 % des appels Matrix sont des recherches. Sans pagination, seule la
    première page — dix résultats par défaut côté serveur — était accessible,
    et rien n'indiquait qu'il en restait.
    """

    async def test_the_total_and_the_cursor_are_surfaced(self) -> None:
        with patch("matrix_mcp.server._post", new_callable=AsyncMock) as post, \
             patch("matrix_mcp.server._get", new_callable=AsyncMock) as get:
            get.return_value = {"name": "Direction"}
            post.return_value = _search_response([_hit()], next_batch="TOKEN", count=137)
            data = json.loads(await matrix_search_messages("rapprochement"))

        assert data["index_matches"] == 137
        assert data["next_batch"] == "TOKEN"
        assert data["has_more"] is True

    async def test_the_last_page_says_so(self) -> None:
        with patch("matrix_mcp.server._post", new_callable=AsyncMock) as post, \
             patch("matrix_mcp.server._get", new_callable=AsyncMock) as get:
            get.return_value = {}
            post.return_value = _search_response([_hit()])
            data = json.loads(await matrix_search_messages("x"))

        assert data["has_more"] is False

    async def test_a_cursor_is_sent_back_on_the_query_string(self) -> None:
        with patch("matrix_mcp.server._post", new_callable=AsyncMock) as post, \
             patch("matrix_mcp.server._get", new_callable=AsyncMock) as get:
            get.return_value = {}
            post.return_value = _search_response([])
            await matrix_search_messages("x", next_batch="TOKEN")

        assert post.call_args[0][0] == "search?next_batch=TOKEN"

    async def test_the_limit_reaches_the_server_filter(self) -> None:
        with patch("matrix_mcp.server._post", new_callable=AsyncMock) as post, \
             patch("matrix_mcp.server._get", new_callable=AsyncMock) as get:
            get.return_value = {}
            post.return_value = _search_response([])
            await matrix_search_messages("x", limit=50)

        f = post.call_args[0][1]["search_categories"]["room_events"]["filter"]
        assert f["limit"] == 50

    async def test_a_sender_filter_is_forwarded(self) -> None:
        with patch("matrix_mcp.server._post", new_callable=AsyncMock) as post, \
             patch("matrix_mcp.server._get", new_callable=AsyncMock) as get:
            get.return_value = {}
            post.return_value = _search_response([])
            await matrix_search_messages("x", sender="@bjean:inno3.eu", room_id="!r:inno3.eu")

        f = post.call_args[0][1]["search_categories"]["room_events"]["filter"]
        assert f["senders"] == ["@bjean:inno3.eu"]
        assert f["rooms"] == ["!r:inno3.eu"]

    async def test_context_is_requested_only_when_asked(self) -> None:
        with patch("matrix_mcp.server._post", new_callable=AsyncMock) as post, \
             patch("matrix_mcp.server._get", new_callable=AsyncMock) as get:
            get.return_value = {}
            post.return_value = _search_response([])
            await matrix_search_messages("x")
            assert "event_context" not in post.call_args[0][1][
                "search_categories"]["room_events"]

            await matrix_search_messages("x", context_size=3)
            ctx = post.call_args[0][1]["search_categories"]["room_events"]["event_context"]
            assert ctx == {"before_limit": 3, "after_limit": 3, "include_profile": False}

    async def test_results_carry_the_room_name_and_a_permalink(self) -> None:
        with patch("matrix_mcp.server._post", new_callable=AsyncMock) as post, \
             patch("matrix_mcp.server._get", new_callable=AsyncMock) as get:
            get.return_value = {"name": "Direction inno³"}
            post.return_value = _search_response([_hit()])
            data = json.loads(await matrix_search_messages("x"))

        hit = data["results"][0]
        assert hit["room_name"] == "Direction inno³"
        assert hit["permalink"].startswith("https://matrix.to/#/")

    async def test_the_room_name_is_resolved_once_per_room(self) -> None:
        with patch("matrix_mcp.server._post", new_callable=AsyncMock) as post, \
             patch("matrix_mcp.server._get", new_callable=AsyncMock) as get:
            get.return_value = {"name": "Direction"}
            post.return_value = _search_response(
                [_hit("$a"), _hit("$b"), _hit("$c")]
            )
            await matrix_search_messages("x")

        assert get.await_count == 1


class TestRoomMessagePagination:
    async def test_the_end_token_is_returned(self) -> None:
        with patch("matrix_mcp.server._get", new_callable=AsyncMock) as get:
            get.return_value = {"chunk": [{
                "type": "m.room.message", "event_id": "$e", "sender": "@a:b",
                "origin_server_ts": 1787011200000, "content": {"body": "salut"},
            }], "end": "t42"}
            data = json.loads(await matrix_get_room_messages("!r:inno3.eu"))

        assert data["next_token"] == "t42"
        assert data["has_more"] is True

    async def test_a_cursor_is_forwarded(self) -> None:
        with patch("matrix_mcp.server._get", new_callable=AsyncMock) as get:
            get.return_value = {"chunk": []}
            await matrix_get_room_messages("!r:inno3.eu", from_token="t42")

        assert get.call_args.kwargs["params"]["from"] == "t42"

    async def test_an_empty_page_is_not_has_more(self) -> None:
        with patch("matrix_mcp.server._get", new_callable=AsyncMock) as get:
            get.return_value = {"chunk": [], "end": "t99"}
            data = json.loads(await matrix_get_room_messages("!r:inno3.eu"))

        assert data["has_more"] is False


class TestThreads:
    """Répondre dans un fil, plutôt qu'ajouter un message au salon."""

    async def test_a_reply_carries_the_thread_relation(self) -> None:
        with patch("matrix_mcp.server._put", new_callable=AsyncMock) as put:
            put.return_value = {"event_id": "$reply"}
            data = json.loads(await matrix_reply_in_thread(
                "!r:inno3.eu", "$root", "je regarde"))

        path, content = put.call_args[0]
        assert "/send/m.room.message/" in path
        rel = content["m.relates_to"]
        assert rel["rel_type"] == "m.thread"
        assert rel["event_id"] == "$root"
        # Sans ce drapeau, les clients sans fils affichent une citation en double.
        assert rel["is_falling_back"] is True
        assert data["event_id"] == "$reply"

    async def test_html_is_attached_when_given(self) -> None:
        with patch("matrix_mcp.server._put", new_callable=AsyncMock) as put:
            put.return_value = {"event_id": "$r"}
            await matrix_reply_in_thread("!r", "$root", "texte", html_message="<b>t</b>")

        content = put.call_args[0][1]
        assert content["format"] == "org.matrix.custom.html"
        assert content["formatted_body"] == "<b>t</b>"

    async def test_a_thread_is_read_from_the_v1_relations_route(self) -> None:
        async def fake_get(path, params=None):
            if "relations" in path:
                return {"chunk": [{"event_id": "$r1", "sender": "@a:b",
                                   "content": {"body": "réponse"},
                                   "origin_server_ts": 1787011200000}],
                        "next_batch": None}
            return {"event_id": "$root", "sender": "@a:b",
                    "content": {"body": "racine"}, "origin_server_ts": 1787011200000}

        with patch("matrix_mcp.server._get", new=AsyncMock(side_effect=fake_get)) as get:
            data = json.loads(await matrix_get_thread("!r:inno3.eu", "$root"))

        assert any("_matrix/client/v1/" in c.args[0] for c in get.await_args_list)
        assert data["count"] == 1
        assert data["root"]["body"] == "racine"

    def test_a_non_v3_path_is_left_alone(self) -> None:
        # La base est lue à l'import : la relire ici rend le test indépendant
        # de l'ordre dans lequel pytest a chargé les modules.
        base = mx.MATRIX_HOMESERVER_URL
        assert _matrix_url("_matrix/client/v1/x") == f"{base}/_matrix/client/v1/x"
        assert _matrix_url("rooms/x") == f"{base}/_matrix/client/v3/rooms/x"


class TestReactEditRedact:
    async def test_a_reaction_returns_its_own_event_id(self) -> None:
        with patch("matrix_mcp.server._put", new_callable=AsyncMock) as put:
            put.return_value = {"event_id": "$reaction"}
            data = json.loads(await matrix_react("!r", "$msg", "✅"))

        path, content = put.call_args[0]
        assert "/send/m.reaction/" in path
        assert content["m.relates_to"] == {
            "rel_type": "m.annotation", "event_id": "$msg", "key": "✅"}
        # C'est cet id, pas celui du message, qu'il faut pour retirer la réaction.
        assert data["reaction_event_id"] == "$reaction"
        assert data["reacted_to"] == "$msg"

    async def test_an_edit_sends_a_replacement_not_a_rewrite(self) -> None:
        with patch("matrix_mcp.server._put", new_callable=AsyncMock) as put:
            put.return_value = {"event_id": "$edit"}
            data = json.loads(await matrix_edit_message("!r", "$msg", "corrigé"))

        content = put.call_args[0][1]
        assert content["m.relates_to"] == {"rel_type": "m.replace", "event_id": "$msg"}
        assert content["m.new_content"]["body"] == "corrigé"
        assert content["body"] == "* corrigé"       # repli des clients sans édition
        assert "historique" in data["note"]

    async def test_a_redaction_is_verified_by_rereading(self) -> None:
        with patch("matrix_mcp.server._put", new_callable=AsyncMock) as put, \
             patch("matrix_mcp.server._get", new_callable=AsyncMock) as get:
            put.return_value = {"event_id": "$redaction"}
            get.return_value = {"event_id": "$msg", "content": {}}   # contenu vidé
            data = json.loads(await matrix_redact_message("!r", "$msg", reason="erreur"))

        assert "/redact/" in put.call_args[0][0]
        assert put.call_args[0][1] == {"reason": "erreur"}
        assert data["success"] is True
        assert data["verified"] is True

    async def test_a_redaction_without_effect_is_not_a_success(self) -> None:
        with patch("matrix_mcp.server._put", new_callable=AsyncMock) as put, \
             patch("matrix_mcp.server._get", new_callable=AsyncMock) as get:
            put.return_value = {"event_id": "$redaction"}
            get.return_value = {"event_id": "$msg", "content": {"body": "toujours là"}}
            data = json.loads(await matrix_redact_message("!r", "$msg"))

        assert data["success"] is False

    async def test_each_send_uses_a_fresh_transaction_id(self) -> None:
        """Matrix déduplique sur cet identifiant : le réutiliser perd un message."""
        seen = []
        with patch("matrix_mcp.server._put", new_callable=AsyncMock) as put:
            put.return_value = {"event_id": "$x"}
            await matrix_react("!r", "$m", "👍")
            seen.append(put.call_args[0][0])
            await matrix_react("!r", "$m", "🎉")
            seen.append(put.call_args[0][0])

        assert seen[0] != seen[1]


class TestSendFile:
    async def test_a_file_is_uploaded_then_referenced(self, tmp_path) -> None:
        target = tmp_path / "rapport.pdf"
        target.write_bytes(b"%PDF-1.4")

        with patch("matrix_mcp.server._upload_media", new_callable=AsyncMock) as up, \
             patch("matrix_mcp.server._put", new_callable=AsyncMock) as put:
            up.return_value = {"content_uri": "mxc://inno3.eu/abc"}
            put.return_value = {"event_id": "$file"}
            data = json.loads(await matrix_send_file("!r", str(target), caption="Rapport"))

        payload, filename, mimetype = up.call_args[0]
        assert payload == b"%PDF-1.4"
        assert filename == "rapport.pdf"
        assert mimetype == "application/pdf"
        content = put.call_args[0][1]
        assert content["msgtype"] == "m.file"
        assert content["url"] == "mxc://inno3.eu/abc"
        assert content["body"] == "Rapport"
        assert data["success"] is True

    async def test_an_image_gets_the_image_msgtype(self, tmp_path) -> None:
        target = tmp_path / "capture.png"
        target.write_bytes(b"\x89PNG")
        with patch("matrix_mcp.server._upload_media", new_callable=AsyncMock) as up, \
             patch("matrix_mcp.server._put", new_callable=AsyncMock) as put:
            up.return_value = {"content_uri": "mxc://x/y"}
            put.return_value = {"event_id": "$i"}
            await matrix_send_file("!r", str(target))

        assert put.call_args[0][1]["msgtype"] == "m.image"

    async def test_a_file_can_land_in_a_thread(self, tmp_path) -> None:
        target = tmp_path / "a.txt"
        target.write_bytes(b"x")
        with patch("matrix_mcp.server._upload_media", new_callable=AsyncMock) as up, \
             patch("matrix_mcp.server._put", new_callable=AsyncMock) as put:
            up.return_value = {"content_uri": "mxc://x/y"}
            put.return_value = {"event_id": "$f"}
            await matrix_send_file("!r", str(target), thread_root_event_id="$root")

        assert put.call_args[0][1]["m.relates_to"]["rel_type"] == "m.thread"

    async def test_a_missing_file_is_refused_before_upload(self, tmp_path) -> None:
        with patch("matrix_mcp.server._upload_media", new_callable=AsyncMock) as up:
            msg = await matrix_send_file("!r", str(tmp_path / "absent.pdf"))
        assert "introuvable" in msg
        up.assert_not_called()

    async def test_an_oversized_file_is_refused(self, tmp_path) -> None:
        target = tmp_path / "gros.bin"
        target.write_bytes(b"x")
        with patch("matrix_mcp.server.MEDIA_UPLOAD_MAX_BYTES", 0), \
             patch("matrix_mcp.server._upload_media", new_callable=AsyncMock) as up:
            msg = await matrix_send_file("!r", str(target))
        assert "trop volumineux" in msg
        up.assert_not_called()

    async def test_a_homeserver_without_uri_is_not_a_success(self, tmp_path) -> None:
        target = tmp_path / "a.txt"
        target.write_bytes(b"x")
        with patch("matrix_mcp.server._upload_media", new_callable=AsyncMock) as up, \
             patch("matrix_mcp.server._put", new_callable=AsyncMock) as put:
            up.return_value = {}
            data = json.loads(await matrix_send_file("!r", str(target)))
        assert data["success"] is False
        put.assert_not_called()


class TestContextAndPermalink:
    async def test_context_returns_before_event_and_after(self) -> None:
        async def fake_get(path, params=None):
            if "/context/" in path:
                return {
                    "events_before": [{"event_id": "$b", "content": {"body": "avant"}}],
                    "event": {"event_id": "$e", "content": {"body": "cible"}},
                    "events_after": [{"event_id": "$a", "content": {"body": "après"}}],
                }
            return {"name": "Direction"}

        with patch("matrix_mcp.server._get", new=AsyncMock(side_effect=fake_get)):
            data = json.loads(await matrix_get_event_context("!r:inno3.eu", "$e"))

        assert data["before"][0]["body"] == "avant"
        assert data["event"]["body"] == "cible"
        assert data["after"][0]["body"] == "après"
        assert data["room_name"] == "Direction"

    def test_a_permalink_targets_a_room_or_a_message(self) -> None:
        assert _permalink("!r:inno3.eu") == "https://matrix.to/#/%21r%3Ainno3.eu"
        assert _permalink("!r:inno3.eu", "$e").endswith("/%24e")

    async def test_the_permalink_tool_makes_no_network_call(self) -> None:
        with patch("matrix_mcp.server._get", new_callable=AsyncMock) as get, \
             patch("matrix_mcp.server._post", new_callable=AsyncMock) as post:
            data = json.loads(await matrix_get_permalink("!r:inno3.eu", "$e"))
        get.assert_not_called()
        post.assert_not_called()
        assert data["permalink"].startswith("https://matrix.to/#/")


# ---------------------------------------------------------------------------
# Non-régression : recette du 01/09/2026
# ---------------------------------------------------------------------------


class TestSearchVolumeIsBounded:
    """50 résultats sortaient à 870 000 caractères — recherche inutilisable."""

    @staticmethod
    def _long_hit(n=3000) -> dict:
        return {"result": {
            "event_id": "$e", "sender": "@a:b", "room_id": "!r:b",
            "origin_server_ts": 1787011200000,
            "content": {"body": "x" * n, "msgtype": "m.text"},
        }}

    async def test_bodies_are_clipped_and_the_cut_is_announced(self) -> None:
        with patch("matrix_mcp.server._post", new_callable=AsyncMock) as post, \
             patch("matrix_mcp.server._get", new_callable=AsyncMock) as get:
            get.return_value = {}
            post.return_value = _search_response([self._long_hit()])
            raw = await matrix_search_messages("x")

        data = json.loads(raw)
        hit = data["results"][0]
        assert len(hit["body"]) == mx.BODY_PREVIEW_CHARS
        assert hit["body_truncated"] is True
        assert hit["body_full_length"] == 3000
        assert len(raw) < 3000

    async def test_the_full_body_can_be_asked_for(self) -> None:
        with patch("matrix_mcp.server._post", new_callable=AsyncMock) as post, \
             patch("matrix_mcp.server._get", new_callable=AsyncMock) as get:
            get.return_value = {}
            post.return_value = _search_response([self._long_hit()])
            data = json.loads(await matrix_search_messages("x", body_chars=0))

        assert len(data["results"][0]["body"]) == 3000
        assert "body_truncated" not in data["results"][0]


class TestIndexTotalIsNotAResultCount:
    """`total_available` ignorait les filtres et comptait les supprimés.

    Vérifié le 01/09/2026 : une recherche filtrée par expéditeur annonçait 109
    disponibles, en rendait 2, et déclarait n'avoir plus rien.
    """

    async def test_the_field_is_renamed_and_qualified(self) -> None:
        with patch("matrix_mcp.server._post", new_callable=AsyncMock) as post, \
             patch("matrix_mcp.server._get", new_callable=AsyncMock) as get:
            get.return_value = {}
            post.return_value = _search_response([_hit()], count=109)
            data = json.loads(await matrix_search_messages("x", sender="@bjean:inno3.fr"))

        assert "total_available" not in data          # nom trompeur retiré
        assert data["index_matches"] == 109
        assert "IGNORE en revanche" in data["index_matches_note"]
        assert data["index_matches_reliable"] is False


class TestSearchFilters:
    async def test_rooms_can_be_excluded(self) -> None:
        with patch("matrix_mcp.server._post", new_callable=AsyncMock) as post, \
             patch("matrix_mcp.server._get", new_callable=AsyncMock) as get:
            get.return_value = {}
            post.return_value = _search_response([])
            await matrix_search_messages("x", exclude_rooms="!bruit:b, !alertes:b")

        f = post.call_args[0][1]["search_categories"]["room_events"]["filter"]
        assert f["not_rooms"] == ["!bruit:b", "!alertes:b"]

    async def test_a_date_window_filters_client_side_and_says_so(self) -> None:
        old_hit = dict(_hit("$vieux"))
        old_hit["result"]["origin_server_ts"] = 1750000000000   # juin 2025
        with patch("matrix_mcp.server._post", new_callable=AsyncMock) as post, \
             patch("matrix_mcp.server._get", new_callable=AsyncMock) as get:
            get.return_value = {}
            post.return_value = _search_response([_hit("$recent"), old_hit])
            data = json.loads(await matrix_search_messages("x", since="2026-08-01"))

        assert data["count"] == 1
        assert data["filtered_out"] == 1
        # L'API Matrix ne sait pas borner dans le temps : le dire évite de
        # prendre une page filtrée pour un résultat exhaustif.
        assert "côté client" in data["date_filter_note"]


class TestRoomMessageLimitCountsMessages:
    """Le `limit` de l'API porte sur les événements bruts, pas les messages."""

    async def test_pages_are_chained_until_the_count_is_met(self) -> None:
        def page(n, end):
            return {"chunk": [
                {"type": "m.reaction", "event_id": f"$r{n}", "content": {"key": "x"}},
                {"type": "m.room.message", "event_id": f"$m{n}", "sender": "@a:b",
                 "origin_server_ts": 1787011200000, "content": {"body": f"msg {n}"}},
            ], "end": end}

        pages = iter([page(1, "t1"), page(2, "t2"), page(3, "t3")])
        with patch("matrix_mcp.server._get",
                   new=AsyncMock(side_effect=lambda *a, **k: next(pages))):
            data = json.loads(await matrix_get_room_messages("!r:b", limit=3))

        assert data["count"] == 3            # 3 messages, pas 3 événements
        assert data["pages_fetched"] == 3

    async def test_redacted_messages_are_dropped_not_returned_empty(self) -> None:
        with patch("matrix_mcp.server._get", new_callable=AsyncMock) as get:
            get.return_value = {"chunk": [
                {"type": "m.room.message", "event_id": "$vide", "content": {}},
                {"type": "m.room.message", "event_id": "$plein", "sender": "@a:b",
                 "origin_server_ts": 1787011200000, "content": {"body": "réel"}},
            ], "end": None}
            data = json.loads(await matrix_get_room_messages("!r:b", limit=5))

        assert [m["event_id"] for m in data["messages"]] == ["$plein"]

    async def test_the_page_loop_is_bounded(self) -> None:
        with patch("matrix_mcp.server._get", new_callable=AsyncMock) as get:
            get.return_value = {"chunk": [
                {"type": "m.reaction", "event_id": "$r", "content": {"key": "x"}}
            ], "end": "t"}
            data = json.loads(await matrix_get_room_messages("!r:b", limit=10))

        assert data["pages_fetched"] == mx.MESSAGE_FETCH_MAX_PAGES
        assert data["count"] == 0


class TestContextIsTrimmedOnBothSides:
    """before=3, after=3 rendait 3 avant et 4 après."""

    async def test_each_side_is_cut_to_what_was_asked(self) -> None:
        async def fake_get(path, params=None):
            if "/context/" in path:
                return {
                    "events_before": [{"event_id": f"$b{i}", "content": {"body": "b"}}
                                      for i in range(5)],
                    "event": {"event_id": "$e", "content": {"body": "cible"}},
                    "events_after": [{"event_id": f"$a{i}", "content": {"body": "a"}}
                                     for i in range(5)],
                }
            return {"name": "salon"}

        with patch("matrix_mcp.server._get", new=AsyncMock(side_effect=fake_get)):
            data = json.loads(await matrix_get_event_context("!r:b", "$e", before=3, after=3))

        assert len(data["before"]) == 3
        assert len(data["after"]) == 3


class TestSendMessageReturnsAPermalink:
    async def test_the_permalink_is_present_like_the_other_write_tools(self) -> None:
        with patch("matrix_mcp.server._put", new_callable=AsyncMock) as put:
            put.return_value = {"event_id": "$sent"}
            data = json.loads(await matrix_send_message("!r:inno3.eu", "coucou"))

        assert data["event_id"] == "$sent"
        assert data["permalink"].startswith("https://matrix.to/#/")
        assert "%24sent" in data["permalink"]


class TestReadReactions:
    """Poser une réaction sans pouvoir la relire ne dit pas si on a acquitté."""

    async def test_reactions_are_grouped_by_emoji_with_senders(self) -> None:
        with patch("matrix_mcp.server._get", new_callable=AsyncMock) as get:
            get.return_value = {"chunk": [
                {"event_id": "$r1", "sender": "@a:b", "origin_server_ts": 1787011200000,
                 "content": {"m.relates_to": {"key": "✅"}}},
                {"event_id": "$r2", "sender": "@c:b", "origin_server_ts": 1787011200000,
                 "content": {"m.relates_to": {"key": "✅"}}},
                {"event_id": "$r3", "sender": "@a:b", "origin_server_ts": 1787011200000,
                 "content": {"m.relates_to": {"key": "👀"}}},
            ]}
            data = json.loads(await matrix_get_reactions("!r:b", "$msg"))

        assert data["count"] == 3
        top = data["by_emoji"][0]
        assert top["emoji"] == "✅" and top["count"] == 2
        assert set(top["senders"]) == {"@a:b", "@c:b"}
        # C'est cet identifiant qu'il faut pour retirer une réaction.
        assert data["reactions"][0]["reaction_event_id"] == "$r1"

    async def test_it_reads_the_v1_relations_route(self) -> None:
        with patch("matrix_mcp.server._get", new_callable=AsyncMock) as get:
            get.return_value = {"chunk": []}
            await matrix_get_reactions("!r:b", "$msg")

        assert "_matrix/client/v1/" in get.call_args[0][0]
        assert "m.annotation" in get.call_args[0][0]


class TestIndexMatchesReliability:
    """La note disait « ignore les filtres de salon » ; la mesure dit l'inverse.

    Constaté le 01/09/2026 : index_matches passe de 2613 à 2050 avec
    exclude_rooms — le filtre de salon est appliqué côté serveur. Le filtre
    d'expéditeur, lui, ne l'est pas.
    """

    async def test_a_room_filter_leaves_the_count_usable(self) -> None:
        with patch("matrix_mcp.server._post", new_callable=AsyncMock) as post, \
             patch("matrix_mcp.server._get", new_callable=AsyncMock) as get:
            get.return_value = {}
            post.return_value = _search_response([_hit()], count=2050)
            data = json.loads(await matrix_search_messages("x", exclude_rooms="!bruit:b"))

        assert data["index_matches_reliable"] is True
        assert "REFLÈTE les filtres de salon" in data["index_matches_note"]

    async def test_a_sender_filter_marks_the_count_unusable(self) -> None:
        with patch("matrix_mcp.server._post", new_callable=AsyncMock) as post, \
             patch("matrix_mcp.server._get", new_callable=AsyncMock) as get:
            get.return_value = {}
            post.return_value = _search_response([_hit()], count=109)
            data = json.loads(await matrix_search_messages("x", sender="@bjean:inno3.fr"))

        # 109 annoncés pour 1 rendu : le drapeau évite qu'un appelant
        # automatique compare les deux.
        assert data["index_matches_reliable"] is False

    async def test_a_date_window_marks_it_unusable_too(self) -> None:
        with patch("matrix_mcp.server._post", new_callable=AsyncMock) as post, \
             patch("matrix_mcp.server._get", new_callable=AsyncMock) as get:
            get.return_value = {}
            post.return_value = _search_response([_hit()], count=50)
            data = json.loads(await matrix_search_messages("x", since="2026-08-01"))

        assert data["index_matches_reliable"] is False


class TestContextSkipsBodylessEvents:
    """Juste après un ménage, un contexte de 3 ne contenait que 3 suppressions."""

    @staticmethod
    def _api():
        async def fake_get(path, params=None):
            if "/context/" in path:
                return {
                    "events_before": [
                        {"event_id": "$vide1", "content": {}},
                        {"event_id": "$vide2", "content": {}},
                        {"event_id": "$reel", "sender": "@a:b",
                         "content": {"body": "un vrai message"}},
                    ],
                    "event": {"event_id": "$e", "content": {"body": "cible"}},
                    "events_after": [{"event_id": "$a", "content": {"body": "après"}}],
                }
            return {"name": "salon"}

        return fake_get

    async def test_bodyless_events_are_dropped_by_default(self) -> None:
        with patch("matrix_mcp.server._get", new=AsyncMock(side_effect=self._api())):
            data = json.loads(await matrix_get_event_context("!r:b", "$e", before=3, after=3))

        assert [e["event_id"] for e in data["before"]] == ["$reel"]
        assert data["bodyless_included"] is False

    async def test_they_can_be_kept_on_demand(self) -> None:
        with patch("matrix_mcp.server._get", new=AsyncMock(side_effect=self._api())):
            data = json.loads(await matrix_get_event_context(
                "!r:b", "$e", before=3, after=3, include_bodyless=True))

        assert len(data["before"]) == 3


class TestUnreadState:
    """« Qu'est-ce qui est arrivé depuis mon dernier passage » — rien ne le disait."""

    @staticmethod
    def _sync() -> dict:
        return {"rooms": {"join": {
            "!calme:b": {"unread_notifications": {"notification_count": 0,
                                                  "highlight_count": 0},
                         "state": {"events": [{"type": "m.room.name",
                                               "content": {"name": "Calme"}}]},
                         "timeline": {"events": []}},
            "!actif:b": {"unread_notifications": {"notification_count": 12,
                                                  "highlight_count": 0},
                         "state": {"events": [{"type": "m.room.name",
                                               "content": {"name": "Notifications"}}]},
                         "timeline": {"events": [
                             {"type": "m.room.message", "event_id": "$m", "sender": "@bot:b",
                              "origin_server_ts": 1787011200000,
                              "content": {"body": "alerte"}}]}},
            "!mention:b": {"unread_notifications": {"notification_count": 2,
                                                    "highlight_count": 1},
                           "state": {"events": []}, "timeline": {"events": []}},
        }}}

    async def test_rooms_with_mentions_come_first(self) -> None:
        with patch("matrix_mcp.server._get", new_callable=AsyncMock) as get:
            get.return_value = self._sync()
            data = json.loads(await matrix_list_unread())

        assert data["rooms"][0]["room_id"] == "!mention:b"   # highlight prioritaire
        assert data["rooms"][0]["highlights"] == 1
        assert data["count"] == 2                            # le salon calme est écarté
        assert data["total_unread"] == 14

    async def test_the_sync_payload_is_kept_minimal(self) -> None:
        with patch("matrix_mcp.server._get", new_callable=AsyncMock) as get:
            get.return_value = {"rooms": {"join": {}}}
            await matrix_list_unread()

        f = json.loads(get.call_args[0][1]["filter"])
        # Sans ce filtre, /sync rapatrie l'historique récent de tous les salons.
        assert f["room"]["timeline"]["limit"] == 1
        assert f["presence"]["types"] == []
        assert get.call_args[0][1]["timeout"] == 0

    async def test_quiet_rooms_can_be_listed_too(self) -> None:
        with patch("matrix_mcp.server._get", new_callable=AsyncMock) as get:
            get.return_value = self._sync()
            data = json.loads(await matrix_list_unread(only_unread=False))

        assert data["count"] == 3

    async def test_marking_read_uses_the_latest_message_by_default(self) -> None:
        async def fake_get(path, params=None):
            if "/messages" in path:
                return {"chunk": [
                    {"type": "m.reaction", "event_id": "$r", "content": {"key": "x"}},
                    {"type": "m.room.message", "event_id": "$dernier",
                     "content": {"body": "coucou"}},
                ]}
            return {"rooms": {"join": {"!r:b": {
                "unread_notifications": {"notification_count": 0, "highlight_count": 0},
                "state": {"events": []}, "timeline": {"events": []}}}}}

        with patch("matrix_mcp.server._get", new=AsyncMock(side_effect=fake_get)), \
             patch("matrix_mcp.server._post", new_callable=AsyncMock) as post:
            data = json.loads(await matrix_mark_read("!r:b"))

        path, body = post.call_args[0]
        assert path.endswith("/read_markers")
        # Une réaction ne fait pas un repère de lecture.
        assert body == {"m.fully_read": "$dernier", "m.read": "$dernier"}
        assert data["success"] is True
        assert data["unread_after"] == 0

    async def test_an_explicit_event_is_honoured(self) -> None:
        with patch("matrix_mcp.server._get", new_callable=AsyncMock) as get, \
             patch("matrix_mcp.server._post", new_callable=AsyncMock) as post:
            get.return_value = {"rooms": {"join": {}}}
            await matrix_mark_read("!r:b", event_id="$choisi")

        assert post.call_args[0][1]["m.read"] == "$choisi"

    async def test_a_room_without_messages_is_refused_cleanly(self) -> None:
        with patch("matrix_mcp.server._get", new_callable=AsyncMock) as get, \
             patch("matrix_mcp.server._post", new_callable=AsyncMock) as post:
            get.return_value = {"chunk": []}
            data = json.loads(await matrix_mark_read("!vide:b"))

        assert data["success"] is False
        post.assert_not_called()


class TestEmptyFirstPageIsFixed:
    """Un salon dont la tête de chronologie est faite de suppressions.

    Constaté le 01/09/2026 : limit=5 rendait 0 message, pages_fetched=5,
    has_more=true, limit_reached=false — le budget de pages sautait avant
    d'avoir trouvé le moindre message. Ce sont les salons de notification,
    les plus consultés.
    """

    @staticmethod
    def _rooms(dead_pages: int):
        pages = []
        for i in range(dead_pages):
            pages.append({"chunk": [
                {"type": "m.room.message", "event_id": f"$sup{i}{j}", "content": {}}
                for j in range(4)
            ], "end": f"t{i}"})
        pages.append({"chunk": [
            {"type": "m.room.message", "event_id": f"$m{j}", "sender": "@a:b",
             "origin_server_ts": 1787011200000, "content": {"body": f"réel {j}"}}
            for j in range(5)
        ], "end": "tfin"})
        it = iter(pages)
        return lambda *a, **k: next(it)

    async def test_it_pushes_past_blocks_of_redactions(self) -> None:
        with patch("matrix_mcp.server._get", new=AsyncMock(side_effect=self._rooms(6))):
            data = json.loads(await matrix_get_room_messages("!notif:b", limit=5))

        # Avec l'ancien budget de 5 pages, on rendait 0 message.
        assert data["count"] == 5
        assert data["limit_reached"] is True

    async def test_raw_pages_are_oversampled(self) -> None:
        with patch("matrix_mcp.server._get", new_callable=AsyncMock) as get:
            get.return_value = {"chunk": [], "end": None}
            await matrix_get_room_messages("!r:b", limit=10)

        # Demander exactement `limit` événements garantit d'en manquer après
        # filtrage.
        assert get.call_args.kwargs["params"]["limit"] == "40"

    async def test_an_empty_page_explains_itself(self) -> None:
        with patch("matrix_mcp.server._get", new_callable=AsyncMock) as get:
            get.return_value = {"chunk": [
                {"type": "m.room.message", "event_id": "$s", "content": {}}
            ], "end": "t1"}
            data = json.loads(await matrix_get_room_messages("!r:b", limit=5))

        assert data["count"] == 0
        assert "empty_page_note" in data
        assert "suppressions" in data["empty_page_note"]
        assert data["events_scanned"] > 0

    async def test_messages_carry_room_id_and_permalink(self) -> None:
        with patch("matrix_mcp.server._get", new_callable=AsyncMock) as get:
            get.return_value = {"chunk": [
                {"type": "m.room.message", "event_id": "$m", "sender": "@a:b",
                 "origin_server_ts": 1787011200000, "content": {"body": "x"}}
            ], "end": None}
            data = json.loads(await matrix_get_room_messages("!r:inno3.eu", limit=5))

        m = data["messages"][0]
        assert m["room_id"] == "!r:inno3.eu"
        assert m["permalink"].startswith("https://matrix.to/#/")
        assert m["type"] == "m.room.message"

    async def test_a_date_window_applies_to_the_timeline_too(self) -> None:
        with patch("matrix_mcp.server._get", new_callable=AsyncMock) as get:
            get.return_value = {"chunk": [
                {"type": "m.room.message", "event_id": "$vieux", "sender": "@a:b",
                 "origin_server_ts": 1750000000000, "content": {"body": "juin 2025"}},
                {"type": "m.room.message", "event_id": "$recent", "sender": "@a:b",
                 "origin_server_ts": 1787011200000, "content": {"body": "août 2026"}},
            ], "end": None}
            data = json.loads(await matrix_get_room_messages(
                "!r:b", limit=10, since="2026-08-01"))

        assert [m["event_id"] for m in data["messages"]] == ["$recent"]


class TestRedactingAMessageTakesItsEdits:
    """Une correction est un événement distinct : elle survivait à la racine."""

    async def test_replacements_are_redacted_first(self) -> None:
        async def fake_get(path, params=None):
            if "m.replace" in path:
                return {"chunk": [{"event_id": "$edit1"}, {"event_id": "$edit2"}]}
            return {"event_id": "$msg", "content": {}}

        with patch("matrix_mcp.server._get", new=AsyncMock(side_effect=fake_get)), \
             patch("matrix_mcp.server._put", new_callable=AsyncMock) as put:
            put.return_value = {"event_id": "$redaction"}
            data = json.loads(await matrix_redact_message("!r:b", "$msg"))

        paths = [c[0][0] for c in put.call_args_list]
        # Les corrections partent AVANT la racine : une fois la racine partie
        # elles resteraient seules et lisibles. Les identifiants voyagent
        # encodés dans le chemin.
        assert "%24edit1" in paths[0]
        assert "%24edit2" in paths[1]
        assert "%24msg" in paths[2]
        assert data["edits_redacted"] == ["$edit1", "$edit2"]

    async def test_it_can_be_turned_off(self) -> None:
        with patch("matrix_mcp.server._get", new_callable=AsyncMock) as get, \
             patch("matrix_mcp.server._put", new_callable=AsyncMock) as put:
            get.return_value = {"event_id": "$msg", "content": {}}
            put.return_value = {"event_id": "$redaction"}
            data = json.loads(await matrix_redact_message(
                "!r:b", "$msg", include_edits=False))

        assert len(put.call_args_list) == 1
        assert "edits_redacted" not in data

    async def test_a_failure_on_edits_does_not_block_the_root(self) -> None:
        async def fake_get(path, params=None):
            if "m.replace" in path:
                raise RuntimeError("relations indisponibles")
            return {"event_id": "$msg", "content": {}}

        with patch("matrix_mcp.server._get", new=AsyncMock(side_effect=fake_get)), \
             patch("matrix_mcp.server._put", new_callable=AsyncMock) as put:
            put.return_value = {"event_id": "$redaction"}
            data = json.loads(await matrix_redact_message("!r:b", "$msg"))

        assert data["success"] is True


class TestListFiles:
    """La recherche n'indexe pas le corps des m.image et m.file."""

    async def test_the_contains_url_filter_is_used(self) -> None:
        with patch("matrix_mcp.server._get", new_callable=AsyncMock) as get:
            get.return_value = {"chunk": [], "end": None}
            await matrix_list_files("!r:b")

        f = json.loads(get.call_args.kwargs["params"]["filter"])
        assert f["contains_url"] is True
        assert f["types"] == ["m.room.message"]

    async def test_attachments_are_described(self) -> None:
        with patch("matrix_mcp.server._get", new_callable=AsyncMock) as get:
            get.return_value = {"chunk": [
                {"event_id": "$f", "sender": "@a:b", "origin_server_ts": 1787011200000,
                 "content": {"msgtype": "m.file", "body": "rapport.pdf",
                             "filename": "rapport.pdf", "url": "mxc://x/y",
                             "info": {"mimetype": "application/pdf", "size": 393}}},
                {"event_id": "$t", "content": {"msgtype": "m.text", "body": "sans url"}},
            ], "end": "t1"}
            data = json.loads(await matrix_list_files("!r:inno3.eu"))

        assert data["count"] == 1                    # le message texte est écarté
        f = data["files"][0]
        assert f["filename"] == "rapport.pdf"
        assert f["content_uri"] == "mxc://x/y"
        assert f["size"] == 393
        assert f["permalink"].startswith("https://matrix.to/#/")
