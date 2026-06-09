# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 inno³ and contributors
"""
Serveur MCP générique pour WordPress (API REST + ACF).

Supporte deux instances simultanément :
  - "prod"  : site de destination (production)
  - "test"  : site source (serveur de recette)

Auth : HTTP Basic avec Application Password WordPress (WP 5.6+).
API  : WordPress REST API v2 (/wp-json/wp/v2/).
ACF  : lecture/écriture des champs ACF si show_in_rest activé.
Redirection plugin : gestion des redirections 301/302.
"""

import asyncio
import base64
import json
import logging
import mimetypes
import os
import re
from pathlib import Path
from typing import Any

import httpx
from mcp.server.fastmcp import FastMCP

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("wordpress-mcp")

mcp = FastMCP("wordpress")

# Chaque serveur = {url, user, password}
_SERVER_CONFIGS: dict[str, dict] = {}


def _init_servers() -> None:
    """Initialise les configs serveurs depuis les variables d'environnement."""
    global _SERVER_CONFIGS
    _SERVER_CONFIGS = {
        "prod": {
            "url": os.environ.get("WP_PROD_URL", "").rstrip("/"),
            "user": os.environ.get("WP_PROD_USER", ""),
            "password": os.environ.get("WP_PROD_APP_PASSWORD", ""),
            "verify_ssl": True,
            # Pas de Basic Auth serveur en prod
            "server_auth_user": "",
            "server_auth_pass": "",
            "subsites": _parse_subsites(os.environ.get("WP_PROD_SUBSITES", "")),
        },
        "test": {
            "url": os.environ.get("WP_TEST_URL", "").rstrip("/"),
            "user": os.environ.get("WP_TEST_USER", ""),
            "password": os.environ.get("WP_TEST_APP_PASSWORD", ""),
            "verify_ssl": os.environ.get("WP_TEST_VERIFY_SSL", "false").lower() != "false",
            # Basic Auth serveur (Nginx/Apache) si le site de test est protégé
            "server_auth_user": os.environ.get("WP_TEST_SERVER_AUTH_USER", ""),
            "server_auth_pass": os.environ.get("WP_TEST_SERVER_AUTH_PASS", ""),
            "subsites": _parse_subsites(os.environ.get("WP_TEST_SUBSITES", "")),
        },
    }


def _parse_subsites(raw: str) -> dict:
    """Parse une définition JSON de sous-sites (multisite / multilingue).

    Vide ou invalide → aucun sous-site (site unique standard).
    """
    if not raw.strip():
        return {}
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}
    except Exception:
        logger.warning("WP subsites JSON invalide, ignoré : %s", raw[:80])
        return {}


_init_servers()

DEFAULT_PER_PAGE = 20
MAX_PER_PAGE = 100

# ---------------------------------------------------------------------------
# Helpers HTTP
# ---------------------------------------------------------------------------

_MAX_RETRIES = 2
_RETRY_DELAY = 1.0


def _get_auth_header(server: str) -> str:
    """Retourne le header Authorization Basic pour un serveur.

    Si le serveur a une Basic Auth Nginx/Apache (server_auth_user/pass),
    ces credentials sont utilisés pour passer la couche serveur.
    Sinon, les credentials WordPress (Application Password) sont utilisés.
    """
    cfg = _SERVER_CONFIGS.get(server)
    if not cfg:
        raise ValueError(f"Serveur inconnu : '{server}'. Utiliser 'prod' ou 'test'.")
    if not cfg["url"]:
        raise ValueError(f"URL non configurée pour le serveur '{server}'.")

    # Basic Auth serveur (Nginx/Apache) — prioritaire si configuré
    if cfg.get("server_auth_user") and cfg.get("server_auth_pass"):
        creds = f"{cfg['server_auth_user']}:{cfg['server_auth_pass']}"
        token = base64.b64encode(creds.encode()).decode()
        return f"Basic {token}"

    # Application Password WordPress
    if not cfg["user"] or not cfg["password"]:
        raise ValueError(f"Credentials manquants pour le serveur '{server}'.")
    creds = f"{cfg['user']}:{cfg['password']}"
    token = base64.b64encode(creds.encode()).decode()
    return f"Basic {token}"


def _subsite_route(server: str, subsite: str) -> tuple[str, dict]:
    """Résout un sous-site vers (base_url, query_params).

    Le sous-site par défaut ("" ou inconnu) cible le site principal sans
    routage particulier. Les sous-sites sont déclarés *par serveur* via
    WP_<SERVER>_SUBSITES (JSON). La valeur d'un sous-site peut être :
      - "/chemin"      → Multisite en sous-répertoire (préfixe de chemin)
      - "https://…"    → URL de base alternative (sous-domaine / domaine séparé)
      - {"lang": "en"} → paramètre(s) de requête (Polylang / WPML)

    Il n'existe aucune convention WordPress universelle pour le multilingue :
    cette configuration laisse chaque site décrire la sienne sans toucher au code.
    """
    cfg = _SERVER_CONFIGS[server]
    base = cfg["url"]
    if not subsite:
        return base, {}
    spec = (cfg.get("subsites") or {}).get(subsite)
    if spec is None:
        return base, {}
    if isinstance(spec, dict):
        return base, {k: str(v) for k, v in spec.items()}
    spec = str(spec)
    if spec.startswith("http"):
        return spec.rstrip("/"), {}
    if spec.startswith("/"):
        return f"{base}/{spec.strip('/')}", {}
    return base, {}


def _merge_params(server: str, subsite: str, params: dict | None) -> dict:
    """Fusionne les paramètres de requête du sous-site (ex. lang=en) avec ceux fournis."""
    _, extra = _subsite_route(server, subsite)
    merged = dict(params or {})
    for k, v in extra.items():
        merged.setdefault(k, v)
    return merged


def _api_url(server: str, endpoint: str, subsite: str = "") -> str:
    """Construit l'URL REST WP v2 pour le sous-site demandé (défaut = site principal)."""
    base, _ = _subsite_route(server, subsite)
    return f"{base}/wp-json/wp/v2/{endpoint.lstrip('/')}"


def _plugin_url(server: str, plugin_path: str, subsite: str = "") -> str:
    """URL pour les APIs de plugins (Redirection, etc.), sous-site optionnel."""
    base, _ = _subsite_route(server, subsite)
    return f"{base}/wp-json/{plugin_path.lstrip('/')}"


