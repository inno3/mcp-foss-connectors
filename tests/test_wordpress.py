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
