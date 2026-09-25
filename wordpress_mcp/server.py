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
import urllib.parse
import xml.etree.ElementTree as ET
from contextvars import ContextVar
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
            # Vérification TLS active par défaut : opt-out explicite uniquement
            # (l'auth est Basic, un MITM capterait les credentials en clair).
            "verify_ssl": os.environ.get("WP_TEST_VERIFY_SSL", "1").strip().lower()
                          not in ("0", "false", "no", "off"),
            # Basic Auth serveur (Nginx/Apache) si le site de test est protégé
            "server_auth_user": os.environ.get("WP_TEST_SERVER_AUTH_USER", ""),
            "server_auth_pass": os.environ.get("WP_TEST_SERVER_AUTH_PASS", ""),
            "subsites": _parse_subsites(os.environ.get("WP_TEST_SUBSITES", "")),
        },
    }
    if not _SERVER_CONFIGS["test"]["verify_ssl"]:
        logger.warning(
            "Vérification TLS DÉSACTIVÉE pour le serveur 'test' (WP_TEST_VERIFY_SSL) : "
            "les credentials Basic sont exposés à une interception MITM."
        )


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

# Méthodes rejouables une fois la requête émise. Rejouer une écriture après un
# timeout de lecture ou un 5xx PUBLIE en double : WordPress a pu traiter la
# requête avant de perdre la réponse, et le second article est visible du public.
_IDEMPOTENT_METHODS = {"GET", "HEAD", "OPTIONS"}

#: Ce que signifie chaque famille d'exception httpx quand elle arrive sans
#: message : c'est le cas courant (ReadError depuis une fin de flux, ReadTimeout
#: depuis un TimeoutError nu), et « Erreur : » suivi de rien était tout ce que
#: l'appelant recevait.
_TRANSPORT_DEFAULT_DETAIL = {
    "ReadError": "connexion coupée par le serveur avant la réponse",
    "ReadTimeout": "délai de lecture dépassé",
    "WriteTimeout": "délai d'écriture dépassé",
    "ConnectTimeout": "délai de connexion dépassé",
    "ConnectError": "connexion impossible",
    "PoolTimeout": "délai d'attente d'une connexion dépassé",
    "RemoteProtocolError": "réponse HTTP mal formée ou interrompue",
    "WriteError": "connexion coupée pendant l'envoi de la requête",
    "CloseError": "échec de fermeture de la connexion",
    "LocalProtocolError": "requête HTTP mal formée",
    "ProxyError": "échec de connexion au proxy",
    "UnsupportedProtocol": "protocole HTTP non pris en charge",
}

# MCP lance chaque appel d'outil dans une tâche distincte. Une tâche enfant
# hérite du contexte : l'identité de la tâche évite qu'elle hérite aussi des
# écritures du parent et les attribue à tort à son propre appel.
_applied_writes: ContextVar["tuple[asyncio.Task[Any], list[str]] | None"] = ContextVar(
    "wordpress_applied_writes", default=None,
)


def _write_journal() -> list[str]:
    """Journal propre à l'appel d'outil, vide hors d'une tâche asyncio."""
    try:
        task = asyncio.current_task()
    except RuntimeError:
        return []
    if task is None:
        return []
    journal = _applied_writes.get()
    if journal is None or journal[0] is not task:
        journal = (task, [])
        _applied_writes.set(journal)
    return journal[1]


def _request_label(method: str, url: str) -> str:
    """Nomme la requête par sa route, sans l'hôte.

    Un message d'erreur n'a pas à citer le domaine — il est déjà connu de
    l'appelant par le paramètre `server` — et la route seule reste lisible
    quand plusieurs écritures sont listées côte à côte.
    """
    path = urllib.parse.urlparse(url).path
    for marker in ("/wp-json/", "/xmlrpc.php"):
        if marker in path:
            path = path.split(marker, 1)[1] if marker == "/wp-json/" else "xmlrpc.php"
            break
    return f"{method.upper()} {path.strip('/') or '/'}"


def _append_sentence(base: str, extra: str) -> str:
    """Enchaîne deux phrases sans les souder : un message WordPress finit
    rarement par un point, et « Paramètre invalide Etat de l'ecriture » se lit mal."""
    if not extra:
        return base
    base = base.rstrip()
    if base and base[-1] not in ".!?;:":
        base += "."
    return base + extra


def _write_state_message(write_state: str, label: str) -> str:
    """Précise le risque de rejouer la requête qui vient d'échouer."""
    if write_state == "non_appliquee":
        return (f" Écriture {label} non appliquée : rejouer cette requête est sans "
                "risque de doublon.")
    if write_state == "inconnu":
        return (f" État de l'écriture {label} inconnu : écriture non rejouée "
                "automatiquement, WordPress a pu la traiter avant de perdre la "
                "réponse ; relire l'objet avant de rejouer.")
    return ""


class WordPressTransportError(RuntimeError):
    """Échec réseau définitif, nommé même quand l'exception httpx est vide.

    Sans elle, un `httpx.ReadError` vide ou un `ReadTimeout` sans message se
    rendaient à l'appelant en « Erreur : » nu, et rien ne distinguait un mot de
    passe d'application refusé d'une connexion coupée.
    """

    def __init__(self, kind: str, label: str, attempts: int,
                 replayable: bool, detail: str = ""):
        self.kind = kind
        self.label = label
        self.attempts = attempts
        self.replayable = replayable
        self.detail = detail.strip() or _TRANSPORT_DEFAULT_DETAIL.get(
            kind, "échec du transport HTTP")
        idempotent = label.split(" ", 1)[0] in _IDEMPOTENT_METHODS
        self.write_state = (
            "sans_objet" if idempotent
            else "non_appliquee" if replayable else "inconnu"
        )
        tail = _write_state_message(self.write_state, label)
        if self.write_state == "sans_objet":
            tail = (" Rejouer l'appel plus tard, et vérifier la disponibilité du "
                    "site si l'erreur persiste.")
        count = "tentative" if attempts == 1 else "tentatives"
        super().__init__(
            f"{kind} sur {label} ({self.detail}) après {attempts} {count}.{tail}")


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
    timeout: float = 30,
    **kwargs: Any,
) -> Any:
    """Appel HTTP avec rejeu et backoff exponentiel.

    Les lectures sont rejouées après toute erreur de transport et sur un HTTP
    5xx. Une écriture n'est rejouée que si la connexion n'a jamais abouti
    (ConnectError, ConnectTimeout) : une fois la requête partie, WordPress a pu
    la traiter avant de perdre la réponse, et un rejeu publierait l'article, le
    média ou la redirection en double — visible du public.

    Un corps non-JSON sur une réponse 2xx est rendu en texte, comme avant :
    `wordpress_check_content` lit du HTML par cette voie.

    Limite : une écriture partie dont la réponse est perdue laisse un état que
    le client ne peut pas trancher. L'API REST n'expose ni clef d'idempotence ni
    identifiant de requête à vérifier ; il faut relire l'objet avant de rejouer,
    et c'est exactement ce que veut dire « inconnu ». Le journal rappelle en
    outre les écritures déjà réussies dans le même appel d'outil.
    """
    auth = _get_auth_header(server)
    headers = {
        "Authorization": auth,
        "Accept": "application/json",
    }
    if extra_headers:
        headers.update(extra_headers)

    label = _request_label(method, url)
    idempotent = method.upper() in _IDEMPOTENT_METHODS
    journal = _write_journal()
    verify_ssl = _SERVER_CONFIGS.get(server, {}).get("verify_ssl", True)
    last_exc: Exception | None = None
    for attempt in range(_MAX_RETRIES + 1):
        try:
            async with httpx.AsyncClient(verify=verify_ssl, timeout=timeout) as client:
                logger.info("%s %s (attempt %d)", method, url, attempt + 1)
                resp = await client.request(method, url, headers=headers, **kwargs)
                resp.raise_for_status()
                # Certaines réponses (DELETE) peuvent être vides
                if resp.content:
                    try:
                        data: Any = resp.json()
                    except Exception:
                        data = resp.text
                else:
                    data = {}
            if not idempotent:
                journal.append(label)
            return data
        except httpx.TransportError as exc:
            # Seul un échec de connexion prouve que WordPress n'a rien reçu.
            # Après l'envoi, rejouer une écriture peut publier en double. Toute
            # la famille est traitée : un ReadError, un WriteTimeout ou un
            # RemoteProtocolError traversaient sans rejeu ni habillage.
            never_sent = isinstance(exc, (httpx.ConnectError, httpx.ConnectTimeout))
            replayable = never_sent or idempotent
            last_exc = exc
            if replayable and attempt < _MAX_RETRIES:
                delay = _RETRY_DELAY * (2**attempt)
                logger.warning("Retry %s in %.1fs (%s: %s)", url, delay,
                               type(exc).__name__, exc)
                await asyncio.sleep(delay)
            else:
                raise WordPressTransportError(
                    type(exc).__name__, label, attempt + 1, replayable, str(exc),
                ) from exc
        except httpx.HTTPStatusError as exc:
            # Les outils inspectent le statut HTTP (404 sur un article absent,
            # 401 sur un mot de passe d'application) : garder l'exception httpx
            # d'origine tout en donnant au formateur le contexte de l'écriture.
            exc.wp_label = label
            exc.wp_mutating = not idempotent
            if idempotent and exc.response.status_code >= 500 and attempt < _MAX_RETRIES:
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