async def _request(
    server: str,
    method: str,
    url: str,
    extra_headers: dict | None = None,
    **kwargs: Any,
) -> Any:
    """Appel HTTP avec retry et backoff."""
    auth = _get_auth_header(server)
    headers = {
        "Authorization": auth,
        "Accept": "application/json",
    }
    if extra_headers:
        headers.update(extra_headers)

    verify_ssl = _SERVER_CONFIGS.get(server, {}).get("verify_ssl", True)
    last_exc: Exception | None = None
    for attempt in range(_MAX_RETRIES + 1):
        try:
            async with httpx.AsyncClient(verify=verify_ssl, timeout=30) as client:
                logger.info("%s %s (attempt %d)", method, url, attempt + 1)
                resp = await client.request(method, url, headers=headers, **kwargs)
                resp.raise_for_status()
                # Certaines réponses (DELETE) peuvent être vides
                if resp.content:
                    try:
                        return resp.json()
                    except Exception:
                        return resp.text
                return {}
        except (httpx.ConnectError, httpx.ReadTimeout) as exc:
            last_exc = exc
            if attempt < _MAX_RETRIES:
                delay = _RETRY_DELAY * (2**attempt)
                logger.warning("Retry %s in %.1fs: %s", url, delay, exc)
                await asyncio.sleep(delay)
            else:
                raise
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code >= 500 and attempt < _MAX_RETRIES:
                last_exc = exc
                delay = _RETRY_DELAY * (2**attempt)
                logger.warning("Retry %s (HTTP %d) in %.1fs", url, exc.response.status_code, delay)
                await asyncio.sleep(delay)
            else:
                raise
    raise last_exc  # type: ignore[misc]


async def _wp_get(server: str, endpoint: str, params: dict | None = None, subsite: str = "") -> Any:
    return await _request(server, "GET", _api_url(server, endpoint, subsite),
                          params=_merge_params(server, subsite, params))


async def _wp_post(server: str, endpoint: str, data: dict | None = None, subsite: str = "") -> Any:
    return await _request(
        server, "POST", _api_url(server, endpoint, subsite),
        extra_headers={"Content-Type": "application/json"},
        params=_merge_params(server, subsite, None),
        content=json.dumps(data or {}, ensure_ascii=False).encode(),
    )


async def _wp_put(server: str, endpoint: str, data: dict | None = None, subsite: str = "") -> Any:
    return await _request(
        server, "PUT", _api_url(server, endpoint, subsite),
        extra_headers={"Content-Type": "application/json"},
        params=_merge_params(server, subsite, None),
        content=json.dumps(data or {}, ensure_ascii=False).encode(),
    )


async def _wp_delete(server: str, endpoint: str, params: dict | None = None, subsite: str = "") -> Any:
    return await _request(server, "DELETE", _api_url(server, endpoint, subsite),
                          params=_merge_params(server, subsite, params))


async def _plugin_get(server: str, path: str, params: dict | None = None, subsite: str = "") -> Any:
    return await _request(server, "GET", _plugin_url(server, path, subsite),
                          params=_merge_params(server, subsite, params))


async def _plugin_post(server: str, path: str, data: dict | None = None, subsite: str = "") -> Any:
    return await _request(
        server, "POST", _plugin_url(server, path, subsite),
        extra_headers={"Content-Type": "application/json"},
        params=_merge_params(server, subsite, None),
        content=json.dumps(data or {}, ensure_ascii=False).encode(),
    )


async def _plugin_delete(server: str, path: str, subsite: str = "") -> Any:
    return await _request(server, "DELETE", _plugin_url(server, path, subsite),
                          params=_merge_params(server, subsite, None))


# ---------------------------------------------------------------------------
# Formatters
# ---------------------------------------------------------------------------

def _strip_html(html: str | None) -> str:
    """Retire les balises HTML pour affichage compact."""
    if not html:
        return ""
    text = re.sub(r"<[^>]+>", " ", html)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:500] + "…" if len(text) > 500 else text


def _content_fields(p: dict, context: str) -> dict:
    """Construit les champs content/title/excerpt selon le contexte demandé.

    En context="edit" (REST WP retourne {raw, rendered}) :
      - content        : raw (markup Gutenberg, avec commentaires <!-- wp:* -->)
      - content_html   : rendered (HTML post-wpautop)
      - content_raw    : raw (idem `content`, pour usages explicites)
      - title          : title.rendered (entités décodées, lisible)
      - title_raw      : title.raw (avant tout filtrage WP)
      - excerpt        : excerpt.raw si dispo, sinon strip_html(rendered)
      - excerpt_raw    : excerpt.raw

    En context="view" (REST WP retourne seulement {rendered}) :
      - content        : rendered
      - content_html   : rendered
      - title          : title.rendered
      - excerpt        : strip_html(excerpt.rendered)
    """
    out: dict = {}

    title_obj = p.get("title", {}) if isinstance(p.get("title"), dict) else {}
    out["title"] = title_obj.get("rendered", "") if isinstance(title_obj, dict) else str(p.get("title", ""))
    if context == "edit" and title_obj.get("raw") is not None:
        out["title_raw"] = title_obj.get("raw", "")

    content_obj = p.get("content", {}) if isinstance(p.get("content"), dict) else {}
    content_rendered = content_obj.get("rendered", "") if isinstance(content_obj, dict) else str(p.get("content", ""))
    content_raw = content_obj.get("raw", "") if isinstance(content_obj, dict) else ""
    if context == "edit":
        # Quand l'éditeur Gutenberg renvoie un raw vide (page non-bloc), on retombe sur le rendered
        out["content"] = content_raw if content_raw else content_rendered
        out["content_html"] = content_rendered
        if content_raw:
            out["content_raw"] = content_raw
    else:
        out["content"] = content_rendered
        out["content_html"] = content_rendered

    excerpt_obj = p.get("excerpt", {}) if isinstance(p.get("excerpt"), dict) else {}
    excerpt_rendered = excerpt_obj.get("rendered", "") if isinstance(excerpt_obj, dict) else ""
    excerpt_raw = excerpt_obj.get("raw", "") if isinstance(excerpt_obj, dict) else ""
    if context == "edit" and excerpt_raw:
        out["excerpt"] = excerpt_raw
        out["excerpt_raw"] = excerpt_raw
    else:
        out["excerpt"] = _strip_html(excerpt_rendered)

    return out


def _format_page(p: dict, include_content: bool = False, context: str = "edit") -> dict:
    """Résumé d'une page WP. context="edit" expose le markup Gutenberg raw."""
    result = {
        "id": p.get("id"),
        "title": (p.get("title", {}).get("rendered", "") if isinstance(p.get("title"), dict) else ""),
        "slug": p.get("slug", ""),
        "status": p.get("status", ""),
        "link": p.get("link", ""),
        "parent": p.get("parent", 0),
        "menu_order": p.get("menu_order", 0),
        "modified": p.get("modified", "")[:10] if p.get("modified") else "",
        "template": p.get("template", ""),
    }
    if include_content:
        result.update(_content_fields(p, context))
        if "acf" in p and p["acf"]:
            result["acf"] = p["acf"]
    else:
        result["excerpt"] = _strip_html(
            p.get("excerpt", {}).get("rendered", "") if isinstance(p.get("excerpt"), dict) else ""
        )
    return result


