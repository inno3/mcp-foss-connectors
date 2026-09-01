"""Tests unitaires pour le serveur MCP WordPress.

Cible principale : exposition du markup Gutenberg raw via context=edit
(régression du bug constaté le 22/05/2026 sur wordpress_get_custom_post).
"""

import json
import os
from unittest.mock import AsyncMock, patch

import pytest

os.environ.setdefault("WP_PROD_URL", "https://example.test")
os.environ.setdefault("WP_PROD_USER", "tester")
os.environ.setdefault("WP_PROD_APP_PASSWORD", "xxxx xxxx xxxx xxxx xxxx xxxx")

import wordpress_mcp.server as wp  # noqa: E402
from wordpress_mcp.server import (  # noqa: E402
    _content_fields,
    _fetch_active_plugins,
    _format_page,
    _format_post,
    _parse_xmlrpc_software_version,
    _strip_html,
    wordpress_get_custom_post,
    wordpress_get_page,
    wordpress_get_post,
)

# ---------------------------------------------------------------------------
# Fixtures : réponses REST WordPress simulées
# ---------------------------------------------------------------------------

GUTENBERG_RAW = (
    "<!-- wp:columns -->\n"
    "<div class=\"wp-block-columns\">"
    "<!-- wp:column -->"
    "<div class=\"wp-block-column\">"
    "<!-- wp:paragraph -->\n<p>Bonjour&nbsp;!</p>\n<!-- /wp:paragraph -->"
    "</div>"
    "<!-- /wp:column -->"
    "</div>\n"
    "<!-- /wp:columns -->"
)

# Ce que wpautop / le filtre WP produirait
GUTENBERG_RENDERED = (
    "<div class=\"wp-block-columns\">"
    "<div class=\"wp-block-column\">"
    "<p>Bonjour&nbsp;!</p>"
    "</div>"
    "</div>"
)


def _make_post_payload(context: str) -> dict:
    """Reproduit la forme renvoyée par /wp/v2/<type>/<id>?context=<context>."""
    title = {"rendered": "Cursus &amp; outils"}
    excerpt = {"rendered": "<p>Un &rsquo;tit extrait</p>"}
    content: dict = {"rendered": GUTENBERG_RENDERED}
    if context == "edit":
        # Mode "edit" : WP expose AUSSI .raw sur title/content/excerpt
        title["raw"] = "Cursus & outils"
        excerpt["raw"] = "Un 'tit extrait"
        content["raw"] = GUTENBERG_RAW
    return {
        "id": 9203,
        "title": title,
        "slug": "cursus-formation",
        "status": "publish",
        "link": "https://example.test/formation/cursus-formation/",
        "date": "2026-04-01T10:00:00",
        "modified": "2026-05-22T09:30:00",
        "content": content,
        "excerpt": excerpt,
        "acf": {"foo": "bar"},
        "categories": [],
        "tags": [],
        "parent": 0,
        "menu_order": 0,
        "template": "",
        "author": 4,
    }


# ---------------------------------------------------------------------------
# Helpers — _content_fields
# ---------------------------------------------------------------------------