_BLOCK_MARKER = "<!-- wp:"


def _with_warning(payload: dict, warning: str | None) -> dict:
    """Ajoute la clé `warning` au retour d'une écriture, si garde-fou déclenché."""
    if warning:
        payload["warning"] = warning
    return payload


async def _gutenberg_downgrade_warning(
    server: str, endpoint: str, content: str, subsite: str = ""
) -> str | None:
    """Prévient qu'une écriture va faire retomber un contenu à blocs en HTML classique.

    Un article Gutenberg réécrit sans ses délimiteurs `<!-- wp: -->` redevient un
    contenu classique : WordPress réactive alors wpautop, qui injecte des
    paragraphes vides. L'API REST accepte l'écriture sans rien signaler, d'où ce
    contrôle préalable. Renvoie None si le contenu envoyé porte déjà des blocs,
    si le contenu actuel n'en portait pas, ou si la lecture de contrôle échoue.
    """
    if not content or _BLOCK_MARKER in content:
        return None
    try:
        current = await _wp_get(server, endpoint, {"context": "edit"}, subsite=subsite)
    except Exception:
        return None
    if not isinstance(current, dict):
        return None
    current_obj = current.get("content", {})
    current_raw = current_obj.get("raw", "") if isinstance(current_obj, dict) else ""
    if _BLOCK_MARKER not in current_raw:
        return None
    return (
        "Le contenu actuel était un contenu à blocs Gutenberg, le contenu envoyé ne "
        "contient aucun délimiteur `<!-- wp:`. La publication est désormais un contenu "
        "classique : wpautop redevient actif et peut injecter des paragraphes vides. "
        "Pour conserver la structure, repartir du champ `content` renvoyé par les "
        "outils de lecture en context=\"edit\", ou encapsuler le HTML dans un bloc "
        "`<!-- wp:html -->` ... `<!-- /wp:html -->`."
    )


_EMPTY_P_RE = re.compile(r"<p[^>]*>(?:\s|&nbsp;|&#160;|<br\s*/?>)*</p>", re.I)
_IMG_RE = re.compile(r"<img\b[^>]*>", re.I)
_ALT_RE = re.compile(r"""\balt\s*=\s*(["'])(.*?)\1""", re.I | re.S)
_H1_RE = re.compile(r"<h1\b", re.I)


def _plain_text(html: str) -> str:
    """Texte nu d'un fragment HTML, commentaires de blocs et scripts retirés."""
    text = re.sub(r"(?s)<!--.*?-->", " ", html or "")
    text = re.sub(r"(?si)<(script|style)\b.*?</\1>", " ", text)
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _content_health(raw: str, rendered: str = "") -> dict:
    """Relève les défauts courants d'un contenu WordPress, sans rien modifier.

    Reprend les contrôles qu'il fallait jusqu'ici mener à la main après une
    écriture. La présence de blocs se mesure sur la source (`raw`) : elle seule
    porte les délimiteurs. Tout le reste se mesure sur le rendu, parce que les
    paragraphes vides de wpautop n'existent pas dans la source, ils
    apparaissent à l'affichage dès qu'un contenu est retombé en classique.
    """
    raw = raw or ""
    surface = rendered if rendered else raw
    imgs = _IMG_RE.findall(surface)
    no_alt = 0
    for tag in imgs:
        found = _ALT_RE.search(tag)
        if found is None or not found.group(2).strip():
            no_alt += 1
    text = _plain_text(surface)
    empty_p = len(_EMPTY_P_RE.findall(surface))
    blocks = raw.count(_BLOCK_MARKER)
    headings = len(_H1_RE.findall(surface))
    em_dashes = text.count("—")

    issues: list[str] = []
    if empty_p:
        issues.append(
            f"{empty_p} paragraphe(s) vide(s) au rendu : signature de wpautop "
            "sur un contenu ayant perdu ses délimiteurs de blocs"
        )
    if not blocks:
        issues.append(
            "aucun délimiteur `<!-- wp:` dans la source : contenu classique, "
            "wpautop reste actif"
        )
    if no_alt:
        issues.append(f"{no_alt} image(s) sans alternative textuelle")
    if headings > 1:
        issues.append(f"{headings} titres de niveau 1 : un seul attendu")
    if em_dashes:
        issues.append(f"{em_dashes} tiret(s) cadratin(s) dans le corps (souvent proscrits par les chartes de rédaction)")

    return {
        "blocks": blocks,
        "empty_paragraphs": empty_p,
        "images": len(imgs),
        "images_without_alt": no_alt,
        "h1": headings,
        "em_dashes": em_dashes,
        "chars": len(raw),
        "words": len(text.split()) if text else 0,
        "measured_on": "rendered" if rendered else "raw",
        "issues": issues,
        "clean": not issues,
    }


def _parse_id_list(value: str) -> list[int] | None:
    """"12, 34" -> [12, 34]. Renvoie None si la chaîne ne contient aucun entier."""
    ids = [int(part) for part in re.findall(r"\d+", value or "")]
    return ids or None