def _format_post(p: dict, include_content: bool = False, context: str = "edit") -> dict:
    result = {
        "id": p.get("id"),
        "title": (p.get("title", {}).get("rendered", "") if isinstance(p.get("title"), dict) else ""),
        "slug": p.get("slug", ""),
        "status": p.get("status", ""),
        "link": p.get("link", ""),
        "date": p.get("date", "")[:10] if p.get("date") else "",
        "modified": p.get("modified", "")[:10] if p.get("modified") else "",
        "categories": p.get("categories", []),
        "tags": p.get("tags", []),
        "author": p.get("author"),
    }
    if include_content:
        result.update(_content_fields(p, context))
        if "acf" in p and p["acf"]:
            result["acf"] = p["acf"]
    return result


def _format_media(m: dict) -> dict:
    return {
        "id": m.get("id"),
        "title": m.get("title", {}).get("rendered", ""),
        "filename": m.get("source_url", "").split("/")[-1],
        "url": m.get("source_url", ""),
        "mime_type": m.get("mime_type", ""),
        "date": m.get("date", "")[:10] if m.get("date") else "",
        "alt_text": m.get("alt_text", ""),
    }


def _format_error(exc: Exception) -> str:
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        try:
            body = exc.response.json()
            msg = body.get("message", exc.response.text[:200])
        except Exception:
            msg = exc.response.text[:200]
        return f"Erreur HTTP {status} : {msg}"
    return f"Erreur : {exc}"


# ---------------------------------------------------------------------------
# Tools — Pages
# ---------------------------------------------------------------------------


@mcp.tool()
async def wordpress_list_pages(
    server: str = "prod",
    subsite: str = "",
    search: str = "",
    status: str = "any",
    parent: int = -1,
    per_page: int = DEFAULT_PER_PAGE,
    page: int = 1,
    orderby: str = "menu_order",
    order: str = "asc",
) -> str:
    """Liste les pages WordPress.

    Args:
        server: "prod" ou "test"
        subsite: "" (défaut, site principal) | clé d'un sous-site déclaré dans WP_<SERVER>_SUBSITES
        search: filtre texte (titre, contenu)
        status: "publish", "draft", "private", "any" (défaut: "any")
        parent: ID de la page parente (0 = pages racine, -1 = toutes)
        per_page: nombre de résultats (défaut 20, max 100)
        page: numéro de page pour pagination
        orderby: "menu_order" | "title" | "date" | "modified" | "id"
        order: "asc" | "desc"
    """
    params: dict = {
        "per_page": min(per_page, MAX_PER_PAGE),
        "page": page,
        "orderby": orderby,
        "order": order,
        "_fields": "id,title,slug,status,link,parent,menu_order,modified,template,excerpt",
    }
    if search:
        params["search"] = search
    if status and status != "any":
        params["status"] = status
    else:
        params["status"] = "any"
    if parent >= 0:
        params["parent"] = parent

    try:
        pages = await _wp_get(server, "pages", params, subsite=subsite)
        if not isinstance(pages, list):
            return f"Réponse inattendue : {pages}"
        results = [_format_page(p) for p in pages]
        return json.dumps({
            "server": server,
            "count": len(results),
            "page": page,
            "pages": results,
        }, ensure_ascii=False, separators=(",", ":"))
    except Exception as exc:
        return _format_error(exc)


@mcp.tool()
async def wordpress_get_page(
    page_id: int,
    server: str = "prod",
    subsite: str = "",
    context: str = "edit",
) -> str:
    """Lit le contenu complet d'une page WordPress (titre, HTML, ACF, métadonnées).

    Par défaut (context="edit"), le champ `content` renvoyé est le markup
    Gutenberg brut avec les commentaires `<!-- wp:* -->`, indispensable pour
    tout refactor structurel sans perte (wpautop n'est pas appliqué).
    `content_html` reste disponible si on a besoin du rendu HTML final.

    Args:
        page_id: ID WordPress de la page
        server: "prod" ou "test"
        subsite: "" (défaut, site principal) | clé d'un sous-site déclaré dans WP_<SERVER>_SUBSITES
        context: "edit" (raw Gutenberg, défaut) | "view" (rendered post-wpautop)
    """
    ctx = "edit" if context not in ("view", "edit") else context
    params = {"context": ctx}
    try:
        p = await _wp_get(server, f"pages/{page_id}", params, subsite=subsite)
        return json.dumps(
            _format_page(p, include_content=True, context=ctx),
            ensure_ascii=False,
            separators=(",", ":"),
        )
    except Exception as exc:
        return _format_error(exc)


@mcp.tool()
async def wordpress_create_page(
    title: str,
    content: str = "",
    server: str = "prod",
    subsite: str = "",
    status: str = "draft",
    parent: int = 0,
    slug: str = "",
    menu_order: int = 0,
    template: str = "",
    acf_fields: str = "",
) -> str:
    """Crée une nouvelle page WordPress.

    Args:
        title: titre de la page
        content: contenu HTML de la page
        server: "prod" ou "test"
        subsite: "" (défaut, site principal) | clé d'un sous-site déclaré dans WP_<SERVER>_SUBSITES
        status: "draft" | "publish" | "private"
        parent: ID de la page parente (0 = racine)
        slug: identifiant URL (généré automatiquement si vide)
        menu_order: ordre dans le menu
        template: nom du template (ex: "page-full-width.php" ou "")
        acf_fields: JSON des champs ACF à renseigner (ex: '{"mon_champ": "valeur"}')
    """
    data: dict = {
        "title": title,
        "content": content,
        "status": status,
        "parent": parent,
        "menu_order": menu_order,
    }
    if slug:
        data["slug"] = slug
    if template:
        data["template"] = template
    if acf_fields:
        try:
            data["acf"] = json.loads(acf_fields)
        except json.JSONDecodeError as e:
            return f"acf_fields JSON invalide : {e}"

    try:
        p = await _wp_post(server, "pages", data, subsite=subsite)
        return json.dumps({
            "created": True,
            "id": p.get("id"),
            "link": p.get("link"),
            "status": p.get("status"),
            "title": p.get("title", {}).get("rendered", ""),
        }, ensure_ascii=False, separators=(",", ":"))
    except Exception as exc:
        return _format_error(exc)