class TestContentFields:
    def test_edit_exposes_raw_gutenberg_markup(self) -> None:
        p = _make_post_payload("edit")
        fields = _content_fields(p, "edit")
        assert "<!-- wp:columns" in fields["content"]
        assert "<!-- wp:paragraph" in fields["content"]
        assert fields["content_raw"] == GUTENBERG_RAW
        assert fields["content_html"] == GUTENBERG_RENDERED

    def test_edit_exposes_title_and_excerpt_raw(self) -> None:
        p = _make_post_payload("edit")
        fields = _content_fields(p, "edit")
        assert fields["title"] == "Cursus &amp; outils"
        assert fields["title_raw"] == "Cursus & outils"
        assert fields["excerpt"] == "Un 'tit extrait"
        assert fields["excerpt_raw"] == "Un 'tit extrait"

    def test_view_returns_rendered_only(self) -> None:
        p = _make_post_payload("view")
        fields = _content_fields(p, "view")
        # Pas de commentaires Gutenberg en mode view
        assert "<!-- wp:" not in fields["content"]
        assert fields["content"] == GUTENBERG_RENDERED
        assert fields["content_html"] == GUTENBERG_RENDERED
        assert "content_raw" not in fields
        assert "title_raw" not in fields
        # excerpt est strippé de son HTML
        assert "<" not in fields["excerpt"]

    def test_edit_falls_back_to_rendered_when_raw_empty(self) -> None:
        """Une page non-Gutenberg (Classic editor) peut avoir un raw vide."""
        p = {
            "title": {"rendered": "T", "raw": "T"},
            "content": {"rendered": "<p>Hello</p>", "raw": ""},
            "excerpt": {"rendered": "", "raw": ""},
        }
        fields = _content_fields(p, "edit")
        assert fields["content"] == "<p>Hello</p>"
        assert "content_raw" not in fields


# ---------------------------------------------------------------------------
# Formatters de haut niveau
# ---------------------------------------------------------------------------

class TestFormatters:
    def test_format_page_edit_includes_gutenberg(self) -> None:
        p = _make_post_payload("edit")
        out = _format_page(p, include_content=True, context="edit")
        assert "<!-- wp:columns" in out["content"]
        assert out["content_raw"] == GUTENBERG_RAW
        assert out["acf"] == {"foo": "bar"}

    def test_format_page_view_strips_gutenberg(self) -> None:
        p = _make_post_payload("view")
        out = _format_page(p, include_content=True, context="view")
        assert "<!-- wp:" not in out["content"]
        assert "content_raw" not in out

    def test_format_post_edit_includes_gutenberg(self) -> None:
        p = _make_post_payload("edit")
        out = _format_post(p, include_content=True, context="edit")
        assert "<!-- wp:columns" in out["content"]
        assert out["content_raw"] == GUTENBERG_RAW

    def test_format_page_summary_without_content(self) -> None:
        p = _make_post_payload("edit")
        out = _format_page(p, include_content=False)
        # Mode liste : pas de content, excerpt strippé
        assert "content" not in out
        assert "content_raw" not in out
        assert "<" not in out["excerpt"]


# ---------------------------------------------------------------------------
# Tools — mocks de l'appel REST
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
class TestGetCustomPost:
    async def test_default_returns_raw_gutenberg(self) -> None:
        """wordpress_get_custom_post() sans context → doit exposer le markup raw."""
        with patch(
            "wordpress_mcp.server._wp_get",
            new_callable=AsyncMock,
            return_value=_make_post_payload("edit"),
        ) as mock_get:
            raw = await wordpress_get_custom_post(
                post_type="formation",
                post_id=9203,
                server="prod",
            )
        # 1. L'appel REST utilise bien context=edit
        args, kwargs = mock_get.call_args
        # _wp_get(server, endpoint, params, subsite=...)
        assert args[1] == "formation/9203"
        assert args[2] == {"context": "edit"}
        # 2. La réponse JSON contient les commentaires Gutenberg
        data = json.loads(raw)
        assert "<!-- wp:columns" in data["content"]
        assert "<!-- wp:paragraph" in data["content"]
        assert data["content_raw"] == GUTENBERG_RAW
        assert data["content_html"] == GUTENBERG_RENDERED
        assert data["title_raw"] == "Cursus & outils"
        assert data["acf"] == {"foo": "bar"}

    async def test_context_view_returns_rendered(self) -> None:
        with patch(
            "wordpress_mcp.server._wp_get",
            new_callable=AsyncMock,
            return_value=_make_post_payload("view"),
        ) as mock_get:
            raw = await wordpress_get_custom_post(
                post_type="formation",
                post_id=9203,
                server="prod",
                context="view",
            )
        args, _ = mock_get.call_args
        assert args[2] == {"context": "view"}
        data = json.loads(raw)
        assert "<!-- wp:" not in data["content"]
        assert data["content"] == GUTENBERG_RENDERED
        assert "content_raw" not in data
        assert "title_raw" not in data

    async def test_invalid_context_falls_back_to_edit(self) -> None:
        with patch(
            "wordpress_mcp.server._wp_get",
            new_callable=AsyncMock,
            return_value=_make_post_payload("edit"),
        ) as mock_get:
            await wordpress_get_custom_post(
                post_type="formation",
                post_id=9203,
                server="prod",
                context="bogus",
            )
        args, _ = mock_get.call_args
        assert args[2] == {"context": "edit"}


