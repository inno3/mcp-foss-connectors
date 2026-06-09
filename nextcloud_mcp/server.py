# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 inno³ and contributors
"""
Serveur MCP générique pour Nextcloud (WebDAV + OCS).

Auth  : HTTP Basic Auth avec Application Password Nextcloud.
APIs  : WebDAV (/remote.php/dav/files/{user}/) pour les fichiers,
        OCS (/ocs/v2.php/...) pour le partage et les informations utilisateur.
"""

import asyncio
import json
import logging
import os
import urllib.parse
import xml.etree.ElementTree as ET
from typing import Any

import httpx
from mcp.server.fastmcp import FastMCP

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("nextcloud-mcp")

mcp = FastMCP("nextcloud")

_NC_URL: str = os.environ.get("NEXTCLOUD_URL", "").rstrip("/")
_NC_USER: str = os.environ.get("NEXTCLOUD_USER", "")
_NC_PASSWORD: str = os.environ.get("NEXTCLOUD_APP_PASSWORD", "")

_MAX_RETRIES = 2
_RETRY_DELAY = 1.0
_MAX_FILE_SIZE = 100 * 1024  # 100 KB pour le téléchargement de fichiers texte

# ---------------------------------------------------------------------------
# Helpers URL
# ---------------------------------------------------------------------------


def _webdav_url(path: str) -> str:
    """Construit l'URL WebDAV pour un chemin donné."""
    if not _NC_URL or not _NC_USER:
        raise ValueError("NEXTCLOUD_URL et NEXTCLOUD_USER doivent être configurés.")
    clean = path.lstrip("/")
    encoded = "/".join(urllib.parse.quote(seg, safe="") for seg in clean.split("/")) if clean else ""
    base = f"{_NC_URL}/remote.php/dav/files/{urllib.parse.quote(_NC_USER, safe='')}"
    return f"{base}/{encoded}" if encoded else base + "/"


def _ocs_url(endpoint: str) -> str:
    """Construit l'URL OCS pour un endpoint donné."""
    if not _NC_URL:
        raise ValueError("NEXTCLOUD_URL doit être configuré.")
    return f"{_NC_URL}/ocs/v2.php/{endpoint.lstrip('/')}"


def _destination_header(path: str) -> str:
    """Retourne le header Destination complet pour MOVE/COPY."""
    return _webdav_url(path)


# ---------------------------------------------------------------------------
# Helpers HTTP
# ---------------------------------------------------------------------------


def _auth() -> tuple[str, str]:
    """Retourne le tuple (user, password) pour httpx Basic Auth."""
    if not _NC_USER or not _NC_PASSWORD:
        raise ValueError("NEXTCLOUD_USER et NEXTCLOUD_APP_PASSWORD doivent être configurés.")
    return (_NC_USER, _NC_PASSWORD)


async def _request(
    method: str,
    url: str,
    extra_headers: dict | None = None,
    raw_response: bool = False,
    **kwargs: Any,
) -> Any:
    """Appel HTTP avec retry et backoff exponentiel."""
    headers = {
        "Accept": "application/json",
    }
    if extra_headers:
        headers.update(extra_headers)

    last_exc: Exception | None = None
    for attempt in range(_MAX_RETRIES + 1):
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                logger.info("%s %s (tentative %d)", method, url, attempt + 1)
                resp = await client.request(
                    method, url, headers=headers, auth=_auth(), **kwargs
                )
                resp.raise_for_status()
                if raw_response:
                    return resp
                if resp.content:
                    ct = resp.headers.get("content-type", "")
                    if "json" in ct:
                        return resp.json()
                    if "xml" in ct or ct.startswith("application/xml") or ct.startswith("text/xml"):
                        return resp.text
                    # Réponse binaire ou texte brut
                    return resp.content
                return {}
        except (httpx.ConnectError, httpx.ReadTimeout) as exc:
            last_exc = exc
            if attempt < _MAX_RETRIES:
                delay = _RETRY_DELAY * (2**attempt)
                logger.warning("Retry %s dans %.1fs : %s", url, delay, exc)
                await asyncio.sleep(delay)
            else:
                raise
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code >= 500 and attempt < _MAX_RETRIES:
                last_exc = exc
                delay = _RETRY_DELAY * (2**attempt)
                logger.warning("Retry %s (HTTP %d) dans %.1fs", url, exc.response.status_code, delay)
                await asyncio.sleep(delay)
            else:
                raise
    raise last_exc  # type: ignore[misc]