@mcp.tool()
async def wordpress_update_page(
    page_id: int,
    server: str = "prod",
    subsite: str = "",
    title: str = "",
    content: str = "",
    status: str = "",
    slug: str = "",
    parent: int = -1,
    menu_order: int = -1,
    template: str = "",
    acf_fields: str = "",
) -> str:
    """Met à jour une page WordPress existante. Seuls les champs fournis sont modifiés.

    Args:
        page_id: ID WordPress de la page
        server: "prod" ou "test"
        subsite: "" (défaut, site principal) | clé d'un sous-site déclaré dans WP_<SERVER>_SUBSITES
        title: nouveau titre (ignoré si vide)
        content: nouveau contenu HTML (ignoré si vide)
        status: "publish" | "draft" | "private" (ignoré si vide)
        slug: nouvel identifiant URL (ignoré si vide)
        parent: nouvelle page parente (ignoré si -1)
        menu_order: nouvel ordre de menu (ignoré si -1)
        template: nom du template (ignoré si vide)
        acf_fields: JSON des champs ACF à modifier (ex: '{"champ1": "val"}')
    """
    data: dict = {}
    if title:
        data["title"] = title
    if content:
        data["content"] = content
    if status:
        data["status"] = status
    if slug:
        data["slug"] = slug
    if parent >= 0:
        data["parent"] = parent
    if menu_order >= 0:
        data["menu_order"] = menu_order
    if template:
        data["template"] = template
    if acf_fields:
        try:
            data["acf"] = json.loads(acf_fields)
        except json.JSONDecodeError as e:
            return f"acf_fields JSON invalide : {e}"

    if not data:
        return "Aucun champ à modifier fourni."

    try:
        p = await _wp_put(server, f"pages/{page_id}", data, subsite=subsite)
        return json.dumps({
            "updated": True,
            "id": p.get("id"),
            "link": p.get("link"),
            "status": p.get("status"),
            "title": p.get("title", {}).get("rendered", ""),
            "modified": p.get("modified", "")[:19] if p.get("modified") else "",
        }, ensure_ascii=False, separators=(",", ":"))
    except Exception as exc:
        return _format_error(exc)


@mcp.tool()
async def wordpress_delete_page(
    page_id: int,
    server: str = "prod",
    subsite: str = "",
    force: bool = False,
) -> str:
    """Supprime ou met à la corbeille une page WordPress.

    Args:
        page_id: ID de la page
        server: "prod" ou "test"
        subsite: "" (défaut, site principal) | clé d'un sous-site déclaré dans WP_<SERVER>_SUBSITES
        force: True = suppression définitive, False = corbeille (défaut)
    """
    params = {"force": "true" if force else "false"}
    try:
        result = await _wp_delete(server, f"pages/{page_id}", params, subsite=subsite)
        return json.dumps({
            "deleted": True,
            "force": force,
            "id": page_id,
            "detail": result.get("previous", {}).get("title", {}).get("rendered", "") if isinstance(result, dict) else str(result)[:100],
        }, ensure_ascii=False, separators=(",", ":"))
    except Exception as exc:
        return _format_error(exc)


# ---------------------------------------------------------------------------
# Tools — Articles (Posts)
# ---------------------------------------------------------------------------


@mcp.tool()
async def wordpress_list_posts(
    server: str = "prod",
    subsite: str = "",
    search: str = "",
    status: str = "publish",
    per_page: int = DEFAULT_PER_PAGE,
    page: int = 1,
    orderby: str = "date",
    order: str = "desc",
) -> str:
    """Liste les "Posts" natifs WordPress (rest_base="posts").

    ⚠ Certains sites n'utilisent pas ce type natif pour leur blog mais un type de
    contenu personnalisé (CPT). Pour découvrir les types réellement publiés sur le
    site, appeler `wordpress_list_post_types` puis lister via
    `wordpress_list_custom_posts(post_type="<rest_base>", subsite=...)`.

    Args:
        server: "prod" ou "test"
        subsite: "" (défaut, site principal) | clé d'un sous-site déclaré dans WP_<SERVER>_SUBSITES
        search: filtre texte
        status: "publish" | "draft" | "private" | "any"
        per_page: limite (défaut 20)
        page: page de pagination
        orderby: "date" | "title" | "modified" | "id"
        order: "asc" | "desc"
    """
    params: dict = {
        "per_page": min(per_page, MAX_PER_PAGE),
        "page": page,
        "orderby": orderby,
        "order": order,
        "_fields": "id,title,slug,status,link,date,modified,categories,tags,author",
    }
    if search:
        params["search"] = search
    if status and status != "any":
        params["status"] = status
    else:
        params["status"] = "any"

    try:
        posts = await _wp_get(server, "posts", params, subsite=subsite)
        if not isinstance(posts, list):
            return f"Réponse inattendue : {posts}"
        return json.dumps({
            "server": server,
            "count": len(posts),
            "page": page,
            "posts": [_format_post(p) for p in posts],
        }, ensure_ascii=False, separators=(",", ":"))
    except Exception as exc:
        return _format_error(exc)


@mcp.tool()
async def wordpress_get_post(
    post_id: int,
    server: str = "prod",
    subsite: str = "",
    context: str = "edit",
) -> str:
    """Lit le contenu complet d'un article WordPress.

    Par défaut (context="edit"), le champ `content` renvoyé est le markup
    Gutenberg brut avec les commentaires `<!-- wp:* -->`.

    Args:
        post_id: ID de l'article
        server: "prod" ou "test"
        subsite: "" (défaut, site principal) | clé d'un sous-site déclaré dans WP_<SERVER>_SUBSITES
        context: "edit" (raw Gutenberg, défaut) | "view" (rendered post-wpautop)
    """
    ctx = "edit" if context not in ("view", "edit") else context
    try:
        p = await _wp_get(server, f"posts/{post_id}", {"context": ctx}, subsite=subsite)
        return json.dumps(
            _format_post(p, include_content=True, context=ctx),
            ensure_ascii=False,
            separators=(",", ":"),
        )
    except Exception as exc:
        return _format_error(exc)


@mcp.tool()
async def wordpress_update_post(
    post_id: int,
    server: str = "prod",
    subsite: str = "",
    title: str = "",
    content: str = "",
    status: str = "",
    acf_fields: str = "",
) -> str:
    """Met à jour un article WordPress.

    Args:
        post_id: ID de l'article
        server: "prod" ou "test"
        subsite: "" (défaut, site principal) | clé d'un sous-site déclaré dans WP_<SERVER>_SUBSITES
        title: nouveau titre (ignoré si vide)
        content: nouveau contenu HTML (ignoré si vide)
        status: "publish" | "draft" | "private" (ignoré si vide)
        acf_fields: JSON des champs ACF (ex: '{"champ": "valeur"}')
    """
    data: dict = {}
    if title:
        data["title"] = title
    if content:
        data["content"] = content
    if status:
        data["status"] = status
    if acf_fields:
        try:
            data["acf"] = json.loads(acf_fields)
        except json.JSONDecodeError as e:
            return f"acf_fields JSON invalide : {e}"
    if not data:
        return "Aucun champ à modifier."
    try:
        p = await _wp_put(server, f"posts/{post_id}", data, subsite=subsite)
        return json.dumps({
            "updated": True,
            "id": p.get("id"),
            "title": p.get("title", {}).get("rendered", ""),
            "status": p.get("status"),
        }, ensure_ascii=False, separators=(",", ":"))
    except Exception as exc:
        return _format_error(exc)


