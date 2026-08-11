# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 inno³ and contributors
"""
Serveur MCP générique pour Nextcloud (WebDAV + OCS).

Auth  : HTTP Basic Auth avec Application Password Nextcloud.
APIs  : WebDAV (/remote.php/dav/files/{user}/) pour les fichiers,
        OCS (/ocs/v2.php/...) pour le partage et les informations utilisateur.
"""

import asyncio
import fnmatch
import json
import logging
import os
import re
import urllib.parse
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
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

# Pagination de nextcloud_list_files. Un PROPFIND sur un dossier de travail réel
# produisait ~123 000 caractères de JSON, au-delà de ce qu'une réponse d'outil
# peut porter : la réponse partait entière ou pas du tout, sans recours.
_DEFAULT_LIST_LIMIT = 100
_MAX_LIST_LIMIT = 1000

# Racine autorisée pour toute écriture locale (nextcloud_download_file
# save_path). Le chemin demandé est TOUJOURS traité comme relatif à cette
# racine : sans ce garde-fou, le serveur écrase n'importe quel fichier
# accessible à son utilisateur (~/.ssh/authorized_keys, un .env…) sur simple
# instruction d'un modèle.
_NC_DOWNLOAD_DIR = os.environ.get("NEXTCLOUD_DOWNLOAD_DIR") or os.path.join(
    os.path.expanduser("~"), "Downloads"
)
# Plafond par fichier écrit sur disque, distinct de _MAX_FILE_SIZE qui ne borne
# que ce qui remonte dans le contexte. Le corps de la réponse étant bufferisé,
# ce plafond borne aussi la RAM : d'où une valeur volontairement modeste.
_MAX_SAVE_BYTES = int(os.environ.get("NEXTCLOUD_MAX_SAVE_BYTES") or 25 * 1024 * 1024)

# Plafond serveur de l'Unified Search : Nextcloud ramène tout `limit` supérieur
# à cette valeur. Le demander explicitement permet de détecter la troncature
# (nombre d'entrées == plafond) au lieu de la subir en silence.
_SEARCH_LIMIT = 25

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

# Préfixe WebDAV porté par chaque href d'une réponse PROPFIND.
_HREF_ROOT_RE = re.compile(r"^.*?/remote\.php/dav/files/[^/]+")


def _href_to_path(decoded_href: str) -> str:
    """Convertit un href WebDAV en chemin utilisateur (« /Documents/x.pdf »).

    L'href brut répète `/remote.php/dav/files/<user>` sur *chaque* entrée : une
    constante d'une trentaine d'octets, inutile au modèle, et qui pèse autant de
    fois qu'il y a de fichiers dans le listing. Si le préfixe est absent (autre
    montage WebDAV), on renvoie l'href tel quel plutôt que de perdre l'info.
    """
    stripped = _HREF_ROOT_RE.sub("", decoded_href, count=1)
    if not stripped.startswith("/"):
        stripped = "/" + stripped
    return stripped.rstrip("/") or "/"


def _normalize_path(path: str) -> str:
    """Ramène un chemin utilisateur à la forme produite par _href_to_path."""
    return "/" + path.strip("/") if path.strip("/") else "/"