async def _webdav(
    method: str,
    path: str,
    extra_headers: dict | None = None,
    raw_response: bool = False,
    **kwargs: Any,
) -> Any:
    """Appel WebDAV."""
    url = _webdav_url(path)
    return await _request(method, url, extra_headers=extra_headers, raw_response=raw_response, **kwargs)


async def _ocs_get(endpoint: str, params: dict | None = None) -> Any:
    """GET sur l'API OCS."""
    return await _request(
        "GET",
        _ocs_url(endpoint),
        extra_headers={"OCS-APIRequest": "true"},
        params=params or {},
    )


async def _ocs_post(endpoint: str, data: dict | None = None) -> Any:
    """POST sur l'API OCS."""
    return await _request(
        "POST",
        _ocs_url(endpoint),
        extra_headers={"OCS-APIRequest": "true"},
        data=data or {},
    )


async def _ocs_delete(endpoint: str) -> Any:
    """DELETE sur l'API OCS."""
    return await _request(
        "DELETE",
        _ocs_url(endpoint),
        extra_headers={"OCS-APIRequest": "true"},
    )


# ---------------------------------------------------------------------------
# Parseurs WebDAV XML
# ---------------------------------------------------------------------------

_DAV_NS = "DAV:"
_D = f"{{{_DAV_NS}}}"  # Préfixe namespace DAV


def _parse_propfind(xml_text: str) -> list[dict]:
    """Parse une réponse PROPFIND et retourne une liste de ressources."""
    results = []
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        logger.error("Erreur parsing XML WebDAV : %s", exc)
        return results

    for response in root.findall(f"{_D}response"):
        href_el = response.find(f"{_D}href")
        if href_el is None:
            continue
        href = href_el.text or ""

        # On cherche le propstat avec status 200
        props: dict[str, Any] = {}
        for propstat in response.findall(f"{_D}propstat"):
            status_el = propstat.find(f"{_D}status")
            if status_el is not None and "200 OK" not in (status_el.text or ""):
                continue
            prop_el = propstat.find(f"{_D}prop")
            if prop_el is None:
                continue
            # Nom du fichier / dossier
            display_el = prop_el.find(f"{_D}displayname")
            if display_el is not None:
                props["displayname"] = display_el.text or ""
            # Type de contenu
            ct_el = prop_el.find(f"{_D}getcontenttype")
            if ct_el is not None:
                props["content_type"] = ct_el.text or ""
            # Taille
            size_el = prop_el.find(f"{_D}getcontentlength")
            if size_el is not None:
                try:
                    props["size"] = int(size_el.text or 0)
                except ValueError:
                    props["size"] = 0
            # Date de modification
            mod_el = prop_el.find(f"{_D}getlastmodified")
            if mod_el is not None:
                props["last_modified"] = mod_el.text or ""
            # Type de ressource (dossier ?)
            restype_el = prop_el.find(f"{_D}resourcetype")
            if restype_el is not None:
                props["is_directory"] = restype_el.find(f"{_D}collection") is not None
            else:
                props["is_directory"] = False

        # Décode le path depuis href
        decoded_href = urllib.parse.unquote(href)
        # Nom du fichier = dernier segment du href (hors slash final)
        segments = decoded_href.rstrip("/").split("/")
        filename = segments[-1] if segments else decoded_href

        results.append({
            "href": decoded_href,
            "filename": filename,
            "displayname": props.get("displayname", filename),
            "is_directory": props.get("is_directory", False),
            "size": props.get("size", 0),
            "last_modified": props.get("last_modified", ""),
            "content_type": props.get("content_type", "httpd/unix-directory" if props.get("is_directory") else ""),
        })

    return results


def _format_error(exc: Exception) -> str:
    """Formate un message d'erreur lisible."""
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        try:
            body = exc.response.json()
            msg = body.get("message") or body.get("error") or str(body)[:200]
        except Exception:
            msg = exc.response.text[:200]
        return f"Erreur HTTP {status} : {msg}"
    return f"Erreur : {exc}"


def _compact(obj: Any) -> str:
    """Sérialise en JSON compact."""
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))


def _extract_ocs_data(response: Any) -> Any:
    """Extrait les données utiles d'une réponse OCS (élimine l'enveloppe ocs/data)."""
    if isinstance(response, dict):
        return response.get("ocs", {}).get("data", response)
    return response