# ---------------------------------------------------------------------------
# Tools — Recherche
# ---------------------------------------------------------------------------


@mcp.tool()
async def wordpress_search(
    query: str,
    server: str = "prod",
    subsite: str = "",
    type: str = "post",
    subtype: str = "",
    per_page: int = 20,
) -> str:
    """Recherche globale dans WordPress (pages, articles, types custom).

    Args:
        query: texte à rechercher
        server: "prod" ou "test"
        subsite: "" (défaut, site principal) | clé d'un sous-site déclaré dans WP_<SERVER>_SUBSITES
        type: "post" | "term" | "post-format"
        subtype: "page" | "post" | nom du CPT (ex: "produit") — vide = tout
        per_page: nombre de résultats
    """
    params: dict = {
        "search": query,
        "type": type,
        "per_page": min(per_page, 100),
    }
    if subtype:
        params["subtype"] = subtype

    try:
        results = await _wp_get(server, "search", params, subsite=subsite)
        if not isinstance(results, list):
            return f"Réponse inattendue : {results}"
        return json.dumps({
            "server": server,
            "query": query,
            "count": len(results),
            "results": [
                {
                    "id": r.get("id"),
                    "title": r.get("title", ""),
                    "url": r.get("url", ""),
                    "type": r.get("type", ""),
                    "subtype": r.get("subtype", ""),
                }
                for r in results
            ],
        }, ensure_ascii=False, separators=(",", ":"))
    except Exception as exc:
        return _format_error(exc)


# ---------------------------------------------------------------------------
# Tools — Types de contenu & taxonomies
# ---------------------------------------------------------------------------


@mcp.tool()
async def wordpress_list_post_types(
    server: str = "prod",
    subsite: str = "",
) -> str:
    """Liste tous les types de contenu disponibles sur le site (pages, posts, CPT...).

    Args:
        server: "prod" ou "test"
        subsite: "" (défaut, site principal) | clé d'un sous-site déclaré dans WP_<SERVER>_SUBSITES
    """
    try:
        types = await _wp_get(server, "types", subsite=subsite)
        if not isinstance(types, dict):
            return f"Réponse inattendue : {types}"
        result = []
        for slug, info in types.items():
            result.append({
                "slug": slug,
                "name": info.get("name", ""),
                "rest_base": info.get("rest_base", ""),
                "hierarchical": info.get("hierarchical", False),
            })
        return json.dumps({
            "server": server,
            "count": len(result),
            "types": result,
        }, ensure_ascii=False, separators=(",", ":"))
    except Exception as exc:
        return _format_error(exc)


@mcp.tool()
async def wordpress_list_custom_posts(
    post_type: str,
    server: str = "prod",
    subsite: str = "",
    search: str = "",
    status: str = "publish",
    per_page: int = DEFAULT_PER_PAGE,
    page: int = 1,
) -> str:
    """Liste les publications d'un type de contenu personnalisé (CPT).

    Utiliser wordpress_list_post_types() pour découvrir les rest_base disponibles
    sur le site cible (ils varient d'un site à l'autre).

    Args:
        post_type: rest_base du type (découvert via wordpress_list_post_types)
        server: "prod" ou "test"
        subsite: "" (défaut, site principal) | clé d'un sous-site déclaré dans WP_<SERVER>_SUBSITES
        search: filtre texte
        status: "publish" | "draft" | "any"
        per_page: limite
        page: page de pagination
    """
    params: dict = {
        "per_page": min(per_page, MAX_PER_PAGE),
        "page": page,
        "context": "edit",
    }
    if search:
        params["search"] = search
    if status and status != "any":
        params["status"] = status
    else:
        params["status"] = "any"

    try:
        items = await _wp_get(server, post_type, params, subsite=subsite)
        if not isinstance(items, list):
            return f"Réponse inattendue : {items}"

        results = []
        for item in items:
            entry = {
                "id": item.get("id"),
                "title": item.get("title", {}).get("rendered", "") if isinstance(item.get("title"), dict) else str(item.get("title", "")),
                "slug": item.get("slug", ""),
                "status": item.get("status", ""),
                "link": item.get("link", ""),
                "modified": item.get("modified", "")[:10] if item.get("modified") else "",
            }
            if "acf" in item and item["acf"]:
                entry["acf"] = item["acf"]
            results.append(entry)

        return json.dumps({
            "server": server,
            "post_type": post_type,
            "count": len(results),
            "items": results,
        }, ensure_ascii=False, separators=(",", ":"))
    except Exception as exc:
        return _format_error(exc)


@mcp.tool()
async def wordpress_update_custom_post(
    post_type: str,
    post_id: int,
    server: str = "prod",
    subsite: str = "",
    title: str = "",
    content: str = "",
    status: str = "",
    acf_fields: str = "",
) -> str:
    """Met à jour un contenu de type personnalisé (CPT) avec ses champs ACF.

    Args:
        post_type: rest_base du type (ex: "produit", "membre")
        post_id: ID de la publication
        server: "prod" ou "test"
        subsite: "" (défaut, site principal) | clé d'un sous-site déclaré dans WP_<SERVER>_SUBSITES
        title: nouveau titre (ignoré si vide)
        content: nouveau contenu HTML (ignoré si vide)
        status: "publish" | "draft" | "private" (ignoré si vide)
        acf_fields: JSON des champs ACF (ex: '{"nom": "valeur", "date": "2025-01-01"}')
    """
    data: dict = {}
    if title:
        data["title"] = title
    if content:
        data["content"] = content
    if status:
        data["status"] = status
    if acf_fields:
        try:
            data["acf"] = json.loads(acf_fields)
        except json.JSONDecodeError as e:
            return f"acf_fields JSON invalide : {e}"
    if not data:
        return "Aucun champ à modifier."

    try:
        p = await _wp_put(server, f"{post_type}/{post_id}", data, subsite=subsite)
        return json.dumps({
            "updated": True,
            "id": p.get("id"),
            "title": p.get("title", {}).get("rendered", "") if isinstance(p.get("title"), dict) else str(p.get("title", "")),
            "status": p.get("status"),
            "link": p.get("link", ""),
        }, ensure_ascii=False, separators=(",", ":"))
    except Exception as exc:
        return _format_error(exc)