def _parse_rfc1123(value: str) -> datetime | None:
    """Parse une date HTTP (`Tue, 29 Jul 2026 08:12:33 GMT`), None si illisible.

    Indispensable pour trier ou comparer : ces chaînes ne sont PAS ordonnables
    lexicographiquement (« Fri » < « Tue », « 10 Jan » < « 2 Feb »).
    """
    if not value:
        return None
    try:
        parsed = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    if parsed is None:
        return None
    # Un getlastmodified sans fuseau reste possible sur des serveurs exotiques ;
    # WebDAV impose GMT, on l'assume pour rester comparable.
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _parse_iso_datetime(value: str) -> datetime:
    """Parse un filtre ISO (`2026-07-01` ou `2026-07-01T08:00:00Z`)."""
    raw = value.strip()
    if raw.endswith(("Z", "z")):
        raw = raw[:-1] + "+00:00"
    parsed = datetime.fromisoformat(raw)
    # Naïf → UTC, pour rester comparable aux dates WebDAV qui sont en GMT.
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


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

        is_dir = props.get("is_directory", False)
        entry = {
            "path": _href_to_path(decoded_href),
            "filename": filename,
            "is_directory": is_dir,
            "size": props.get("size", 0),
            "last_modified": props.get("last_modified", ""),
            "content_type": props.get("content_type", "httpd/unix-directory" if is_dir else ""),
        }
        # displayname est égal à filename dans la quasi-totalité des cas : on ne
        # le porte que lorsqu'il apporte réellement une information.
        displayname = props.get("displayname", "")
        if displayname and displayname != filename:
            entry["displayname"] = displayname
        results.append(entry)

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
# Helpers écriture locale
# ---------------------------------------------------------------------------


def _resolve_save_path(save_path: str) -> str:
    """Résout un chemin d'écriture DANS la racine autorisée, ou lève ValueError.

    Le chemin fourni est toujours traité comme relatif à _NC_DOWNLOAD_DIR : un
    chemin absolu ou remontant (`..`, symlink sortant) est refusé. Sans ce
    garde-fou, un simple save_path écrase n'importe quel fichier accessible au
    processus, sur instruction d'un modèle et à partir d'un nom de fichier
    Nextcloud qui n'est pas forcément de confiance.
    """
    raw = (save_path or "").strip()
    if not raw:
        raise ValueError("save_path vide")
    if os.path.isabs(raw) or raw.startswith("~"):
        raise ValueError(
            f"save_path doit être relatif à {_NC_DOWNLOAD_DIR} "
            "(chemin absolu refusé ; configurable via NEXTCLOUD_DOWNLOAD_DIR)"
        )
    if any(part == ".." for part in re.split(r"[\\/]+", raw)):
        raise ValueError("save_path ne peut pas contenir '..'")

    root = os.path.realpath(_NC_DOWNLOAD_DIR)
    target = os.path.realpath(os.path.join(root, raw))
    # realpath des deux côtés : couvre aussi un symlink déjà en place qui
    # pointerait hors de la racine.
    if target != root and not target.startswith(root + os.sep):
        raise ValueError("save_path sort de la racine autorisée")
    return target


def _write_local_file(target: str, payload: bytes, overwrite: bool) -> None:
    """Écrit le fichier de façon atomique, sans écraser par défaut."""
    if len(payload) > _MAX_SAVE_BYTES:
        raise ValueError(
            f"contenu de {len(payload)} octets — au-delà du plafond "
            f"{_MAX_SAVE_BYTES} (NEXTCLOUD_MAX_SAVE_BYTES)"
        )
    if os.path.exists(target) and not overwrite:
        raise ValueError(f"{target} existe déjà (passer overwrite=True pour remplacer)")
    parent = os.path.dirname(target)
    if parent:
        os.makedirs(parent, exist_ok=True)
    # Fichier temporaire puis rename : une interruption ne laisse jamais un
    # fichier tronqué qui passerait pour un téléchargement réussi.
    tmp = target + ".part"
    with open(tmp, "wb") as fh:
        fh.write(payload)
    os.replace(tmp, target)


def _parse_content_range_total(value: str) -> int | None:
    """Extrait la taille totale d'un en-tête `Content-Range: bytes 0-99/12345`.

    C'est la seule façon de connaître la taille réelle du fichier sans un
    aller-retour supplémentaire quand on n'en lit qu'une plage.
    """
    match = re.search(r"/(\d+)\s*$", value or "")
    return int(match.group(1)) if match else None


# ---------------------------------------------------------------------------
# Tools — Opérations sur les fichiers (WebDAV)
# ---------------------------------------------------------------------------