# ---------------------------------------------------------------------------
# Tools — Opérations sur les fichiers (WebDAV)
# ---------------------------------------------------------------------------


@mcp.tool()
async def nextcloud_list_files(path: str = "/", depth: int = 1) -> str:
    """Liste les fichiers et dossiers d'un chemin Nextcloud (PROPFIND).

    Retourne pour chaque élément : filename, is_directory, size, last_modified, content_type.
    depth=1 liste le contenu du dossier, depth=0 retourne seulement le dossier lui-même.
    """
    try:
        xml_text = await _webdav(
            "PROPFIND",
            path,
            extra_headers={
                "Depth": str(depth),
                "Content-Type": "application/xml",
            },
            content=b"""<?xml version="1.0" encoding="UTF-8"?>
<d:propfind xmlns:d="DAV:">
  <d:prop>
    <d:displayname/>
    <d:getcontenttype/>
    <d:getcontentlength/>
    <d:getlastmodified/>
    <d:resourcetype/>
  </d:prop>
</d:propfind>""",
        )
        if not isinstance(xml_text, str):
            xml_text = xml_text.decode("utf-8") if isinstance(xml_text, bytes) else str(xml_text)
        items = _parse_propfind(xml_text)
        # Si depth=1, exclure l'entrée du dossier racine (premier élément = le dossier lui-même)
        if depth >= 1 and len(items) > 1:
            items = items[1:]
        return _compact({"path": path, "count": len(items), "items": items})
    except Exception as exc:
        return _compact({"error": _format_error(exc)})


@mcp.tool()
async def nextcloud_get_file_info(path: str) -> str:
    """Retourne les métadonnées d'un fichier ou dossier (PROPFIND depth=0).

    Params : path (str) — chemin absolu dans Nextcloud (ex: /Documents/rapport.pdf).
    """
    try:
        xml_text = await _webdav(
            "PROPFIND",
            path,
            extra_headers={
                "Depth": "0",
                "Content-Type": "application/xml",
            },
            content=b"""<?xml version="1.0" encoding="UTF-8"?>
<d:propfind xmlns:d="DAV:">
  <d:prop>
    <d:displayname/>
    <d:getcontenttype/>
    <d:getcontentlength/>
    <d:getlastmodified/>
    <d:resourcetype/>
  </d:prop>
</d:propfind>""",
        )
        if not isinstance(xml_text, str):
            xml_text = xml_text.decode("utf-8") if isinstance(xml_text, bytes) else str(xml_text)
        items = _parse_propfind(xml_text)
        if not items:
            return _compact({"error": "Ressource introuvable ou réponse vide."})
        return _compact(items[0])
    except Exception as exc:
        return _compact({"error": _format_error(exc)})


@mcp.tool()
async def nextcloud_download_file(path: str) -> str:
    """Télécharge le contenu d'un fichier texte (≤100 KB) depuis Nextcloud.

    Params : path (str) — chemin absolu du fichier (ex: /Notes/todo.txt).
    Ne convient pas aux fichiers binaires (images, PDFs, etc.).
    """
    try:
        resp = await _webdav("GET", path, raw_response=True)
        # raw_response=True → resp est un httpx.Response
        size = len(resp.content)
        if size > _MAX_FILE_SIZE:
            return _compact({
                "error": f"Fichier trop volumineux ({size} octets). Limite : {_MAX_FILE_SIZE} octets (100 KB).",
                "size": size,
            })
        # Tenter de décoder en texte
        try:
            content = resp.content.decode("utf-8")
        except UnicodeDecodeError:
            return _compact({
                "error": "Fichier binaire non supporté (impossible de décoder en UTF-8).",
                "size": size,
                "content_type": resp.headers.get("content-type", ""),
            })
        return _compact({
            "path": path,
            "size": size,
            "content_type": resp.headers.get("content-type", ""),
            "content": content,
        })
    except Exception as exc:
        return _compact({"error": _format_error(exc)})


@mcp.tool()
async def nextcloud_upload_file(path: str, content: str) -> str:
    """Crée ou remplace un fichier dans Nextcloud avec le contenu fourni.

    Params :
      path    (str) — chemin absolu cible (ex: /Notes/todo.txt).
      content (str) — contenu textuel du fichier.
    """
    try:
        encoded = content.encode("utf-8")
        await _webdav(
            "PUT",
            path,
            extra_headers={"Content-Type": "application/octet-stream"},
            content=encoded,
        )
        return _compact({"ok": True, "path": path, "size": len(encoded)})
    except Exception as exc:
        return _compact({"error": _format_error(exc)})