@pytest.mark.asyncio
class TestGetPage:
    async def test_default_returns_raw_gutenberg(self) -> None:
        with patch(
            "wordpress_mcp.server._wp_get",
            new_callable=AsyncMock,
            return_value=_make_post_payload("edit"),
        ) as mock_get:
            raw = await wordpress_get_page(page_id=553, server="prod")
        args, _ = mock_get.call_args
        assert args[1] == "pages/553"
        assert args[2] == {"context": "edit"}
        data = json.loads(raw)
        assert "<!-- wp:columns" in data["content"]
        assert data["content_raw"] == GUTENBERG_RAW

    async def test_view_mode(self) -> None:
        with patch(
            "wordpress_mcp.server._wp_get",
            new_callable=AsyncMock,
            return_value=_make_post_payload("view"),
        ):
            raw = await wordpress_get_page(page_id=553, server="prod", context="view")
        data = json.loads(raw)
        assert "<!-- wp:" not in data["content"]


@pytest.mark.asyncio
class TestGetPost:
    async def test_default_returns_raw_gutenberg(self) -> None:
        with patch(
            "wordpress_mcp.server._wp_get",
            new_callable=AsyncMock,
            return_value=_make_post_payload("edit"),
        ) as mock_get:
            raw = await wordpress_get_post(post_id=42, server="prod")
        args, _ = mock_get.call_args
        assert args[1] == "posts/42"
        assert args[2] == {"context": "edit"}
        data = json.loads(raw)
        assert "<!-- wp:columns" in data["content"]


# ---------------------------------------------------------------------------
# Sanity check sur _strip_html (au passage)
# ---------------------------------------------------------------------------

def test_strip_html_removes_tags() -> None:
    assert _strip_html("<p>Hello <b>world</b></p>") == "Hello world"
    assert _strip_html("") == ""
    assert _strip_html(None) == ""


# ---------------------------------------------------------------------------
# Routage des sous-sites — générique, piloté par WP_<SERVER>_SUBSITES
# ---------------------------------------------------------------------------

class TestSubsiteRouting:
    """Le routage multisite/multilingue ne doit reposer sur AUCUNE convention
    inno³ : tout passe par la config par-serveur (sous-répertoire, sous-domaine,
    ou paramètre de requête)."""

    def test_default_main_site(self) -> None:
        base = wp._SERVER_CONFIGS["prod"]["url"]
        assert wp._api_url("prod", "posts") == f"{base}/wp-json/wp/v2/posts"

    def test_unknown_subsite_falls_back_to_main(self) -> None:
        base = wp._SERVER_CONFIGS["prod"]["url"]
        assert wp._api_url("prod", "posts", subsite="zz") == f"{base}/wp-json/wp/v2/posts"

    def test_path_prefix_subsite(self) -> None:
        base = wp._SERVER_CONFIGS["prod"]["url"]
        wp._SERVER_CONFIGS["prod"]["subsites"] = {"en": "/en"}
        try:
            assert wp._api_url("prod", "posts", "en") == f"{base}/en/wp-json/wp/v2/posts"
        finally:
            wp._SERVER_CONFIGS["prod"]["subsites"] = {}

    def test_alternate_base_url_subsite(self) -> None:
        wp._SERVER_CONFIGS["prod"]["subsites"] = {"de": "https://de.example.test"}
        try:
            assert (
                wp._api_url("prod", "posts", "de")
                == "https://de.example.test/wp-json/wp/v2/posts"
            )
        finally:
            wp._SERVER_CONFIGS["prod"]["subsites"] = {}

    def test_query_param_subsite(self) -> None:
        wp._SERVER_CONFIGS["prod"]["subsites"] = {"en": {"lang": "en"}}
        try:
            assert wp._merge_params("prod", "en", {"per_page": 5}) == {
                "per_page": 5,
                "lang": "en",
            }
        finally:
            wp._SERVER_CONFIGS["prod"]["subsites"] = {}

    def test_parse_subsites_invalid(self) -> None:
        assert wp._parse_subsites("") == {}
        assert wp._parse_subsites("not json") == {}
        assert wp._parse_subsites('{"en":"/en"}') == {"en": "/en"}