@mcp.tool()
async def wordpress_get_custom_post(
    post_type: str,
    post_id: int,
    server: str = "prod",
    subsite: str = "",
    context: str = "edit",
) -> str:
    """Récupère un contenu de type personnalisé (CPT) avec ses champs ACF complets.

    Par défaut (context="edit"), le champ `content` renvoyé est le markup
    Gutenberg brut (`<!-- wp:* -->` préservés). Indispensable pour faire du
    refactor structurel sans casser les blocs. `content_html` reste accessible
    si on veut le rendu post-wpautop, et `title_raw` / `excerpt_raw` exposent
    les versions non filtrées.

    Args:
        post_type: rest_base du type (découvert via wordpress_list_post_types)
        post_id: ID de la publication
        server: "prod" ou "test"
        subsite: "" (défaut, site principal) | clé d'un sous-site déclaré dans WP_<SERVER>_SUBSITES
        context: "edit" (raw Gutenberg, défaut) | "view" (rendered post-wpautop)
    """
    ctx = "edit" if context not in ("view", "edit") else context
    try:
        p = await _wp_get(server, f"{post_type}/{post_id}", {"context": ctx}, subsite=subsite)
        if not isinstance(p, dict):
            return f"Réponse inattendue : {p}"
        result: dict = {
            "id": p.get("id"),
            "slug": p.get("slug", ""),
            "status": p.get("status", ""),
            "link": p.get("link", ""),
            "date": p.get("date", ""),
            "modified": p.get("modified", ""),
            "acf": p.get("acf", {}),
            "categories": p.get("categories", []),
            "tags": p.get("tags", []),
        }
        result.update(_content_fields(p, ctx))
        return json.dumps(result, ensure_ascii=False, separators=(",", ":"))
    except Exception as exc:
        return _format_error(exc)


@mcp.tool()
async def wordpress_create_custom_post(
    post_type: str,
    title: str,
    content: str = "",
    status: str = "draft",
    slug: str = "",
    acf_fields: str = "",
    server: str = "prod",
    subsite: str = "",
) -> str:
    """Crée une nouvelle publication d'un type personnalisé (CPT).

    Le rest_base du type dépend du site : le découvrir via
    wordpress_list_post_types. Pour viser un sous-site, passer sa clé en `subsite`.

    Args:
        post_type: rest_base du type (découvert via wordpress_list_post_types)
        title: titre de la publication
        content: contenu HTML (optionnel)
        status: "draft" (défaut) | "publish" | "private" | "pending"
        slug: identifiant URL (généré automatiquement si vide)
        acf_fields: JSON des champs ACF (ex: '{"chapeau": "..."}')
        server: "prod" ou "test"
        subsite: "" (défaut, site principal) | clé d'un sous-site déclaré dans WP_<SERVER>_SUBSITES
    """
    data: dict = {
        "title": title,
        "content": content,
        "status": status,
    }
    if slug:
        data["slug"] = slug
    if acf_fields:
        try:
            data["acf"] = json.loads(acf_fields)
        except json.JSONDecodeError as e:
            return f"acf_fields JSON invalide : {e}"

    try:
        p = await _wp_post(server, post_type, data, subsite=subsite)
        return json.dumps({
            "created": True,
            "id": p.get("id"),
            "link": p.get("link"),
            "status": p.get("status"),
            "title": p.get("title", {}).get("rendered", "") if isinstance(p.get("title"), dict) else str(p.get("title", "")),
        }, ensure_ascii=False, separators=(",", ":"))
    except Exception as exc:
        return _format_error(exc)


@mcp.tool()
async def wordpress_delete_custom_post(
    post_type: str,
    post_id: int,
    force: bool = False,
    server: str = "prod",
    subsite: str = "",
) -> str:
    """Supprime ou met à la corbeille une publication d'un type personnalisé (CPT).

    Args:
        post_type: rest_base du type (découvert via wordpress_list_post_types)
        post_id: ID de la publication
        force: True = suppression définitive, False = corbeille (défaut)
        server: "prod" ou "test"
        subsite: "" (défaut, site principal) | clé d'un sous-site déclaré dans WP_<SERVER>_SUBSITES
    """
    params = {"force": "true" if force else "false"}
    try:
        result = await _wp_delete(server, f"{post_type}/{post_id}", params, subsite=subsite)
        return json.dumps({
            "deleted": True,
            "force": force,
            "id": post_id,
            "detail": result.get("previous", {}).get("title", {}).get("rendered", "") if isinstance(result, dict) else str(result)[:100],
        }, ensure_ascii=False, separators=(",", ":"))
    except Exception as exc:
        return _format_error(exc)


# ---------------------------------------------------------------------------
# Tools — Médias
# ---------------------------------------------------------------------------


@mcp.tool()
async def wordpress_list_media(
    server: str = "prod",
    subsite: str = "",
    search: str = "",
    mime_type: str = "",
    per_page: int = DEFAULT_PER_PAGE,
    page: int = 1,
) -> str:
    """Liste la médiathèque WordPress.

    Args:
        server: "prod" ou "test"
        subsite: "" (défaut, site principal) | clé d'un sous-site déclaré dans WP_<SERVER>_SUBSITES
        search: filtre texte
        mime_type: filtre MIME (ex: "image/jpeg", "image", "application/pdf")
        per_page: limite
        page: page de pagination
    """
    params: dict = {
        "per_page": min(per_page, MAX_PER_PAGE),
        "page": page,
        "_fields": "id,title,source_url,mime_type,date,alt_text",
    }
    if search:
        params["search"] = search
    if mime_type:
        params["media_type"] = mime_type.split("/")[0] if "/" in mime_type else mime_type

    try:
        items = await _wp_get(server, "media", params, subsite=subsite)
        if not isinstance(items, list):
            return f"Réponse inattendue : {items}"
        return json.dumps({
            "server": server,
            "count": len(items),
            "media": [_format_media(m) for m in items],
        }, ensure_ascii=False, separators=(",", ":"))
    except Exception as exc:
        return _format_error(exc)


@mcp.tool()
async def wordpress_upload_media(
    file_path: str,
    server: str = "prod",
    subsite: str = "",
    title: str = "",
    alt_text: str = "",
    caption: str = "",
) -> str:
    """Uploade un fichier dans la médiathèque WordPress.

    Args:
        file_path: chemin absolu du fichier à uploader
        server: "prod" ou "test"
        subsite: "" (défaut, site principal) | clé d'un sous-site déclaré dans WP_<SERVER>_SUBSITES
        title: titre du média (nom de fichier si vide)
        alt_text: texte alternatif (pour images)
        caption: légende
    """
    path = Path(file_path)
    if not path.exists():
        return f"Fichier non trouvé : {file_path}"

    mime, _ = mimetypes.guess_type(str(path))
    mime = mime or "application/octet-stream"

    try:
        file_bytes = path.read_bytes()
        auth = _get_auth_header(server)
        url = _api_url(server, "media", subsite)

        headers = {
            "Authorization": auth,
            "Content-Type": mime,
            "Content-Disposition": f'attachment; filename="{path.name}"',
        }

        verify_ssl = _SERVER_CONFIGS.get(server, {}).get("verify_ssl", True)
        async with httpx.AsyncClient(verify=verify_ssl, timeout=60) as client:
            resp = await client.post(url, headers=headers, content=file_bytes)
            resp.raise_for_status()
            media = resp.json()

        media_id = media.get("id")

        # Mise à jour titre/alt si demandé
        if media_id and (title or alt_text or caption):
            update_data: dict = {}
            if title:
                update_data["title"] = title
            if alt_text:
                update_data["alt_text"] = alt_text
            if caption:
                update_data["caption"] = caption
            await _wp_put(server, f"media/{media_id}", update_data, subsite=subsite)

        return json.dumps({
            "uploaded": True,
            "id": media_id,
            "url": media.get("source_url", ""),
            "mime_type": mime,
            "filename": path.name,
        }, ensure_ascii=False, separators=(",", ":"))
    except Exception as exc:
        return _format_error(exc)