@mcp.tool()
async def nextcloud_list_files(
    path: str = "/",
    depth: int = 1,
    limit: int = _DEFAULT_LIST_LIMIT,
    offset: int = 0,
    glob: str = "",
    modified_since: str = "",
) -> str:
    """Liste les fichiers et dossiers d'un chemin Nextcloud (PROPFIND), paginé.

    Les entrées sont triées par date de modification DÉCROISSANTE (le plus
    récent d'abord), puis paginées.

    Params :
      path           (str) — chemin absolu du dossier (défaut : racine).
      depth          (int) — 1 = contenu du dossier, 0 = le dossier seul.
      limit          (int) — nombre maximum d'entrées renvoyées (défaut 100,
                             plafond 1000).
      offset         (int) — nombre d'entrées sautées, pour paginer.
      glob           (str) — motif fnmatch sur le NOM de fichier, insensible à
                             la casse (ex : `*.pdf`, `2026-*`).
      modified_since (str) — ne garder que les entrées modifiées à partir de
                             cette date ISO (`2026-07-01` ou
                             `2026-07-01T08:00:00Z`).

    Retourne `total` (après filtres, avant pagination), `returned`, `offset` et
    `has_more` : une liste tronquée est donc toujours signalée. Chaque entrée
    porte path, filename, is_directory, size, last_modified, content_type (plus
    displayname s'il diffère du nom de fichier).
    """
    try:
        if limit < 1:
            return _compact({"error": "limit doit être ≥ 1."})
        limit = min(limit, _MAX_LIST_LIMIT)
        offset = max(offset, 0)

        since: datetime | None = None
        if modified_since:
            try:
                since = _parse_iso_datetime(modified_since)
            except ValueError:
                return _compact({
                    "error": f"modified_since illisible : {modified_since!r} "
                             "(attendu ISO, ex. 2026-07-01 ou 2026-07-01T08:00:00Z)."
                })

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

        # Depth ≥ 1 : PROPFIND renvoie aussi le dossier interrogé lui-même. On
        # l'identifie par son chemin, et avant le tri — le retirer par position
        # après tri retirerait une entrée au hasard.
        if depth >= 1 and items:
            wanted = _normalize_path(path)
            filtered = [it for it in items if it.get("path") != wanted]
            # Repli positionnel si le préfixe WebDAV n'a pas pu être reconnu :
            # la convention veut que la collection soit la première réponse.
            items = filtered if len(filtered) < len(items) else items[1:]

        if glob:
            # fnmatchcase sur deux chaînes abaissées : fnmatch() suit la casse du
            # système de fichiers local, ce qui n'a aucun sens face à un serveur
            # distant et rendrait le résultat dépendant de l'OS du client.
            pattern = glob.lower()
            items = [it for it in items if fnmatch.fnmatchcase(it["filename"].lower(), pattern)]

        # Date parsée une seule fois : elle sert au filtre ET à la clé de tri.
        dated = [(it, _parse_rfc1123(it.get("last_modified", ""))) for it in items]
        if since is not None:
            dated = [(it, dt) for it, dt in dated if dt is not None and dt >= since]
        # Les dates illisibles partent en fin de liste plutôt que de faire
        # échouer la comparaison.
        oldest = datetime.min.replace(tzinfo=timezone.utc)
        dated.sort(key=lambda pair: (pair[1] is not None, pair[1] or oldest), reverse=True)
        items = [it for it, _ in dated]

        total = len(items)
        page = items[offset:offset + limit]
        return _compact({
            "path": path,
            "total": total,
            "returned": len(page),
            "offset": offset,
            "limit": limit,
            "has_more": offset + len(page) < total,
            "sort": "last_modified desc",
            "items": page,
        })
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
async def nextcloud_download_file(
    path: str,
    save_path: str = "",
    overwrite: bool = False,
    offset: int = 0,
    length: int = 0,
) -> str:
    """Télécharge un fichier Nextcloud, dans le contexte ou sur le disque local.

    Params :
      path      (str) — chemin absolu du fichier (ex: /Notes/todo.txt).
      save_path (str) — si fourni, écrit le fichier sur le disque au lieu de
                        renvoyer son contenu. Chemin obligatoirement RELATIF à
                        la racine de téléchargement (NEXTCLOUD_DOWNLOAD_DIR,
                        défaut ~/Downloads) : absolu, `..` et symlink sortant
                        sont refusés. Seule voie pour un binaire ou un gros
                        fichier (plafond 25 Mo, NEXTCLOUD_MAX_SAVE_BYTES).
      overwrite (bool) — autorise le remplacement d'un fichier existant
                        (défaut : False, l'écriture échoue).
      offset    (int) — position de départ en octets (lecture par plage).
      length    (int) — nombre d'octets à lire ; 0 = jusqu'au plafond.

    Sans save_path, le contenu doit être du texte UTF-8 et tenir dans 100 KB.
    La lecture est toujours bornée côté serveur via un en-tête Range, donc un
    fichier de plusieurs Go n'est jamais rapatrié en entier. `total_size`
    renseigne la taille réelle du fichier quand le serveur l'expose, ce qui
    permet d'enchaîner les plages.
    """
    try:
        if offset < 0 or length < 0:
            return _compact({"error": "offset et length doivent être ≥ 0."})

        # Le plafond dépend de la destination : le contexte est bien plus étroit
        # que le disque.
        cap = _MAX_SAVE_BYTES if save_path else _MAX_FILE_SIZE
        if length > cap:
            return _compact({
                "error": f"length demandé ({length} octets) au-delà du plafond {cap} octets."
            })

        # Un Range est envoyé même sans demande explicite : sans lui, le corps
        # entier arrive en mémoire avant tout contrôle de taille, et le plafond
        # ne protège alors que le contexte. On demande cap+1 octets pour
        # distinguer « pile à la limite » de « tronqué ».
        last = offset + (length - 1 if length else cap)
        try:
            resp = await _webdav(
                "GET", path, extra_headers={"Range": f"bytes={offset}-{last}"}, raw_response=True
            )
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code != 416:
                raise
            if offset > 0:
                return _compact({
                    "error": f"offset {offset} au-delà de la fin du fichier.",
                    "path": path,
                })
            # 416 à l'offset 0 = fichier vide (une plage sur 0 octet est toujours
            # insatisfiable), pas une erreur.
            resp = None

        payload = resp.content if resp is not None else b""
        size = len(payload)
        content_type = resp.headers.get("content-type", "") if resp is not None else ""
        total_size = (
            _parse_content_range_total(resp.headers.get("content-range", ""))
            if resp is not None
            else 0
        )
        # length explicite : la troncature est voulue. Sinon, cap+1 octets reçus
        # signifie que le fichier déborde du plafond.
        if not length and size > cap:
            return _compact({
                "error": f"Fichier trop volumineux (> {cap} octets)."
                         + ("" if save_path else " Utiliser save_path pour l'écrire sur disque,"
                                                 " ou offset/length pour n'en lire qu'une plage."),
                "size": total_size or size,
            })

        if save_path:
            try:
                target = _resolve_save_path(save_path)
                _write_local_file(target, payload, overwrite=overwrite)
            except ValueError as exc:
                return _compact({"error": str(exc), "download_root": _NC_DOWNLOAD_DIR})
            return _compact({
                "ok": True,
                "path": path,
                "saved_to": target,
                "bytes_written": size,
                "total_size": total_size or size,
                "content_type": content_type,
                "partial": bool(total_size and size < total_size),
            })

        lossy = False
        try:
            content = payload.decode("utf-8")
        except UnicodeDecodeError:
            if not (offset or length):
                return _compact({
                    "error": "Fichier binaire non supporté (impossible de décoder en UTF-8). "
                             "Utiliser save_path pour l'écrire sur disque.",
                    "size": total_size or size,
                    "content_type": content_type,
                })
            # Une plage coupe volontiers un caractère multi-octets à ses bords :
            # ce n'est pas un fichier binaire, seulement des bornes mal alignées.
            content = payload.decode("utf-8", errors="replace")
            lossy = True
        result = {
            "path": path,
            "size": size,
            "total_size": total_size or size,
            "content_type": content_type,
            "content": content,
        }
        if offset or length:
            result["offset"] = offset
            result["partial"] = bool(total_size and offset + size < total_size)
            if lossy:
                result["decode_warning"] = "plage coupée sur un caractère multi-octets"
        return _compact(result)
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