# --------------------------------------------------------------------------
# Version du core et extensions actives (portés avec les helpers, 11/08/2026)
# --------------------------------------------------------------------------


XMLRPC_FAULT = (
    '<?xml version="1.0"?><methodResponse><fault><value><struct>'
    "<member><name>faultCode</name><value><int>403</int></value></member>"
    "</struct></value></fault></methodResponse>"
)


XMLRPC_OK = (
    '<?xml version="1.0" encoding="UTF-8"?><methodResponse><params><param><value>'
    "<struct><member><name>software_version</name><value><struct>"
    "<member><name>desc</name><value><string>Version du logiciel</string></value></member>"
    "<member><name>readonly</name><value><boolean>1</boolean></value></member>"
    "<member><name>value</name><value><string>7.0.2</string></value></member>"
    "</struct></value></member></struct>"
    "</value></param></params></methodResponse>"
)


class TestParseXmlrpcSoftwareVersion:
    """La version du core n'est exposée nulle part dans l'API REST."""

    def test_extrait_la_version(self) -> None:
        assert _parse_xmlrpc_software_version(XMLRPC_OK) == "7.0.2"

    def test_fault_retourne_none(self) -> None:
        assert _parse_xmlrpc_software_version(XMLRPC_FAULT) is None

    def test_xml_invalide_retourne_none(self) -> None:
        assert _parse_xmlrpc_software_version("<pas du xml") is None


@pytest.mark.asyncio
class TestFetchActivePlugins:
    """Liste des extensions actives avec version — matière première d'un audit CVE."""

    async def test_ne_garde_que_les_actives(self) -> None:
        payload = [
            {"name": "ACF PRO", "version": "6.8.6", "plugin": "acf/acf", "status": "network-active"},
            {"name": "Autre", "version": "1.0.0", "plugin": "autre/autre", "status": "active"},
            {"name": "Désactivée", "version": "0.1", "plugin": "off/off", "status": "inactive"},
        ]
        with patch("wordpress_mcp.server._wp_get", new_callable=AsyncMock, return_value=payload):
            plugins, note = await _fetch_active_plugins("prod")
        assert note == ""
        assert [p["name"] for p in plugins] == ["ACF PRO", "Autre"]
        assert plugins[0]["version"] == "6.8.6"

    async def test_droits_insuffisants_explique_le_manque(self) -> None:
        import httpx

        request = httpx.Request("GET", "https://example.test/wp-json/wp/v2/plugins")
        exc = httpx.HTTPStatusError(
            "forbidden", request=request, response=httpx.Response(403, request=request)
        )
        with patch("wordpress_mcp.server._wp_get", new_callable=AsyncMock, side_effect=exc):
            plugins, note = await _fetch_active_plugins("prod")
        assert plugins is None
        assert "activate_plugins" in note


# ---------------------------------------------------------------------------
# Garde-fou Gutenberg à l'écriture
#
# Réécrire un article à blocs sans ses délimiteurs `<!-- wp:` le fait retomber
# en contenu classique : wpautop redevient actif et injecte des paragraphes
# vides. L'API REST accepte l'écriture sans rien signaler : c'est ce qui rend
# le garde-fou nécessaire côté connecteur.
# ---------------------------------------------------------------------------