@mcp.tool()
async def nextcloud_create_folder(path: str) -> str:
    """Crée un dossier dans Nextcloud (MKCOL).

    Params : path (str) — chemin absolu du dossier à créer (ex: /Projets/nouveau-dossier).
    Les dossiers parents doivent exister.
    """
    try:
        await _webdav("MKCOL", path)
        return _compact({"ok": True, "path": path})
    except Exception as exc:
        return _compact({"error": _format_error(exc)})


@mcp.tool()
async def nextcloud_delete(path: str) -> str:
    """Supprime un fichier ou un dossier dans Nextcloud (DELETE).

    Params : path (str) — chemin absolu de la ressource à supprimer.
    Attention : la suppression d'un dossier est récursive.
    """
    try:
        await _webdav("DELETE", path)
        return _compact({"ok": True, "path": path, "deleted": True})
    except Exception as exc:
        return _compact({"error": _format_error(exc)})


@mcp.tool()
async def nextcloud_move(source_path: str, destination_path: str) -> str:
    """Déplace ou renomme un fichier/dossier dans Nextcloud (MOVE).

    Params :
      source_path      (str) — chemin source absolu.
      destination_path (str) — chemin destination absolu.
    """
    try:
        await _webdav(
            "MOVE",
            source_path,
            extra_headers={"Destination": _destination_header(destination_path), "Overwrite": "T"},
        )
        return _compact({"ok": True, "from": source_path, "to": destination_path})
    except Exception as exc:
        return _compact({"error": _format_error(exc)})


@mcp.tool()
async def nextcloud_copy(source_path: str, destination_path: str) -> str:
    """Copie un fichier ou dossier dans Nextcloud (COPY).

    Params :
      source_path      (str) — chemin source absolu.
      destination_path (str) — chemin destination absolu.
    """
    try:
        await _webdav(
            "COPY",
            source_path,
            extra_headers={"Destination": _destination_header(destination_path), "Overwrite": "T"},
        )
        return _compact({"ok": True, "from": source_path, "to": destination_path})
    except Exception as exc:
        return _compact({"error": _format_error(exc)})


# ---------------------------------------------------------------------------
# Tools — Partage (OCS API)
# ---------------------------------------------------------------------------


@mcp.tool()
async def nextcloud_list_shares(path: str = "") -> str:
    """Liste les partages Nextcloud, avec filtre optionnel par chemin.

    Params :
      path (str, optionnel) — filtrer les partages sur ce chemin précis.

    Retourne pour chaque partage : id, share_type, share_with, path, permissions, url.
    Types de partage : 0=utilisateur, 1=groupe, 3=lien public, 4=e-mail, 6=dépôt fédéré.
    """
    try:
        params: dict = {}
        if path:
            params["path"] = path
        response = await _ocs_get("apps/files_sharing/api/v1/shares", params=params)
        data = _extract_ocs_data(response)
        shares = data if isinstance(data, list) else []
        formatted = [
            {
                "id": s.get("id"),
                "share_type": s.get("share_type"),
                "share_with": s.get("share_with"),
                "share_with_displayname": s.get("share_with_displayname"),
                "path": s.get("path"),
                "permissions": s.get("permissions"),
                "url": s.get("url"),
                "token": s.get("token"),
                "expiration": s.get("expiration"),
            }
            for s in shares
        ]
        return _compact({"count": len(formatted), "shares": formatted})
    except Exception as exc:
        return _compact({"error": _format_error(exc)})


@mcp.tool()
async def nextcloud_create_share(
    path: str,
    share_type: int,
    share_with: str = "",
    permissions: int = 1,
    password: str = "",
) -> str:
    """Crée un partage Nextcloud.

    Params :
      path        (str) — chemin absolu de la ressource à partager.
      share_type  (int) — 0=utilisateur, 1=groupe, 3=lien public.
      share_with  (str) — identifiant de l'utilisateur/groupe (ignoré pour lien public).
      permissions (int) — permissions : 1=lecture, 17=lecture+partage, 31=tout (défaut: 1).
      password    (str) — mot de passe pour les liens publics (optionnel).

    Retourne l'id, le token et l'URL du partage créé.
    """
    try:
        data: dict[str, Any] = {
            "path": path,
            "shareType": share_type,
            "permissions": permissions,
        }
        if share_with:
            data["shareWith"] = share_with
        if password:
            data["password"] = password
        response = await _ocs_post("apps/files_sharing/api/v1/shares", data=data)
        share = _extract_ocs_data(response)
        return _compact({
            "ok": True,
            "id": share.get("id"),
            "token": share.get("token"),
            "url": share.get("url"),
            "path": share.get("path"),
            "share_type": share.get("share_type"),
            "permissions": share.get("permissions"),
        })
    except Exception as exc:
        return _compact({"error": _format_error(exc)})