def _format_error(exc: Exception) -> str:
    """Nomme le défaut, l'état de l'écriture et les écritures déjà appliquées.

    Jamais un « Erreur : » nu : une exception sans message (httpx.ReadError,
    ReadTimeout, RemoteProtocolError) garde son type et le sens de sa famille,
    et l'appelant doit pouvoir distinguer une défaillance réseau d'un refus,
    puis décider quoi relire.
    """
    if isinstance(exc, WordPressTransportError):
        message = f"Erreur réseau {exc}"
    elif isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        code = ""
        try:
            body = exc.response.json()
            msg = body.get("message") or exc.response.text[:200]
            # Le `code` REST (rest_invalid_param, rest_cannot_edit…) dit
            # précisément CE QUI est refusé, là où le message se contente
            # souvent de le paraphraser. Le jeter coûtait un aller-retour.
            code = str(body.get("code") or "")
        except Exception:
            msg = exc.response.text[:200]
        msg = str(msg).strip() or "sans message."
        message = f"Erreur HTTP {status} : {msg}"
        if code:
            message = _append_sentence(message, f" Code REST : {code}.")
        if getattr(exc, "wp_mutating", False):
            label = getattr(exc, "wp_label", "inconnue")
            if status >= 500:
                message = _append_sentence(
                    message, _write_state_message("inconnu", label))
            elif status >= 400 and status not in (401, 403, 404):
                # 401, 403 et 404 gardent leur message exact : une auth refusée
                # n'écrit rien et n'a pas besoin qu'on en discute. Le reste
                # (400 sur un paramètre invalide, 413 sur un média trop lourd)
                # est refusé avant exécution : le dire évite une relecture
                # inutile, et un rejeu à l'identique échouerait pareil.
                message = _append_sentence(
                    message,
                    f" Écriture {label} non appliquée : la requête a été refusée "
                    "avant exécution, corriger l'appel avant de rejouer.")
    elif isinstance(exc, json.JSONDecodeError):
        message = f"Erreur JSON WordPress : {exc}"
    elif isinstance(exc, httpx.TransportError):
        kind = type(exc).__name__
        detail = str(exc).strip() or _TRANSPORT_DEFAULT_DETAIL.get(
            kind, "échec du transport HTTP")
        message = f"Erreur réseau {kind} : {detail}."
    else:
        text = str(exc)
        message = (f"Erreur : {text}" if text.strip()
                   else f"Erreur {type(exc).__name__} : sans message.")
    journal = _write_journal()
    if journal:
        message = _append_sentence(
            message,
            f" Écritures déjà appliquées dans cet appel d'outil : {', '.join(journal)}. "
            "Relire les objets concernés avant de rejouer l'appel d'outil complet.")
    return message


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

    Ne jamais repartir de `content_html` (le `rendered` de l'API) pour une
    réécriture : WordPress y a déjà supprimé les délimiteurs de blocs, et
    renvoyer ce contenu transforme la publication en contenu classique, ce qui
    réactive wpautop et injecte des paragraphes vides.

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

    warning = await _gutenberg_downgrade_warning(server, f"pages/{page_id}", content, subsite)
    try:
        p = await _wp_put(server, f"pages/{page_id}", data, subsite=subsite)
        return json.dumps(_with_warning({
            "updated": True,
            "id": p.get("id"),
            "link": p.get("link"),
            "status": p.get("status"),
            "title": p.get("title", {}).get("rendered", ""),
            "modified": p.get("modified", "")[:19] if p.get("modified") else "",
        }, warning), ensure_ascii=False, separators=(",", ":"))
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

    Ne jamais repartir de `content_html` (le `rendered` de l'API) pour une
    réécriture : WordPress y a déjà supprimé les délimiteurs de blocs, et
    renvoyer ce contenu transforme la publication en contenu classique, ce qui
    réactive wpautop et injecte des paragraphes vides.

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
    slug: str = "",
    excerpt: str = "",
    categories: str = "",
    tags: str = "",
    featured_media: int = 0,
    date: str = "",
    acf_fields: str = "",
) -> str:
    """Met à jour un article WordPress.

    Limite : seules les deux taxonomies natives sont adressables ici, via
    `categories` et `tags`. Pour une taxonomie personnalisée attachée aux
    articles, passer par wordpress_update_custom_post(post_type="posts",
    taxonomies='{"<taxonomie>": [<ids>]}'), qui vérifie la taxonomie avant
    d'écrire.

    Args:
        post_id: ID de l'article
        server: "prod" ou "test"
        subsite: "" (défaut, site principal) | clé d'un sous-site déclaré dans WP_<SERVER>_SUBSITES
        title: nouveau titre (ignoré si vide)
        content: nouveau contenu HTML (ignoré si vide)
        status: "publish" | "draft" | "private" (ignoré si vide)
        slug: nouvel identifiant URL (ignoré si vide)
        excerpt: nouveau chapeau / extrait (ignoré si vide)
        categories: IDs de catégories séparés par des virgules (ignoré si vide)
        tags: IDs d'étiquettes séparés par des virgules (ignoré si vide)
        featured_media: ID du média mis en avant (ignoré si 0)
        date: date de publication ISO 8601 (ignoré si vide)
        acf_fields: JSON des champs ACF (ex: '{"champ": "valeur"}')
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
    if excerpt:
        data["excerpt"] = excerpt
    if _parse_id_list(categories):
        data["categories"] = _parse_id_list(categories)
    if _parse_id_list(tags):
        data["tags"] = _parse_id_list(tags)
    if featured_media:
        data["featured_media"] = featured_media
    if date:
        data["date"] = date
    if acf_fields:
        try:
            data["acf"] = json.loads(acf_fields)
        except json.JSONDecodeError as e:
            return f"acf_fields JSON invalide : {e}"
    if not data:
        return "Aucun champ à modifier."
    warning = await _gutenberg_downgrade_warning(server, f"posts/{post_id}", content, subsite)
    try:
        p = await _wp_put(server, f"posts/{post_id}", data, subsite=subsite)
        return json.dumps(_with_warning({
            "updated": True,
            "id": p.get("id"),
            "title": p.get("title", {}).get("rendered", ""),
            "status": p.get("status"),
        }, warning), ensure_ascii=False, separators=(",", ":"))
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
# Taxonomies — découverte et garde-fous d'écriture
# ---------------------------------------------------------------------------
#
# L'API REST de WordPress ignore en silence tout champ absent du type visé :
# écrire `tags` sur un type qui ne déclare pas post_tag renvoie 200 sans rien
# poser. Les outils d'écriture valident donc les taxonomies avant l'appel, puis
# comparent le résultat à ce qui a été demandé.


async def _rest_taxonomies_for_type(
    server: str, post_type: str, subsite: str = ""
) -> tuple[dict[str, dict], str]:
    """Taxonomies REST d'un type de contenu, indexées par nom de taxonomie.

    `post_type` est accepté sous sa forme rest_base (celle qu'attendent les
    outils CPT) ou sous son slug interne. Un dictionnaire vide avec une raison
    non vide signale une découverte impossible ; vide avec une raison vide
    signale un type sans aucune taxonomie exposée en REST.
    """
    try:
        types = await _wp_get(server, "types", subsite=subsite)
    except Exception as exc:
        return {}, f"lecture de /types impossible ({exc})"
    if not isinstance(types, dict):
        return {}, "réponse inattendue de /types"

    slug = ""
    for key, info in types.items():
        if not isinstance(info, dict):
            continue
        if info.get("rest_base") == post_type:
            slug = key
            break
        if key == post_type and not slug:
            slug = key
    if not slug:
        return {}, f"type de contenu inconnu : {post_type}"

    try:
        taxos = await _wp_get(server, "taxonomies", {"type": slug}, subsite=subsite)
    except Exception as exc:
        return {}, f"lecture de /taxonomies impossible ({exc})"
    if not isinstance(taxos, dict):
        return {}, "réponse inattendue de /taxonomies"
    return _normalize_taxonomies(taxos), ""


def _normalize_taxonomies(raw: dict) -> dict[str, dict]:
    """Retient, par nom de taxonomie, le slug, le champ REST et la hiérarchie."""
    return {
        name: {
            "slug": info.get("slug", name),
            "rest_base": info.get("rest_base") or name,
            "hierarchical": bool(info.get("hierarchical", False)),
        }
        for name, info in raw.items()
        if isinstance(info, dict)
    }


def _taxonomy_index(known: dict[str, dict]) -> dict[str, str]:
    """Alias (nom, slug, champ REST) -> nom canonique de la taxonomie."""
    index: dict[str, str] = {}
    for name, info in known.items():
        for alias in (name, info["slug"], info["rest_base"]):
            index.setdefault(alias, name)
    return index


def _describe_taxonomies(known: dict[str, dict]) -> str:
    if not known:
        return "(aucune)"
    parts = []
    for name in sorted(known):
        base = known[name]["rest_base"]
        parts.append(name if base == name else f"{name} (champ REST : {base})")
    return ", ".join(parts)


def _taxonomy_absent_message(
    label: str, scope: str, known: dict[str, dict], hint: str = ""
) -> str:
    return (
        f"Taxonomie {label} absente du {scope}. "
        f"Taxonomies disponibles : {_describe_taxonomies(known)}."
        + (f" {hint}" if hint else "")
    )


def _term_ids_error(key: str, item: Any) -> str:
    return (
        f"taxonomies.{key} : seuls des IDs de termes (entiers) sont acceptés, "
        f"reçu {item!r}. Résoudre le nom en ID avec "
        f'wordpress_list_terms(taxonomy="{key}", search=…) — cet outil ne crée '
        "aucun terme."
    )


def _coerce_term_ids(key: str, value: Any) -> tuple[list[int], str]:
    """Normalise 3 | [3, 9] | "3, 9" | [] en liste d'IDs. [] = retirer tout."""
    if isinstance(value, str):
        items: list = [p for p in re.split(r"[,\s]+", value.strip()) if p]
    elif isinstance(value, list):
        items = value
    else:
        items = [value]
    ids: list[int] = []
    for item in items:
        if isinstance(item, bool) or not isinstance(item, (int, str)):
            return [], _term_ids_error(key, item)
        if isinstance(item, str):
            if not item.strip().lstrip("+").isdigit():
                return [], _term_ids_error(key, item)
            ids.append(int(item))
        else:
            ids.append(item)
    return ids, ""


def _parse_taxonomies_arg(raw: str) -> tuple[list[tuple[str, list[int]]], str]:
    """'{"blog-type": [3], "global_tags": "368"}' -> [("blog-type", [3]), ...]."""
    if not raw:
        return [], ""
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        return [], f"taxonomies : JSON invalide ({exc})."
    if not isinstance(parsed, dict):
        return [], (
            'taxonomies attend un objet JSON {"<taxonomie>": [<ids>]}, reçu : '
            f"{type(parsed).__name__}."
        )
    out: list[tuple[str, list[int]]] = []
    for key, value in parsed.items():
        ids, err = _coerce_term_ids(str(key), value)
        if err:
            return [], err
        out.append((str(key), ids))
    return out, ""


async def _taxonomy_fields_for_write(
    server: str,
    post_type: str,
    subsite: str,
    categories: str,
    tags: str,
    taxonomies: str,
) -> tuple[dict[str, list[int]], str, str]:
    """Champs REST de taxonomie à écrire pour un type donné.

    Renvoie (champs, avertissement, erreur bloquante). Aucun appel réseau si
    aucune taxonomie n'est demandée. Une erreur bloquante doit interrompre
    l'écriture : c'est le cas d'une taxonomie que le type ne porte pas, qui
    serait sinon acceptée puis jetée par WordPress.
    """
    requested: list[tuple[str, str, list[int]]] = []
    cat_ids = _parse_id_list(categories)
    if cat_ids:
        requested.append(("category", "categories", cat_ids))
    tag_ids = _parse_id_list(tags)
    if tag_ids:
        requested.append(("post_tag", "tags", tag_ids))
    generic, err = _parse_taxonomies_arg(taxonomies)
    if err:
        return {}, "", err
    requested.extend((key, key, ids) for key, ids in generic)
    if not requested:
        return {}, "", ""

    known, reason = await _rest_taxonomies_for_type(server, post_type, subsite)
    if reason:
        # Découverte indisponible : ne pas casser une écriture qui fonctionnait.
        # Le contrôle après écriture dira ce qui a réellement été enregistré.
        return (
            {key: ids for _, key, ids in requested},
            f"Taxonomies non validées avant écriture ({reason}) : "
            "se fier au champ `taxonomies` du retour.",
            "",
        )

    index = _taxonomy_index(known)
    fields: dict[str, list[int]] = {}
    for label, key, ids in requested:
        name = index.get(key)
        if not name:
            return {}, "", _taxonomy_absent_message(
                label,
                f"type {post_type}",
                known,
                "Écrire une taxonomie du type avec "
                "taxonomies='{\"<taxonomie>\": [<ids>]}', après avoir relevé ses "
                "IDs de termes avec wordpress_list_terms.",
            )
        fields[known[name]["rest_base"]] = ids
    return fields, "", ""


def _taxonomy_report(payload: Any, sent: dict[str, list[int]], warning: str = "") -> dict:
    """Compare les taxonomies demandées à celles que WordPress a enregistrées."""
    if not sent:
        return {"taxonomies_warning": warning} if warning else {}
    recorded: dict[str, list[int]] = {}
    ignored: list[str] = []
    diverged: list[str] = []
    for field, ids in sent.items():
        if not isinstance(payload, dict) or field not in payload:
            ignored.append(field)
            continue
        got = payload.get(field)
        got = [t for t in got if isinstance(t, int)] if isinstance(got, list) else []
        recorded[field] = got
        if sorted(got) != sorted(ids):
            diverged.append(field)

    report: dict = {}
    if recorded:
        report["taxonomies"] = recorded
    if ignored:
        report["taxonomies_ignored"] = ignored
    messages = [warning] if warning else []
    if ignored:
        messages.append(
            "WordPress a ignoré ces champs, absents du type : "
            + ", ".join(ignored)
            + " — rien n'y a été écrit."
        )
    if diverged:
        messages.append(
            "Termes enregistrés différents de ceux demandés pour : "
            + ", ".join(diverged)
            + "."
        )
    if messages:
        report["taxonomies_warning"] = " ".join(messages)
    return report


# ---------------------------------------------------------------------------
# Tools — Types de contenu & taxonomies
# ---------------------------------------------------------------------------


@mcp.tool()
async def wordpress_list_post_types(
    server: str = "prod",
    subsite: str = "",
) -> str:
    """Liste les types de contenu du site (pages, posts, CPT) et leurs taxonomies.

    Chaque type porte `taxonomies` : la liste des taxonomies qui lui sont
    attachées, avec leur `rest_base`, c'est-à-dire le nom du champ à écrire sur
    une publication de ce type (post_tag s'écrit dans `tags`, category dans
    `categories`, une taxonomie dédiée le plus souvent sous son propre nom).
    C'est cette liste qui dit ce que wordpress_create_custom_post et
    wordpress_update_custom_post peuvent écrire via leur paramètre `taxonomies`.

    Limite non contournable : WordPress n'expose ici que les taxonomies
    déclarées avec show_in_rest. Une taxonomie absente de cette liste n'est pas
    lisible ni modifiable par l'API REST, donc pas par ce connecteur — il faut
    la déclarer côté WordPress (argument show_in_rest de register_taxonomy).
    Si /taxonomies est injoignable, `rest_base` est omis et `taxonomies_note`
    l'explique.

    Args:
        server: "prod" ou "test"
        subsite: "" (défaut, site principal) | clé d'un sous-site déclaré dans WP_<SERVER>_SUBSITES
    """
    try:
        types = await _wp_get(server, "types", subsite=subsite)
        if not isinstance(types, dict):
            return f"Réponse inattendue : {types}"

        taxonomies: dict[str, dict] = {}
        note = ""
        try:
            raw = await _wp_get(server, "taxonomies", subsite=subsite)
            if isinstance(raw, dict):
                taxonomies = _normalize_taxonomies(raw)
            else:
                note = "réponse inattendue de /taxonomies : rest_base non résolu"
        except Exception as exc:
            note = f"rest_base des taxonomies non résolu ({exc})"

        result = []
        for slug, info in types.items():
            attached = []
            for name in info.get("taxonomies", []) or []:
                known = taxonomies.get(name)
                if known:
                    attached.append({
                        "slug": name,
                        "rest_base": known["rest_base"],
                        "hierarchical": known["hierarchical"],
                    })
                else:
                    attached.append({"slug": name})
            result.append({
                "slug": slug,
                "name": info.get("name", ""),
                "rest_base": info.get("rest_base", ""),
                "hierarchical": info.get("hierarchical", False),
                "taxonomies": attached,
            })
        payload = {
            "server": server,
            "count": len(result),
            "types": result,
        }
        if note:
            payload["taxonomies_note"] = note
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
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
    slug: str = "",
    excerpt: str = "",
    categories: str = "",
    tags: str = "",
    featured_media: int = 0,
    date: str = "",
    acf_fields: str = "",
    taxonomies: str = "",
) -> str:
    """Met à jour un contenu de type personnalisé (CPT) : champs, ACF, taxonomies.

    Toute taxonomie du type s'écrit avec `taxonomies`, y compris celles qui ne
    sont ni category ni post_tag. Les taxonomies demandées sont vérifiées
    contre celles du type AVANT l'écriture : une taxonomie que le type ne porte
    pas fait échouer l'appel avec la liste de celles qui existent, au lieu
    d'être acceptée puis jetée en silence par WordPress. Après l'écriture, le
    retour porte `taxonomies` (ce qui est réellement enregistré) et, en cas
    d'écart, `taxonomies_ignored` et `taxonomies_warning`.

    `categories` et `tags` restent acceptés : ce sont les raccourcis des
    taxonomies natives category et post_tag, refusés désormais si le type ne
    les déclare pas. Ils visent les mêmes champs REST que
    taxonomies='{"category": [...], "post_tag": [...]}'; en cas de doublon,
    c'est `taxonomies` qui l'emporte.

    Limites non contournables : les termes s'adressent par ID (aucune
    résolution de nom ni création de terme ici — passer par
    wordpress_list_terms), et seules les taxonomies déclarées avec show_in_rest
    sont accessibles.

    Args:
        post_type: rest_base du type (ex: "produit", "membre")
        post_id: ID de la publication
        server: "prod" ou "test"
        subsite: "" (défaut, site principal) | clé d'un sous-site déclaré dans WP_<SERVER>_SUBSITES
        title: nouveau titre (ignoré si vide)
        content: nouveau contenu HTML (ignoré si vide)
        status: "publish" | "draft" | "private" (ignoré si vide)
        slug: nouvel identifiant URL (ignoré si vide)
        excerpt: nouveau chapeau / extrait (ignoré si vide)
        categories: IDs de catégories séparés par des virgules (ignoré si vide) — refusé si le type ne porte pas la taxonomie category
        tags: IDs d'étiquettes séparés par des virgules (ignoré si vide) — refusé si le type ne porte pas la taxonomie post_tag
        featured_media: ID du média mis en avant (ignoré si 0)
        date: date de publication ISO 8601 (ignoré si vide)
        acf_fields: JSON des champs ACF (ex: '{"nom": "valeur", "date": "2025-01-01"}')
        taxonomies: JSON taxonomie -> IDs de termes, pour toute taxonomie du type
            (ex: '{"blog-type": [3], "global_tags": [368, 12]}'). Clé acceptée
            sous son nom de taxonomie ou son rest_base ; valeur acceptée en
            liste, en entier seul ou en "3, 9". Liste vide = retirer tous les
            termes. Découvrir les taxonomies avec wordpress_list_post_types et
            les IDs avec wordpress_list_terms.
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
    if excerpt:
        data["excerpt"] = excerpt
    if featured_media:
        data["featured_media"] = featured_media
    if date:
        data["date"] = date
    if acf_fields:
        try:
            data["acf"] = json.loads(acf_fields)
        except json.JSONDecodeError as e:
            return f"acf_fields JSON invalide : {e}"

    tax_fields, tax_warning, tax_error = await _taxonomy_fields_for_write(
        server, post_type, subsite, categories, tags, taxonomies
    )
    if tax_error:
        return tax_error
    data.update(tax_fields)
    if not data:
        return "Aucun champ à modifier."

    warning = await _gutenberg_downgrade_warning(server, f"{post_type}/{post_id}", content, subsite)
    try:
        p = await _wp_put(server, f"{post_type}/{post_id}", data, subsite=subsite)
        result = _with_warning({
            "updated": True,
            "id": p.get("id"),
            "title": p.get("title", {}).get("rendered", "") if isinstance(p.get("title"), dict) else str(p.get("title", "")),
            "status": p.get("status"),
            "link": p.get("link", ""),
        }, warning)
        result.update(_taxonomy_report(p, tax_fields, tax_warning))
        return json.dumps(result, ensure_ascii=False, separators=(",", ":"))
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

    Ne jamais repartir de `content_html` (le `rendered` de l'API) pour une
    réécriture : WordPress y a déjà supprimé les délimiteurs de blocs, et
    renvoyer ce contenu transforme la publication en contenu classique, ce qui
    réactive wpautop et injecte des paragraphes vides.

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
    excerpt: str = "",
    categories: str = "",
    tags: str = "",
    featured_media: int = 0,
    date: str = "",
    acf_fields: str = "",
    server: str = "prod",
    subsite: str = "",
    taxonomies: str = "",
) -> str:
    """Crée une nouvelle publication d'un type personnalisé (CPT).

    Le rest_base du type dépend du site : le découvrir via
    wordpress_list_post_types. Pour viser un sous-site, passer sa clé en `subsite`.

    Mêmes règles de taxonomie que wordpress_update_custom_post : `taxonomies`
    écrit n'importe quelle taxonomie du type, une taxonomie que le type ne
    porte pas fait échouer l'appel avant écriture plutôt que d'être ignorée en
    silence, et le retour porte les termes réellement enregistrés.

    Limites non contournables : termes adressés par ID uniquement (pas de
    résolution de nom ni de création de terme), et seules les taxonomies
    déclarées avec show_in_rest sont accessibles.

    Args:
        post_type: rest_base du type (découvert via wordpress_list_post_types)
        title: titre de la publication
        content: contenu HTML (optionnel)
        status: "draft" (défaut) | "publish" | "private" | "pending"
        slug: identifiant URL (généré automatiquement si vide)
        excerpt: chapeau / extrait (ignoré si vide)
        categories: IDs de catégories séparés par des virgules (ignoré si vide) — refusé si le type ne porte pas la taxonomie category
        tags: IDs d'étiquettes séparés par des virgules (ignoré si vide) — refusé si le type ne porte pas la taxonomie post_tag
        featured_media: ID du média mis en avant (ignoré si 0)
        date: date de publication ISO 8601 (ignoré si vide)
        acf_fields: JSON des champs ACF (ex: '{"chapeau": "..."}')
        server: "prod" ou "test"
        subsite: "" (défaut, site principal) | clé d'un sous-site déclaré dans WP_<SERVER>_SUBSITES
        taxonomies: JSON taxonomie -> IDs de termes
            (ex: '{"blog-type": [3], "global_tags": [368]}'). Liste vide = aucun
            terme. Découvrir les taxonomies avec wordpress_list_post_types et
            les IDs avec wordpress_list_terms.
    """
    data: dict = {
        "title": title,
        "content": content,
        "status": status,
    }
    if slug:
        data["slug"] = slug
    if excerpt:
        data["excerpt"] = excerpt
    if featured_media:
        data["featured_media"] = featured_media
    if date:
        data["date"] = date
    if acf_fields:
        try:
            data["acf"] = json.loads(acf_fields)
        except json.JSONDecodeError as e:
            return f"acf_fields JSON invalide : {e}"

    tax_fields, tax_warning, tax_error = await _taxonomy_fields_for_write(
        server, post_type, subsite, categories, tags, taxonomies
    )
    if tax_error:
        return tax_error
    data.update(tax_fields)

    try:
        p = await _wp_post(server, post_type, data, subsite=subsite)
        result = {
            "created": True,
            "id": p.get("id"),
            "link": p.get("link"),
            "status": p.get("status"),
            "title": p.get("title", {}).get("rendered", "") if isinstance(p.get("title"), dict) else str(p.get("title", "")),
        }
        result.update(_taxonomy_report(p, tax_fields, tax_warning))
        return json.dumps(result, ensure_ascii=False, separators=(",", ":"))
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
        url = _api_url(server, "media", subsite)

        # Le dépôt passe par _request comme toute autre écriture : sans cela,
        # il n'était ni nommé ni journalisé, et l'échec de la mise à jour qui
        # suit laissait croire que rien n'avait été déposé.
        media = await _request(
            server, "POST", url,
            extra_headers={
                "Content-Type": mime,
                "Content-Disposition": f'attachment; filename="{path.name}"',
            },
            timeout=60,
            content=file_bytes,
        )
        if not isinstance(media, dict):
            return (f"Réponse inattendue au dépôt de {path.name} : "
                    f"{str(media)[:200]}")

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


# ---------------------------------------------------------------------------
# Version du core et extensions actives
#
# L'API REST n'expose ni l'une ni les autres. Portes depuis le connecteur
# prive le 11/08/2026 : ce sont les deux informations que reclame un audit
# de vulnerabilites, et elles manquaient au connecteur public.
# ---------------------------------------------------------------------------


def _xml_escape(value: str) -> str:
    """Échappe les caractères spéciaux XML (corps des requêtes XML-RPC)."""
    return (
        value.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&apos;")
    )


def _parse_xmlrpc_software_version(xml_text: str) -> str | None:
    """Extrait `software_version` d'une réponse XML-RPC wp.getOptions.

    Structure retournée par WordPress :
    struct → member[name=software_version] → struct → member[name=value] → string
    """
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return None
    for member in root.iter("member"):
        name_el = member.find("name")
        if name_el is None or (name_el.text or "").strip() != "software_version":
            continue
        for sub in member.iter("member"):
            sub_name = sub.find("name")
            if sub_name is not None and (sub_name.text or "").strip() == "value":
                val = sub.find("./value/string")
                if val is not None and val.text and val.text.strip():
                    return val.text.strip()
    return None


async def _fetch_core_version(server: str, subsite: str = "") -> tuple[str | None, str, str]:
    """Détermine la version du core WordPress. Retourne (version, source, note).

    L'API REST n'expose **pas** la version du core (`/wp-json/` ne la contient
    pas, `wp-site-health/v1` non plus). Trois stratégies successives :

    1. XML-RPC `wp.getOptions` → option `software_version`. Authentifié via
       l'Application Password (accepté par XML-RPC comme par REST) : c'est la
       source la plus fiable, et la seule qui fonctionne quand le thème ou un
       durcissement supprime le tag generator — cas vérifié en production.
    2. `<meta name="generator" content="WordPress X.Y.Z">` de la home.
    3. Paramètre `?ver=` d'un asset `wp-includes/` de la home.

    Si aucune ne fonctionne, retourne (None, "", note explicative) — jamais
    d'échec silencieux.
    """
    cfg = _SERVER_CONFIGS[server]
    base, _ = _subsite_route(server, subsite)
    verify_ssl = cfg.get("verify_ssl", True)
    notes: list[str] = []

    async with httpx.AsyncClient(verify=verify_ssl, timeout=30, follow_redirects=True) as client:
        # 1. XML-RPC wp.getOptions
        if cfg.get("user") and cfg.get("password"):
            payload = (
                '<?xml version="1.0"?><methodCall>'
                "<methodName>wp.getOptions</methodName><params>"
                "<param><value><int>1</int></value></param>"
                f"<param><value><string>{_xml_escape(cfg['user'])}</string></value></param>"
                f"<param><value><string>{_xml_escape(cfg['password'])}</string></value></param>"
                "<param><value><array><data>"
                "<value><string>software_version</string></value>"
                "</data></array></value></param>"
                "</params></methodCall>"
            )
            headers = {"Content-Type": "text/xml; charset=utf-8"}
            # Basic Auth serveur (Nginx) éventuelle — les credentials WP sont
            # dans le corps XML-RPC, pas dans l'en-tête.
            if cfg.get("server_auth_user") and cfg.get("server_auth_pass"):
                headers["Authorization"] = _get_auth_header(server)
            try:
                resp = await client.post(
                    f"{cfg['url']}/xmlrpc.php", content=payload.encode("utf-8"), headers=headers
                )
                if resp.status_code == 200:
                    version = _parse_xmlrpc_software_version(resp.text)
                    if version:
                        return version, "xmlrpc:wp.getOptions", ""
                    notes.append("XML-RPC joignable mais wp.getOptions n'a pas retourné software_version")
                else:
                    notes.append(f"XML-RPC indisponible (HTTP {resp.status_code})")
            except Exception as exc:
                notes.append(f"XML-RPC en erreur ({type(exc).__name__})")
        else:
            notes.append("XML-RPC non tenté (Application Password non configuré)")

        # 2 & 3. Page d'accueil : meta generator puis ?ver= d'un asset core
        try:
            home = await client.get(f"{base}/", headers={"Accept": "text/html"})
            if home.status_code == 200:
                gen = re.search(
                    r'<meta[^>]+name=["\']generator["\'][^>]+content=["\']WordPress\s+([0-9][0-9.]*)',
                    home.text, re.I,
                )
                if gen:
                    return gen.group(1), "meta:generator", ""
                notes.append("meta generator absent de la home (hardening ou thème)")

                vers = re.findall(r'wp-includes/[^"\' ]*?[?&]ver=([0-9]+\.[0-9]+(?:\.[0-9]+)?)', home.text)
                if vers:
                    return max(vers), "asset:?ver=", ""
                notes.append("aucun asset wp-includes avec ?ver= exploitable")
            else:
                notes.append(f"home injoignable (HTTP {home.status_code})")
        except Exception as exc:
            notes.append(f"lecture de la home en erreur ({type(exc).__name__})")

    return None, "", " ; ".join(notes)


async def _fetch_active_plugins(server: str, subsite: str = "") -> tuple[list[dict] | None, str]:
    """Liste les extensions actives avec leur version (utile pour un audit CVE).

    Utilise `/wp/v2/plugins` (WP 5.5+), qui exige la capacité `activate_plugins`.
    Retourne (liste, note) — liste None si l'endpoint est refusé/absent, avec
    une note expliquant pourquoi.
    """
    try:
        data = await _wp_get(server, "plugins", subsite=subsite)
    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code
        if status in (401, 403):
            return None, (
                f"/wp/v2/plugins refusé (HTTP {status}) : l'Application Password utilisé "
                "n'a pas la capacité 'activate_plugins'."
            )
        if status == 404:
            return None, "/wp/v2/plugins absent (WordPress < 5.5 ou endpoint désactivé)."
        return None, f"/wp/v2/plugins en erreur (HTTP {status})."
    except Exception as exc:
        return None, f"/wp/v2/plugins injoignable ({type(exc).__name__})."

    if not isinstance(data, list):
        return None, "/wp/v2/plugins a retourné une réponse inattendue."

    actives = [
        {
            "name": p.get("name", ""),
            "version": p.get("version", ""),
            "plugin": p.get("plugin", ""),
            "status": p.get("status", ""),
        }
        for p in data
        if p.get("status") in ("active", "network-active")
    ]
    return actives, ""


@mcp.tool()
async def wordpress_site_info(
    server: str = "prod",
    subsite: str = "",
) -> str:
    """Informations générales sur le site WordPress (nom, URL, version du core,
    extensions actives, multisite...).

    La version du core n'est **pas** exposée par l'API REST : elle est obtenue
    via XML-RPC `wp.getOptions` (authentifié), avec repli sur le tag
    `<meta name="generator">` puis sur le `?ver=` d'un asset core. Si aucune
    méthode n'aboutit, `core_version` vaut null et `core_version_note` explique
    pourquoi (jamais d'échec silencieux).

    Les extensions actives sont listées avec leur version (audit CVE) via
    `/wp/v2/plugins` ; en cas de droits insuffisants, `plugins_note` le signale.

    Args:
        server: "prod" ou "test"
        subsite: "" (défaut, site principal) | clé d'un sous-site déclaré dans WP_<SERVER>_SUBSITES
    """
    try:
        cfg = _SERVER_CONFIGS[server]
        if not cfg["url"]:
            return f"Serveur '{server}' non configuré (WP_{server.upper()}_URL manquant)."
        base, _ = _subsite_route(server, subsite)
        url = f"{base}/wp-json/"
        auth = _get_auth_header(server)
        async with httpx.AsyncClient(verify=cfg.get("verify_ssl", True), timeout=30) as client:
            resp = await client.get(url, headers={"Authorization": auth, "Accept": "application/json"})
            resp.raise_for_status()
            data = resp.json()

        core_version, core_source, core_note = await _fetch_core_version(server, subsite)
        plugins, plugins_note = await _fetch_active_plugins(server, subsite)

        result: dict[str, Any] = {
            "server": server,
            "url": cfg["url"],
            "name": data.get("name", ""),
            "description": data.get("description", ""),
            "url_site": data.get("url", ""),
            "home": data.get("home", ""),
            "gmt_offset": data.get("gmt_offset", 0),
            "timezone": data.get("timezone_string", ""),
            "namespaces": data.get("namespaces", []),
            "core_version": core_version,
            "core_version_source": core_source,
        }
        if core_version is None:
            result["core_version_note"] = (
                "Version du core indéterminable : "
                + (core_note or "aucune source disponible")
                + ". Vérifier manuellement dans wp-admin → Tableau de bord → Mises à jour."
            )
        if plugins is None:
            result["plugins_active"] = None
            result["plugins_note"] = plugins_note
        else:
            result["plugins_active"] = plugins
            result["plugins_active_count"] = len(plugins)

        return json.dumps(result, ensure_ascii=False, separators=(",", ":"))
    except Exception as exc:
        return _format_error(exc)


@mcp.tool()
async def wordpress_list_categories(
    server: str = "prod",
    subsite: str = "",
    per_page: int = 100,
    hide_empty: bool = False,
) -> str:
    """Liste les termes de la taxonomie native `category`.

    Limite : cet outil n'adresse que la taxonomie native category, celle des
    articles et des types qui la déclarent explicitement. Un type qui porte ses
    propres taxonomies (une rubrique dédiée, un vocabulaire maison) n'apparaît
    pas ici : passer par wordpress_list_terms, après avoir lu les taxonomies du
    type dans wordpress_list_post_types.

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
# Tools — Révisions (rattrapage après une écriture qui a dégradé un contenu)
# ---------------------------------------------------------------------------


@mcp.tool()
async def wordpress_list_revisions(
    post_id: int,
    post_type: str = "posts",
    server: str = "prod",
    subsite: str = "",
    per_page: int = 10,
) -> str:
    """Liste les révisions d'une publication, de la plus récente à la plus ancienne.

    C'est le filet de sécurité après une écriture qui a dégradé un contenu :
    WordPress conserve l'état antérieur, `wordpress_restore_revision` le remet
    en place. Attention : certains sites n'utilisent pas ce type natif pour leur
    blog mais un CPT dédié. Vérifier via wordpress_list_post_types.

    Chaque entrée porte `blocks` (nombre de délimiteurs `<!-- wp:` de la
    révision) et `empty_paragraphs` : c'est ce qui permet de repérer la
    dernière révision d'avant un passage en contenu classique.

    Args:
        post_id: ID de la publication
        post_type: rest_base du type ("posts", "pages", ou un CPT)
        server: "prod" ou "test"
        subsite: "" (défaut, site principal) | clé d'un sous-site déclaré dans WP_<SERVER>_SUBSITES
        per_page: nombre de révisions à remonter (défaut 10)
    """
    params = {"context": "edit", "per_page": min(per_page, MAX_PER_PAGE)}
    try:
        revs = await _wp_get(
            server, f"{post_type}/{post_id}/revisions", params, subsite=subsite
        )
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 404:
            return (
                f"Aucun historique pour {post_type}/{post_id} : ce type ne déclare "
                "peut-être pas le support des révisions (add_post_type_support), "
                "ou l'ID n'existe pas."
            )
        return _format_error(exc)
    except Exception as exc:
        return _format_error(exc)

    if not isinstance(revs, list):
        return f"Réponse inattendue : {revs}"

    out = []
    for r in revs:
        content_obj = r.get("content", {}) if isinstance(r.get("content"), dict) else {}
        raw = content_obj.get("raw", "") or content_obj.get("rendered", "")
        title_obj = r.get("title", {}) if isinstance(r.get("title"), dict) else {}
        out.append({
            "id": r.get("id"),
            "date": r.get("modified", "") or r.get("date", ""),
            "author": r.get("author"),
            "title": title_obj.get("raw", "") or title_obj.get("rendered", ""),
            "chars": len(raw),
            "blocks": raw.count(_BLOCK_MARKER),
            "empty_paragraphs": len(_EMPTY_P_RE.findall(raw)),
        })
    return json.dumps({
        "server": server,
        "post_type": post_type,
        "post_id": post_id,
        "count": len(out),
        "revisions": out,
    }, ensure_ascii=False, separators=(",", ":"))


@mcp.tool()
async def wordpress_get_revision(
    post_id: int,
    revision_id: int,
    post_type: str = "posts",
    server: str = "prod",
    subsite: str = "",
) -> str:
    """Lit une révision précise, contenu source compris.

    Le champ `content` est la source de la révision (délimiteurs de blocs
    compris) : c'est ce qu'il faut comparer à l'état courant avant de décider
    d'une restauration.

    Args:
        post_id: ID de la publication parente
        revision_id: ID de la révision (via wordpress_list_revisions)
        post_type: rest_base du type ("posts", "pages", ou un CPT)
        server: "prod" ou "test"
        subsite: "" (défaut, site principal) | clé d'un sous-site déclaré dans WP_<SERVER>_SUBSITES
    """
    try:
        r = await _wp_get(
            server,
            f"{post_type}/{post_id}/revisions/{revision_id}",
            {"context": "edit"},
            subsite=subsite,
        )
    except Exception as exc:
        return _format_error(exc)

    if not isinstance(r, dict):
        return f"Réponse inattendue : {r}"
    result: dict = {
        "id": r.get("id"),
        "parent": r.get("parent"),
        "date": r.get("modified", "") or r.get("date", ""),
        "author": r.get("author"),
    }
    result.update(_content_fields(r, "edit"))
    result["health"] = _content_health(result.get("content", ""))
    return json.dumps(result, ensure_ascii=False, separators=(",", ":"))


@mcp.tool()
async def wordpress_restore_revision(
    post_id: int,
    revision_id: int,
    post_type: str = "posts",
    server: str = "prod",
    subsite: str = "",
    restore_title: bool = False,
) -> str:
    """Remet une publication dans l'état d'une de ses révisions.

    L'API REST n'expose pas de restauration : ce tool relit la révision en
    context=edit puis réécrit sa source dans la publication. Le contenu courant
    devient lui-même une révision, donc l'opération reste réversible par le même
    chemin. Le retour indique ce qui a été remplacé (`replaced_chars`) et l'état
    du contenu restauré.

    Args:
        post_id: ID de la publication à restaurer
        revision_id: ID de la révision à remettre en place
        post_type: rest_base du type ("posts", "pages", ou un CPT)
        server: "prod" ou "test"
        subsite: "" (défaut, site principal) | clé d'un sous-site déclaré dans WP_<SERVER>_SUBSITES
        restore_title: True = restaurer aussi le titre et le chapeau
    """
    try:
        rev = await _wp_get(
            server,
            f"{post_type}/{post_id}/revisions/{revision_id}",
            {"context": "edit"},
            subsite=subsite,
        )
    except Exception as exc:
        return _format_error(exc)
    if not isinstance(rev, dict):
        return f"Réponse inattendue : {rev}"

    fields = _content_fields(rev, "edit")
    content = fields.get("content", "")
    if not content:
        return f"La révision {revision_id} n'a pas de contenu source exploitable."

    data: dict = {"content": content}
    if restore_title:
        if fields.get("title_raw") or fields.get("title"):
            data["title"] = fields.get("title_raw") or fields.get("title")
        if fields.get("excerpt_raw"):
            data["excerpt"] = fields["excerpt_raw"]

    replaced_chars = None
    try:
        current = await _wp_get(
            server, f"{post_type}/{post_id}", {"context": "edit"}, subsite=subsite
        )
        if isinstance(current, dict) and isinstance(current.get("content"), dict):
            replaced_chars = len(current["content"].get("raw", ""))
    except Exception:
        pass

    warning = await _gutenberg_downgrade_warning(
        server, f"{post_type}/{post_id}", content, subsite
    )
    try:
        p = await _wp_put(server, f"{post_type}/{post_id}", data, subsite=subsite)
        return json.dumps(_with_warning({
            "restored": True,
            "id": p.get("id"),
            "from_revision": revision_id,
            "link": p.get("link", ""),
            "status": p.get("status"),
            "restored_chars": len(content),
            "replaced_chars": replaced_chars,
            "health": _content_health(content),
        }, warning), ensure_ascii=False, separators=(",", ":"))
    except Exception as exc:
        return _format_error(exc)


# ---------------------------------------------------------------------------
# Tools — Contrôle de contenu
# ---------------------------------------------------------------------------


@mcp.tool()
async def wordpress_check_content(
    post_id: int,
    post_type: str = "posts",
    server: str = "prod",
    subsite: str = "",
) -> str:
    """Contrôle l'état d'une publication sans la modifier.

    Relit la source et le rendu, puis relève les défauts qu'il fallait jusqu'ici
    vérifier à la main après une écriture : paragraphes vides injectés par
    wpautop, perte des délimiteurs de blocs, images sans alternative textuelle,
    titres de niveau 1 en double, tirets cadratins. `clean: true` signifie
    qu'aucun de ces contrôles ne remonte quoi que ce soit.

    À passer systématiquement après une écriture de contenu.

    Args:
        post_id: ID de la publication
        post_type: rest_base du type ("posts", "pages", ou un CPT)
        server: "prod" ou "test"
        subsite: "" (défaut, site principal) | clé d'un sous-site déclaré dans WP_<SERVER>_SUBSITES
    """
    try:
        p = await _wp_get(
            server, f"{post_type}/{post_id}", {"context": "edit"}, subsite=subsite
        )
    except Exception as exc:
        return _format_error(exc)
    if not isinstance(p, dict):
        return f"Réponse inattendue : {p}"

    content_obj = p.get("content", {}) if isinstance(p.get("content"), dict) else {}
    raw = content_obj.get("raw", "")
    rendered = content_obj.get("rendered", "")
    title_obj = p.get("title", {}) if isinstance(p.get("title"), dict) else {}
    return json.dumps({
        "id": p.get("id"),
        "title": title_obj.get("rendered", ""),
        "link": p.get("link", ""),
        "status": p.get("status", ""),
        "modified": p.get("modified", ""),
        **_content_health(raw if raw else rendered, rendered),
    }, ensure_ascii=False, separators=(",", ":"))


# ---------------------------------------------------------------------------
# Tools — Articles natifs : création et suppression
# ---------------------------------------------------------------------------


@mcp.tool()
async def wordpress_create_post(
    title: str,
    content: str = "",
    server: str = "prod",
    subsite: str = "",
    status: str = "draft",
    slug: str = "",
    excerpt: str = "",
    categories: str = "",
    tags: str = "",
    featured_media: int = 0,
    date: str = "",
    acf_fields: str = "",
) -> str:
    """Crée un article natif WordPress (rest_base="posts").

    Attention : certains sites n'utilisent pas ce type natif pour leur
    blog mais un CPT dédié. Vérifier via wordpress_list_post_types.

    Limite : seules les deux taxonomies natives sont adressables ici, via
    `categories` et `tags`. Pour une taxonomie personnalisée attachée aux
    articles, passer par wordpress_create_custom_post(post_type="posts",
    taxonomies='{"<taxonomie>": [<ids>]}'), qui vérifie la taxonomie avant
    d'écrire.

    Args:
        title: titre de l'article
        content: contenu (markup Gutenberg de préférence)
        server: "prod" ou "test"
        subsite: "" (défaut, site principal) | clé d'un sous-site déclaré dans WP_<SERVER>_SUBSITES
        status: "draft" (défaut) | "publish" | "private" | "pending"
        slug: identifiant URL (généré automatiquement si vide)
        excerpt: chapeau / extrait
        categories: IDs de catégories séparés par des virgules (ex: "3,7")
        tags: IDs d'étiquettes séparés par des virgules
        featured_media: ID du média mis en avant (0 = aucun)
        date: date de publication ISO 8601 (ex: "2026-09-01T09:00:00")
        acf_fields: JSON des champs ACF
    """
    data: dict = {"title": title, "content": content, "status": status}
    if slug:
        data["slug"] = slug
    if excerpt:
        data["excerpt"] = excerpt
    if _parse_id_list(categories):
        data["categories"] = _parse_id_list(categories)
    if _parse_id_list(tags):
        data["tags"] = _parse_id_list(tags)
    if featured_media:
        data["featured_media"] = featured_media
    if date:
        data["date"] = date
    if acf_fields:
        try:
            data["acf"] = json.loads(acf_fields)
        except json.JSONDecodeError as e:
            return f"acf_fields JSON invalide : {e}"
    try:
        p = await _wp_post(server, "posts", data, subsite=subsite)
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
async def wordpress_delete_post(
    post_id: int,
    server: str = "prod",
    subsite: str = "",
    force: bool = False,
) -> str:
    """Met un article natif à la corbeille, ou le supprime définitivement.

    Par défaut l'article part à la corbeille : il reste restaurable depuis
    l'administration. `force=True` est irréversible et perd aussi les révisions.

    Args:
        post_id: ID de l'article
        server: "prod" ou "test"
        subsite: "" (défaut, site principal) | clé d'un sous-site déclaré dans WP_<SERVER>_SUBSITES
        force: True = suppression définitive, False = corbeille (défaut)
    """
    params = {"force": "true" if force else "false"}
    try:
        result = await _wp_delete(server, f"posts/{post_id}", params, subsite=subsite)
        return json.dumps({
            "deleted": True,
            "force": force,
            "id": post_id,
            "detail": result.get("previous", {}).get("title", {}).get("rendered", "") if isinstance(result, dict) else str(result)[:100],
        }, ensure_ascii=False, separators=(",", ":"))
    except Exception as exc:
        return _format_error(exc)


# ---------------------------------------------------------------------------
# Tools — Étiquettes & termes de taxonomie
# ---------------------------------------------------------------------------


@mcp.tool()
async def wordpress_list_tags(
    server: str = "prod",
    subsite: str = "",
    search: str = "",
    per_page: int = 100,
    hide_empty: bool = False,
) -> str:
    """Liste les termes de la taxonomie native `post_tag`.

    Pendant de wordpress_list_categories : sans lui, impossible de résoudre un
    nom d'étiquette en ID avant une écriture.

    Limite : cet outil n'adresse que la taxonomie native post_tag. Beaucoup de
    types personnalisés ne la déclarent pas et utilisent leur propre taxonomie
    d'étiquetage — leurs termes se listent avec wordpress_list_terms, les
    taxonomies d'un type se lisent dans wordpress_list_post_types.

    Args:
        server: "prod" ou "test"
        subsite: "" (défaut, site principal) | clé d'un sous-site déclaré dans WP_<SERVER>_SUBSITES
        search: filtre texte sur le nom
        per_page: limite
        hide_empty: True = masquer les étiquettes sans publication
    """
    params: dict = {
        "per_page": min(per_page, MAX_PER_PAGE),
        "hide_empty": "true" if hide_empty else "false",
        "_fields": "id,name,slug,count",
    }
    if search:
        params["search"] = search
    try:
        tags = await _wp_get(server, "tags", params, subsite=subsite)
        if not isinstance(tags, list):
            return f"Réponse inattendue : {tags}"
        return json.dumps({
            "server": server,
            "count": len(tags),
            "tags": [
                {"id": t.get("id"), "name": t.get("name", ""), "slug": t.get("slug", ""), "count": t.get("count", 0)}
                for t in tags
            ],
        }, ensure_ascii=False, separators=(",", ":"))
    except Exception as exc:
        return _format_error(exc)


@mcp.tool()
async def wordpress_list_terms(
    taxonomy: str,
    server: str = "prod",
    subsite: str = "",
    post_type: str = "",
    search: str = "",
    per_page: int = 100,
    page: int = 1,
    hide_empty: bool = False,
) -> str:
    """Liste les termes de n'importe quelle taxonomie, native ou personnalisée.

    Généralise wordpress_list_categories et wordpress_list_tags, qui ne savent
    lire que category et post_tag. C'est l'outil qui donne les IDs attendus par
    le paramètre `taxonomies` des écritures de CPT.

    La taxonomie est résolue avant lecture : elle est acceptée sous son nom
    (post_tag), son slug ou son rest_base (tags). Une taxonomie inconnue, ou
    non attachée au `post_type` demandé, renvoie une erreur qui liste celles
    qui existent.

    Limites non contournables : lecture seule (aucune création, modification ni
    suppression de terme), et seules les taxonomies déclarées avec show_in_rest
    sont visibles de l'API REST.

    Args:
        taxonomy: nom, slug ou rest_base de la taxonomie (ex: "post_tag", "tags", "blog-type")
        server: "prod" ou "test"
        subsite: "" (défaut, site principal) | clé d'un sous-site déclaré dans WP_<SERVER>_SUBSITES
        post_type: rest_base d'un type de contenu — restreint aux taxonomies de ce type et vérifie l'attachement
        search: filtre texte sur le nom
        per_page: limite (100 maximum)
        page: page de pagination
        hide_empty: True = masquer les termes sans publication
    """
    if post_type:
        known, reason = await _rest_taxonomies_for_type(server, post_type, subsite)
        if reason:
            return f"Découverte des taxonomies impossible : {reason}"
        scope = f"type {post_type}"
    else:
        try:
            raw = await _wp_get(server, "taxonomies", subsite=subsite)
        except Exception as exc:
            return _format_error(exc)
        if not isinstance(raw, dict):
            return f"Réponse inattendue : {raw}"
        known = _normalize_taxonomies(raw)
        scope = "site"

    name = _taxonomy_index(known).get(taxonomy)
    if not name:
        return _taxonomy_absent_message(
            taxonomy,
            scope,
            known,
            "Les taxonomies attachées à chaque type de contenu sont listées par "
            "wordpress_list_post_types.",
        )
    info = known[name]

    params: dict = {
        "per_page": min(per_page, MAX_PER_PAGE),
        "page": page,
        "hide_empty": "true" if hide_empty else "false",
        "_fields": "id,name,slug,parent,count",
    }
    if search:
        params["search"] = search
    try:
        terms = await _wp_get(server, info["rest_base"], params, subsite=subsite)
        if not isinstance(terms, list):
            return f"Réponse inattendue : {terms}"
        return json.dumps({
            "server": server,
            "taxonomy": name,
            "rest_base": info["rest_base"],
            "hierarchical": info["hierarchical"],
            "count": len(terms),
            "terms": [
                {
                    "id": t.get("id"),
                    "name": t.get("name", ""),
                    "slug": t.get("slug", ""),
                    "count": t.get("count", 0),
                    **({"parent": t.get("parent")} if t.get("parent") else {}),
                }
                for t in terms
            ],
        }, ensure_ascii=False, separators=(",", ":"))
    except Exception as exc:
        return _format_error(exc)


# ---------------------------------------------------------------------------
# Entrée principale
# ---------------------------------------------------------------------------

def main() -> None:
    """Point d'entree du serveur MCP (stdio)."""
    mcp.run()


if __name__ == "__main__":
    main()