CLASSIC_HTML = "<h2>Titre</h2>\n<p>Un paragraphe sans bloc.</p>"


def _current_content(raw: str) -> dict:
    return {"id": 1234, "content": {"raw": raw, "rendered": raw}}


@pytest.mark.asyncio
class TestGutenbergDowngradeWarning:
    async def test_alerte_quand_les_blocs_disparaissent(self) -> None:
        with patch(
            "wordpress_mcp.server._wp_get",
            new_callable=AsyncMock,
            return_value=_current_content(GUTENBERG_RAW),
        ):
            warning = await wp._gutenberg_downgrade_warning(
                "prod", "posts/1234", CLASSIC_HTML
            )
        assert warning is not None
        assert "wpautop" in warning

    async def test_silencieux_si_le_contenu_envoye_porte_des_blocs(self) -> None:
        with patch(
            "wordpress_mcp.server._wp_get", new_callable=AsyncMock
        ) as mock_get:
            warning = await wp._gutenberg_downgrade_warning(
                "prod", "posts/1234", GUTENBERG_RAW
            )
        assert warning is None
        mock_get.assert_not_called()

    async def test_silencieux_si_le_contenu_actuel_est_deja_classique(self) -> None:
        with patch(
            "wordpress_mcp.server._wp_get",
            new_callable=AsyncMock,
            return_value=_current_content(CLASSIC_HTML),
        ):
            warning = await wp._gutenberg_downgrade_warning(
                "prod", "posts/1234", CLASSIC_HTML
            )
        assert warning is None

    async def test_silencieux_si_la_lecture_de_controle_echoue(self) -> None:
        with patch(
            "wordpress_mcp.server._wp_get",
            new_callable=AsyncMock,
            side_effect=RuntimeError("403"),
        ):
            warning = await wp._gutenberg_downgrade_warning(
                "prod", "posts/1234", CLASSIC_HTML
            )
        assert warning is None

    async def test_update_post_remonte_l_alerte(self) -> None:
        with patch(
            "wordpress_mcp.server._wp_get",
            new_callable=AsyncMock,
            return_value=_current_content(GUTENBERG_RAW),
        ), patch(
            "wordpress_mcp.server._wp_put",
            new_callable=AsyncMock,
            return_value={"id": 1234, "title": {"rendered": "T"}, "status": "publish"},
        ):
            data = json.loads(
                await wp.wordpress_update_post(
                    post_id=1234, server="prod", content=CLASSIC_HTML
                )
            )
        assert data["updated"] is True
        assert "wpautop" in data["warning"]

    async def test_update_post_sans_alerte_quand_les_blocs_sont_conserves(self) -> None:
        with patch(
            "wordpress_mcp.server._wp_get",
            new_callable=AsyncMock,
            return_value=_current_content(GUTENBERG_RAW),
        ), patch(
            "wordpress_mcp.server._wp_put",
            new_callable=AsyncMock,
            return_value={"id": 1234, "title": {"rendered": "T"}, "status": "publish"},
        ):
            data = json.loads(
                await wp.wordpress_update_post(
                    post_id=1234, server="prod", content=GUTENBERG_RAW
                )
            )
        assert "warning" not in data

    async def test_update_post_sans_contenu_ne_lit_rien(self) -> None:
        with patch(
            "wordpress_mcp.server._wp_get", new_callable=AsyncMock
        ) as mock_get, patch(
            "wordpress_mcp.server._wp_put",
            new_callable=AsyncMock,
            return_value={"id": 1234, "title": {"rendered": "T"}, "status": "draft"},
        ):
            data = json.loads(
                await wp.wordpress_update_post(
                    post_id=1234, server="prod", status="draft"
                )
            )
        mock_get.assert_not_called()
        assert "warning" not in data

    async def test_update_custom_post_remonte_l_alerte(self) -> None:
        with patch(
            "wordpress_mcp.server._wp_get",
            new_callable=AsyncMock,
            return_value=_current_content(GUTENBERG_RAW),
        ), patch(
            "wordpress_mcp.server._wp_put",
            new_callable=AsyncMock,
            return_value={"id": 42, "title": {"rendered": "T"}, "status": "publish"},
        ):
            data = json.loads(
                await wp.wordpress_update_custom_post(
                    post_type="blog", post_id=42, server="prod", content=CLASSIC_HTML
                )
            )
        assert "wpautop" in data["warning"]

    async def test_update_page_remonte_l_alerte(self) -> None:
        with patch(
            "wordpress_mcp.server._wp_get",
            new_callable=AsyncMock,
            return_value=_current_content(GUTENBERG_RAW),
        ), patch(
            "wordpress_mcp.server._wp_put",
            new_callable=AsyncMock,
            return_value={"id": 7, "title": {"rendered": "T"}, "status": "publish"},
        ):
            data = json.loads(
                await wp.wordpress_update_page(
                    page_id=7, server="prod", content=CLASSIC_HTML
                )
            )
        assert "wpautop" in data["warning"]