@mcp.tool()
async def nextcloud_delete_share(share_id: int) -> str:
    """Supprime un partage Nextcloud par son identifiant.

    Params :
      share_id (int) — identifiant numérique du partage (obtenu via nextcloud_list_shares).
    """
    try:
        await _ocs_delete(f"apps/files_sharing/api/v1/shares/{share_id}")
        return _compact({"ok": True, "share_id": share_id, "deleted": True})
    except Exception as exc:
        return _compact({"error": _format_error(exc)})


# ---------------------------------------------------------------------------
# Tools — Recherche et statut
# ---------------------------------------------------------------------------


@mcp.tool()
async def nextcloud_search(query: str, path: str = "/") -> str:
    """Recherche des fichiers dans Nextcloud par nom ou contenu.

    Params :
      query (str) — terme de recherche.
      path  (str) — restreindre la recherche à ce chemin (défaut : racine).

    Utilise le moteur de recherche OCS de Nextcloud (Full-Text Search si activé).
    """
    try:
        params: dict = {"term": query, "limit": 50}
        if path and path != "/":
            params["path"] = path
        response = await _request(
            "GET",
            f"{_NC_URL}/ocs/v2.php/search/providers/files/search",
            extra_headers={"OCS-APIRequest": "true"},
            params=params,
        )
        if isinstance(response, dict):
            data = response.get("ocs", {}).get("data", {})
            entries = data.get("entries", [])
        else:
            entries = []
        results = [
            {
                "title": e.get("title"),
                "subline": e.get("subline"),
                "resourceUrl": e.get("resourceUrl"),
                "thumbnailUrl": e.get("thumbnailUrl"),
                "attributes": e.get("attributes", {}),
            }
            for e in entries
        ]
        return _compact({"query": query, "count": len(results), "results": results})
    except Exception as exc:
        return _compact({"error": _format_error(exc)})


@mcp.tool()
async def nextcloud_status() -> str:
    """Retourne les informations de statut et la version du serveur Nextcloud.

    Aucun paramètre requis. Utile pour vérifier la connectivité et la version.
    """
    try:
        response = await _request("GET", f"{_NC_URL}/status.php")
        if isinstance(response, (bytes, bytearray)):
            import json as _json
            response = _json.loads(response.decode("utf-8"))
        return _compact({
            "installed": response.get("installed"),
            "maintenance": response.get("maintenance"),
            "needsDbUpgrade": response.get("needsDbUpgrade"),
            "version": response.get("version"),
            "versionstring": response.get("versionstring"),
            "edition": response.get("edition"),
            "productname": response.get("productname"),
        })
    except Exception as exc:
        return _compact({"error": _format_error(exc)})


@mcp.tool()
async def nextcloud_user_info() -> str:
    """Retourne les informations de l'utilisateur Nextcloud connecté.

    Aucun paramètre requis. Retourne : id, displayname, email, quota, groupes.
    """
    try:
        response = await _ocs_get("cloud/user")
        data = _extract_ocs_data(response)
        quota = data.get("quota", {})
        return _compact({
            "id": data.get("id"),
            "displayname": data.get("displayname"),
            "email": data.get("email"),
            "language": data.get("language"),
            "groups": data.get("groups", []),
            "quota": {
                "free": quota.get("free"),
                "used": quota.get("used"),
                "total": quota.get("total"),
                "relative": quota.get("relative"),
                "quota": quota.get("quota"),
            },
            "enabled": data.get("enabled"),
        })
    except Exception as exc:
        return _compact({"error": _format_error(exc)})


# ---------------------------------------------------------------------------
# Point d'entrée
# ---------------------------------------------------------------------------

def main() -> None:
    """Point d'entree du serveur MCP (stdio)."""
    mcp.run()


if __name__ == "__main__":
    main()