# ---------------------------------------------------------------------------
# Tools — ACF
# ---------------------------------------------------------------------------


@mcp.tool()
async def wordpress_list_acf_field_groups(
    server: str = "prod",
    subsite: str = "",
    per_page: int = 50,
) -> str:
    """Liste les groupes de champs ACF disponibles sur le site.

    Utilise l'API ACF native (/wp-json/acf/v3/field-groups).
    Si ACF n'est pas actif ou non exposé, retourne une erreur.

    Args:
        server: "prod" ou "test"
        subsite: "" (défaut, site principal) | clé d'un sous-site déclaré dans WP_<SERVER>_SUBSITES
        per_page: limite
    """
    try:
        cfg = _SERVER_CONFIGS[server]
        if not cfg["url"]:
            return f"Serveur '{server}' non configuré."
        url = f"{cfg['url']}/wp-json/acf/v3/field-groups"
        auth = _get_auth_header(server)
        async with httpx.AsyncClient(verify=cfg.get("verify_ssl", True), timeout=30) as client:
            resp = await client.get(url, headers={"Authorization": auth, "Accept": "application/json"}, params={"per_page": per_page})
            resp.raise_for_status()
            data = resp.json()

        groups = data if isinstance(data, list) else data.get("acf-field-group", [])
        result = []
        for g in groups:
            result.append({
                "id": g.get("id"),
                "key": g.get("acf_field_group", {}).get("key", "") if isinstance(g.get("acf_field_group"), dict) else g.get("key", ""),
                "title": g.get("title", ""),
                "location": g.get("acf_field_group", {}).get("location", []) if isinstance(g.get("acf_field_group"), dict) else g.get("location", []),
            })

        return json.dumps({
            "server": server,
            "count": len(result),
            "field_groups": result,
        }, ensure_ascii=False, separators=(",", ":"))
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 404:
            return "ACF API v3 non disponible. Vérifier qu'ACF PRO est actif et que l'API est activée dans ACF > Paramètres."
        return _format_error(exc)
    except Exception as exc:
        return _format_error(exc)


@mcp.tool()
async def wordpress_get_acf_fields(
    post_id: int,
    server: str = "prod",
    subsite: str = "",
) -> str:
    """Récupère tous les champs ACF d'une publication via l'API ACF v3.

    Args:
        post_id: ID de la publication
        server: "prod" ou "test"
        subsite: "" (défaut, site principal) | clé d'un sous-site déclaré dans WP_<SERVER>_SUBSITES
    """
    try:
        cfg = _SERVER_CONFIGS[server]
        url = f"{cfg['url']}/wp-json/acf/v3/posts/{post_id}"
        auth = _get_auth_header(server)
        async with httpx.AsyncClient(verify=cfg.get("verify_ssl", True), timeout=30) as client:
            resp = await client.get(url, headers={"Authorization": auth, "Accept": "application/json"})
            resp.raise_for_status()
            data = resp.json()

        acf = data.get("acf", {})
        return json.dumps({
            "post_id": post_id,
            "server": server,
            "acf": acf,
        }, ensure_ascii=False, separators=(",", ":"))
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 404:
            return f"Publication {post_id} non trouvée ou ACF API v3 non disponible."
        return _format_error(exc)
    except Exception as exc:
        return _format_error(exc)


# ---------------------------------------------------------------------------
# Tools — Redirections (plugin Redirection)
# ---------------------------------------------------------------------------


@mcp.tool()
async def wordpress_list_redirections(
    server: str = "prod",
    subsite: str = "",
    search: str = "",
    per_page: int = 50,
    page: int = 1,
) -> str:
    """Liste les redirections gérées par le plugin Redirection.

    Args:
        server: "prod" ou "test"
        subsite: "" (défaut, site principal) | clé d'un sous-site déclaré dans WP_<SERVER>_SUBSITES
        search: filtre sur l'URL source
        per_page: limite
        page: page de pagination
    """
    params: dict = {"per_page": per_page, "page": page}
    if search:
        params["filterby[url]"] = search

    try:
        data = await _plugin_get(server, "redirection/v1/redirect", params, subsite=subsite)
        items = data.get("items", []) if isinstance(data, dict) else data
        return json.dumps({
            "server": server,
            "count": data.get("total", len(items)) if isinstance(data, dict) else len(items),
            "redirections": [
                {
                    "id": r.get("id"),
                    "url": r.get("url", ""),
                    "action_data": r.get("action_data", {}).get("url", "") if isinstance(r.get("action_data"), dict) else r.get("action_data", ""),
                    "action_code": r.get("action_code", 301),
                    "enabled": r.get("enabled", True),
                    "hits": r.get("hits", 0),
                    "last_access": r.get("last_access", ""),
                }
                for r in items
            ],
        }, ensure_ascii=False, separators=(",", ":"))
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 404:
            return "Plugin Redirection non actif ou API non disponible."
        return _format_error(exc)
    except Exception as exc:
        return _format_error(exc)


@mcp.tool()
async def wordpress_create_redirection(
    source_url: str,
    target_url: str,
    server: str = "prod",
    subsite: str = "",
    http_code: int = 301,
) -> str:
    """Crée une redirection 301/302 via le plugin Redirection.

    Args:
        source_url: URL source (chemin relatif, ex: /ancien-chemin/)
        target_url: URL de destination (ex: /nouveau-chemin/ ou https://...)
        server: "prod" ou "test"
        subsite: "" (défaut, site principal) | clé d'un sous-site déclaré dans WP_<SERVER>_SUBSITES
        http_code: code HTTP (301 permanent ou 302 temporaire)
    """
    data = {
        "url": source_url,
        "action_type": "url",
        "action_data": {"url": target_url},
        "action_code": http_code,
        "match_type": "url",
        "group_id": 1,
        "enabled": True,
    }
    try:
        result = await _plugin_post(server, "redirection/v1/redirect", data, subsite=subsite)
        return json.dumps({
            "created": True,
            "id": result.get("id"),
            "source": source_url,
            "target": target_url,
            "code": http_code,
        }, ensure_ascii=False, separators=(",", ":"))
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 404:
            return "Plugin Redirection non actif ou API non disponible."
        return _format_error(exc)
    except Exception as exc:
        return _format_error(exc)