# ---------------------------------------------------------------------------
# Contrôle de contenu, révisions, articles natifs, étiquettes
# ---------------------------------------------------------------------------

WPAUTOP_RENDERED = (
    "<p></p>\n<h2>Titre</h2>\n<p>&nbsp;</p>\n"
    "<p>Un paragraphe.</p>\n<p> </p>\n"
    "<img src=\"a.png\" alt=\"une image\">\n<img src=\"b.png\">"
)


class TestContentHealth:
    def test_repere_les_paragraphes_vides_du_rendu(self) -> None:
        health = wp._content_health(CLASSIC_HTML, WPAUTOP_RENDERED)
        assert health["empty_paragraphs"] == 3
        assert health["measured_on"] == "rendered"
        assert health["clean"] is False

    def test_compte_les_blocs_sur_la_source_pas_sur_le_rendu(self) -> None:
        # Le rendu ne porte jamais de délimiteurs : ils doivent venir du raw.
        health = wp._content_health(GUTENBERG_RAW, GUTENBERG_RENDERED)
        assert health["blocks"] > 0
        assert health["empty_paragraphs"] == 0

    def test_images_sans_alternative(self) -> None:
        health = wp._content_health("", WPAUTOP_RENDERED)
        assert health["images"] == 2
        assert health["images_without_alt"] == 1

    def test_alt_vide_compte_comme_manquant(self) -> None:
        health = wp._content_health('<img src="a.png" alt="  ">')
        assert health["images_without_alt"] == 1

    def test_h1_en_double(self) -> None:
        health = wp._content_health("<h1>A</h1><h1>B</h1>")
        assert health["h1"] == 2
        assert any("niveau 1" in i for i in health["issues"])

    def test_tirets_cadratins_dans_le_corps(self) -> None:
        health = wp._content_health("<!-- wp:paragraph --><p>un texte — coupé</p>")
        assert health["em_dashes"] == 1

    def test_les_delimiteurs_ne_comptent_pas_comme_du_texte(self) -> None:
        health = wp._content_health(GUTENBERG_RAW)
        assert "wp:columns" not in " ".join(health["issues"])
        assert health["words"] > 0

    def test_contenu_sain(self) -> None:
        health = wp._content_health(
            "<!-- wp:paragraph -->\n<p>Bonjour.</p>\n<!-- /wp:paragraph -->"
        )
        assert health["clean"] is True
        assert health["issues"] == []


class TestParseIdList:
    def test_liste_simple(self) -> None:
        assert wp._parse_id_list("3, 7,12") == [3, 7, 12]

    def test_vide_donne_none(self) -> None:
        assert wp._parse_id_list("") is None
        assert wp._parse_id_list("   ") is None