def _search_entry_path(entry: dict) -> str:
    """Chemin du fichier derrière une entrée Unified Search, "" si indéterminable."""
    attributes = entry.get("attributes") or {}
    if isinstance(attributes, dict) and attributes.get("path"):
        return _normalize_path(str(attributes["path"]))
    # Repli : le provider `files` met le chemin du dossier parent en subline.
    subline = entry.get("subline") or ""
    return _normalize_path(subline) if subline.startswith("/") else ""


@mcp.tool()
async def nextcloud_search(query: str, path: str = "/", cursor: str = "") -> str:
    """Recherche des fichiers par NOM DE FICHIER uniquement (Unified Search).

    ⚠️ Ce n'est PAS une recherche plein texte. Le provider `files` de l'Unified
    Search ne compare le terme qu'au NOM du fichier, comme une SOUS-CHAÎNE
    CONTIGUË insensible à la casse. Le contenu des fichiers n'est ni indexé ni
    lu, et le nom des dossiers parents n'entre pas dans la comparaison.
    Conséquence : un terme fléchi ou dérivé ne matche pas. « transcription » ne
    remonte AUCUN fichier de /MeetingRec_Transcripts/, parce que
    « transcription » n'est pas une sous-chaîne de « Transcripts ». Utiliser une
    racine plus courte (« transcript »), ou nextcloud_list_files avec un glob.

    Params :
      query  (str) — sous-chaîne à chercher dans les noms de fichiers.
      path   (str) — ne conserver que les résultats sous ce chemin. Le filtre
                     est appliqué côté client : il s'exerce sur la page de
                     résultats renvoyée, pas sur l'index.
      cursor (str) — curseur de pagination renvoyé par un appel précédent.

    Le serveur plafonne la page à 25 entrées : `truncated=true` signale que
    d'autres résultats existent et qu'il faut rappeler l'outil avec `cursor`.
    """
    try:
        params: dict = {"term": query, "limit": _SEARCH_LIMIT}
        if cursor:
            params["cursor"] = cursor
        response = await _request(
            "GET",
            f"{_NC_URL}/ocs/v2.php/search/providers/files/search",
            extra_headers={"OCS-APIRequest": "true"},
            params=params,
        )
        data = response.get("ocs", {}).get("data", {}) if isinstance(response, dict) else {}
        entries = data.get("entries") or []

        results = []
        prefix = _normalize_path(path) if path else "/"
        for entry in entries:
            entry_path = _search_entry_path(entry)
            if prefix != "/" and entry_path and not (
                entry_path == prefix or entry_path.startswith(prefix + "/")
            ):
                continue
            item = {"title": entry.get("title"), "resourceUrl": entry.get("resourceUrl")}
            if entry_path:
                item["path"] = entry_path
            else:
                # Pas de chemin exploitable : la subline reste le seul repère.
                item["subline"] = entry.get("subline")
            results.append(item)

        # La troncature se mesure sur ce que le SERVEUR a renvoyé, pas sur ce
        # qui survit au filtre de chemin : sinon une page entièrement filtrée
        # passerait pour une fin de résultats.
        truncated = len(entries) >= _SEARCH_LIMIT
        out: dict[str, Any] = {
            "query": query,
            "count": len(results),
            "server_entries": len(entries),
            "truncated": truncated,
            "match": "nom de fichier, sous-chaîne contiguë, sans indexation du contenu",
            "results": results,
        }
        next_cursor = data.get("cursor")
        if truncated and next_cursor:
            out["cursor"] = next_cursor
        if prefix != "/":
            out["path_filter"] = prefix
        return _compact(out)
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