@mcp.tool()
async def wordpress_delete_redirection(
    redirection_id: int,
    server: str = "prod",
    subsite: str = "",
) -> str:
    """Supprime une redirection par son ID.

    Args:
        redirection_id: ID de la redirection (obtenu via wordpress_list_redirections)
        server: "prod" ou "test"
        subsite: "" (défaut, site principal) | clé d'un sous-site déclaré dans WP_<SERVER>_SUBSITES
    """
    try:
        await _plugin_delete(server, f"redirection/v1/redirect/{redirection_id}", subsite=subsite)
        return json.dumps({"deleted": True, "id": redirection_id}, separators=(",", ":"))
    except Exception as exc:
        return _format_error(exc)


# ---------------------------------------------------------------------------
# Tools — Utilitaires site
# ---------------------------------------------------------------------------


@mcp.tool()
async def wordpress_site_info(
    server: str = "prod",
    subsite: str = "",
) -> str:
    """Informations générales sur le site WordPress (nom, URL, version, multisite...).

    Args:
        server: "prod" ou "test"
        subsite: "" (défaut, site principal) | clé d'un sous-site déclaré dans WP_<SERVER>_SUBSITES
    """
    try:
        cfg = _SERVER_CONFIGS[server]
        if not cfg["url"]:
            return f"Serveur '{server}' non configuré (WP_{server.upper()}_URL manquant)."
        url = f"{cfg['url']}/wp-json/"
        auth = _get_auth_header(server)
        async with httpx.AsyncClient(verify=cfg.get("verify_ssl", True), timeout=30) as client:
            resp = await client.get(url, headers={"Authorization": auth, "Accept": "application/json"})
            resp.raise_for_status()
            data = resp.json()

        return json.dumps({
            "server": server,
            "url": cfg["url"],
            "name": data.get("name", ""),
            "description": data.get("description", ""),
            "url_site": data.get("url", ""),
            "home": data.get("home", ""),
            "gmt_offset": data.get("gmt_offset", 0),
            "timezone": data.get("timezone_string", ""),
            "namespaces": data.get("namespaces", []),
        }, ensure_ascii=False, separators=(",", ":"))
    except Exception as exc:
        return _format_error(exc)


@mcp.tool()
async def wordpress_list_categories(
    server: str = "prod",
    subsite: str = "",
    per_page: int = 100,
    hide_empty: bool = False,
) -> str:
    """Liste les catégories WordPress.

    Args:
        server: "prod" ou "test"
        subsite: "" (défaut, site principal) | clé d'un sous-site déclaré dans WP_<SERVER>_SUBSITES
        per_page: limite
        hide_empty: True = masquer les catégories sans article
    """
    params: dict = {
        "per_page": per_page,
        "hide_empty": "true" if hide_empty else "false",
        "_fields": "id,name,slug,parent,count",
    }
    try:
        cats = await _wp_get(server, "categories", params, subsite=subsite)
        if not isinstance(cats, list):
            return f"Réponse inattendue : {cats}"
        return json.dumps({
            "server": server,
            "count": len(cats),
            "categories": [
                {"id": c.get("id"), "name": c.get("name", ""), "slug": c.get("slug", ""), "parent": c.get("parent", 0), "count": c.get("count", 0)}
                for c in cats
            ],
        }, ensure_ascii=False, separators=(",", ":"))
    except Exception as exc:
        return _format_error(exc)


@mcp.tool()
async def wordpress_list_users(
    server: str = "prod",
    subsite: str = "",
    per_page: int = 50,
    roles: str = "",
) -> str:
    """Liste les utilisateurs WordPress.

    Args:
        server: "prod" ou "test"
        subsite: "" (défaut, site principal) | clé d'un sous-site déclaré dans WP_<SERVER>_SUBSITES
        per_page: limite
        roles: filtre par rôle (ex: "editor", "author") — vide = tous
    """
    params: dict = {
        "per_page": per_page,
        "_fields": "id,name,slug,email,roles",
    }
    if roles:
        params["roles"] = roles

    try:
        users = await _wp_get(server, "users", params, subsite=subsite)
        if not isinstance(users, list):
            return f"Réponse inattendue : {users}"
        return json.dumps({
            "server": server,
            "count": len(users),
            "users": [
                {"id": u.get("id"), "name": u.get("name", ""), "slug": u.get("slug", ""), "email": u.get("email", ""), "roles": u.get("roles", [])}
                for u in users
            ],
        }, ensure_ascii=False, separators=(",", ":"))
    except Exception as exc:
        return _format_error(exc)


@mcp.tool()
async def wordpress_check_connection(
    server: str = "prod",
    subsite: str = "",
) -> str:
    """Teste la connexion et l'authentification à un serveur WordPress.

    Args:
        server: "prod" ou "test"
        subsite: "" (défaut, site principal) | clé d'un sous-site déclaré dans WP_<SERVER>_SUBSITES
    """
    cfg = _SERVER_CONFIGS.get(server, {})
    checks = {
        "server": server,
        "url_configured": bool(cfg.get("url")),
        "user_configured": bool(cfg.get("user")),
        "password_configured": bool(cfg.get("password")),
        "url": cfg.get("url", "(non configuré)"),
    }

    if not all([checks["url_configured"], checks["user_configured"], checks["password_configured"]]):
        checks["status"] = "ERREUR: configuration incomplète"
        return json.dumps(checks, ensure_ascii=False, separators=(",", ":"))

    try:
        result = await _wp_get(server, "users/me", {"_fields": "id,name,email,roles"}, subsite=subsite)
        checks["status"] = "OK"
        checks["authenticated_as"] = result.get("name", "")
        checks["user_id"] = result.get("id")
        checks["email"] = result.get("email", "")
        checks["roles"] = result.get("roles", [])
    except httpx.HTTPStatusError as exc:
        checks["status"] = f"ERREUR HTTP {exc.response.status_code}"
        if exc.response.status_code == 401:
            checks["detail"] = "Authentification échouée — vérifier user/Application Password"
        elif exc.response.status_code == 403:
            checks["detail"] = "Accès refusé — vérifier les droits de l'utilisateur"
    except Exception as exc:
        checks["status"] = f"ERREUR: {exc}"

    return json.dumps(checks, ensure_ascii=False, separators=(",", ":"))


# ---------------------------------------------------------------------------
# Entrée principale
# ---------------------------------------------------------------------------

def main() -> None:
    """Point d'entree du serveur MCP (stdio)."""
    mcp.run()


if __name__ == "__main__":
    main()