def _revision(rev_id: int, raw: str) -> dict:
    return {
        "id": rev_id,
        "parent": 1234,
        "author": 4,
        "modified": "2026-08-31T13:00:00",
        "title": {"raw": "Titre", "rendered": "Titre"},
        "content": {"raw": raw, "rendered": raw},
        "excerpt": {"raw": "", "rendered": ""},
    }


@pytest.mark.asyncio
class TestRevisions:
    async def test_liste_signale_la_perte_des_blocs(self) -> None:
        with patch(
            "wordpress_mcp.server._wp_get",
            new_callable=AsyncMock,
            return_value=[_revision(2, CLASSIC_HTML), _revision(1, GUTENBERG_RAW)],
        ) as mock_get:
            data = json.loads(
                await wp.wordpress_list_revisions(post_id=1234, post_type="blog")
            )
        args, _ = mock_get.call_args
        assert args[1] == "blog/1234/revisions"
        assert args[2]["context"] == "edit"
        assert data["revisions"][0]["blocks"] == 0
        assert data["revisions"][1]["blocks"] > 0

    async def test_404_explique_le_support_des_revisions(self) -> None:
        import httpx

        request = httpx.Request("GET", "https://example.test/x")
        exc = httpx.HTTPStatusError(
            "not found", request=request, response=httpx.Response(404, request=request)
        )
        with patch("wordpress_mcp.server._wp_get", new_callable=AsyncMock, side_effect=exc):
            out = await wp.wordpress_list_revisions(post_id=1234, post_type="blog")
        assert "révisions" in out

    async def test_get_revision_expose_la_source_et_son_etat(self) -> None:
        with patch(
            "wordpress_mcp.server._wp_get",
            new_callable=AsyncMock,
            return_value=_revision(7, GUTENBERG_RAW),
        ):
            data = json.loads(
                await wp.wordpress_get_revision(
                    post_id=1234, revision_id=7, post_type="blog"
                )
            )
        assert data["content"] == GUTENBERG_RAW
        assert data["health"]["blocks"] > 0

    async def test_restore_reecrit_la_source_de_la_revision(self) -> None:
        async def fake_get(server, endpoint, params=None, subsite=""):
            if endpoint.endswith("/revisions/7"):
                return _revision(7, GUTENBERG_RAW)
            return {"id": 1234, "content": {"raw": CLASSIC_HTML}}

        with patch(
            "wordpress_mcp.server._wp_get", new_callable=AsyncMock, side_effect=fake_get
        ), patch(
            "wordpress_mcp.server._wp_put",
            new_callable=AsyncMock,
            return_value={"id": 1234, "status": "publish", "link": "https://x/y"},
        ) as mock_put:
            data = json.loads(
                await wp.wordpress_restore_revision(
                    post_id=1234, revision_id=7, post_type="blog"
                )
            )
        sent = mock_put.call_args[0][2]
        assert sent["content"] == GUTENBERG_RAW
        assert "title" not in sent  # restore_title=False par défaut
        assert data["restored"] is True
        assert data["restored_chars"] == len(GUTENBERG_RAW)
        assert data["replaced_chars"] == len(CLASSIC_HTML)
        assert "warning" not in data

    async def test_restore_title_renvoie_aussi_le_titre(self) -> None:
        async def fake_get(server, endpoint, params=None, subsite=""):
            if endpoint.endswith("/revisions/7"):
                return _revision(7, GUTENBERG_RAW)
            return {"id": 1234, "content": {"raw": ""}}

        with patch(
            "wordpress_mcp.server._wp_get", new_callable=AsyncMock, side_effect=fake_get
        ), patch(
            "wordpress_mcp.server._wp_put",
            new_callable=AsyncMock,
            return_value={"id": 1234, "status": "publish"},
        ) as mock_put:
            await wp.wordpress_restore_revision(
                post_id=1234, revision_id=7, post_type="blog", restore_title=True
            )
        assert mock_put.call_args[0][2]["title"] == "Titre"

    async def test_restore_d_une_revision_vide_est_refuse(self) -> None:
        with patch(
            "wordpress_mcp.server._wp_get",
            new_callable=AsyncMock,
            return_value=_revision(7, ""),
        ), patch("wordpress_mcp.server._wp_put", new_callable=AsyncMock) as mock_put:
            out = await wp.wordpress_restore_revision(post_id=1234, revision_id=7)
        assert "pas de contenu" in out
        mock_put.assert_not_called()


@pytest.mark.asyncio
class TestCheckContent:
    async def test_mesure_les_paragraphes_vides_sur_le_rendu(self) -> None:
        payload = {
            "id": 1234,
            "title": {"rendered": "SBOM et CRA"},
            "status": "publish",
            "link": "https://x/y",
            "modified": "2026-08-31T13:00:00",
            "content": {"raw": CLASSIC_HTML, "rendered": WPAUTOP_RENDERED},
        }
        with patch(
            "wordpress_mcp.server._wp_get", new_callable=AsyncMock, return_value=payload
        ) as mock_get:
            data = json.loads(
                await wp.wordpress_check_content(post_id=1234, post_type="blog")
            )
        assert mock_get.call_args[0][2] == {"context": "edit"}
        assert data["id"] == 1234
        assert data["empty_paragraphs"] == 3
        assert data["blocks"] == 0
        assert data["clean"] is False


@pytest.mark.asyncio
class TestCreateAndDeletePost:
    async def test_create_post_convertit_categories_et_tags(self) -> None:
        with patch(
            "wordpress_mcp.server._wp_post",
            new_callable=AsyncMock,
            return_value={"id": 5, "title": {"rendered": "T"}, "status": "draft"},
        ) as mock_post:
            data = json.loads(
                await wp.wordpress_create_post(
                    title="T", categories="3, 7", tags="12", featured_media=99
                )
            )
        sent = mock_post.call_args[0][2]
        assert sent["categories"] == [3, 7]
        assert sent["tags"] == [12]
        assert sent["featured_media"] == 99
        assert data["created"] is True

    async def test_delete_post_va_en_corbeille_par_defaut(self) -> None:
        with patch(
            "wordpress_mcp.server._wp_delete",
            new_callable=AsyncMock,
            return_value={"previous": {"title": {"rendered": "T"}}},
        ) as mock_del:
            data = json.loads(await wp.wordpress_delete_post(post_id=5))
        assert mock_del.call_args[0][2] == {"force": "false"}
        assert data["force"] is False


@pytest.mark.asyncio
class TestUpdatePostNouveauxChamps:
    async def test_envoie_slug_excerpt_et_taxonomies(self) -> None:
        with patch(
            "wordpress_mcp.server._wp_get", new_callable=AsyncMock
        ), patch(
            "wordpress_mcp.server._wp_put",
            new_callable=AsyncMock,
            return_value={"id": 5, "title": {"rendered": "T"}, "status": "publish"},
        ) as mock_put:
            await wp.wordpress_update_post(
                post_id=5, slug="mon-slug", excerpt="chapeau", categories="4", tags="8,9"
            )
        sent = mock_put.call_args[0][2]
        assert sent["slug"] == "mon-slug"
        assert sent["excerpt"] == "chapeau"
        assert sent["categories"] == [4]
        assert sent["tags"] == [8, 9]


@pytest.mark.asyncio
class TestListTags:
    async def test_liste_les_etiquettes(self) -> None:
        with patch(
            "wordpress_mcp.server._wp_get",
            new_callable=AsyncMock,
            return_value=[{"id": 8, "name": "SBOM", "slug": "sbom", "count": 3}],
        ) as mock_get:
            data = json.loads(await wp.wordpress_list_tags(search="sbom"))
        assert mock_get.call_args[0][1] == "tags"
        assert mock_get.call_args[0][2]["search"] == "sbom"
        assert data["tags"][0]["name"] == "SBOM"
