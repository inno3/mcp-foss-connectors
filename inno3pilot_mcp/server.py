# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 inno³ and contributors
"""
Serveur MCP pour inno3pilot (boards Kanban polymorphes dans Dolibarr).

Expose l'API REST /inno3pilotapi/ du module Dolibarr inno3pilot : boards,
colonnes, cartes (résumés résolus par type), déplacement de carte avec
synchronisation du statut natif (gardée anti-boucle côté module).

Configuration : DOLIBARR_URL (URL de base) + DOLIBARR_API_KEY (header DOLAPIKEY)
— les mêmes variables que le connecteur dolibarr_mcp.

NB : les commentaires de cartes et le partage client passent par des endpoints
de session (AJAX), pas par l'API REST — hors périmètre de ce connecteur.
"""

import asyncio
import json
import logging
import os
from typing import Any

import httpx
from mcp.server.fastmcp import FastMCP

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("inno3pilot-mcp")

mcp = FastMCP("inno3pilot")

DOLIBARR_URL = os.environ.get("DOLIBARR_URL", "")
API_KEY = os.environ.get("DOLIBARR_API_KEY", "")

HEADERS = {
    "DOLAPIKEY": API_KEY,
    "Accept": "application/json",
}

DEFAULT_LIMIT = 50
MAX_LIMIT = 300

_MAX_RETRIES = 2
_RETRY_DELAY = 1.0


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _dumps(data: Any) -> str:
    """JSON compact UTF-8 (les accents restent lisibles)."""
    return json.dumps(data, ensure_ascii=False, default=str)


def _clamp_limit(limit: int) -> int:
    """Borne une limite de liste dans [1, MAX_LIMIT]."""
    try:
        limit = int(limit)
    except (TypeError, ValueError):
        return DEFAULT_LIMIT
    if limit < 1:
        return DEFAULT_LIMIT
    return min(limit, MAX_LIMIT)


def _card_summary(card: dict) -> dict:
    """Réduit une carte de l'API à l'essentiel (économie de tokens).

    L'API renvoie {card:{...}, summary:{...}} ; on aplatit en gardant les
    champs utiles au pilotage.
    """
    c = card.get("card", {}) or {}
    s = card.get("summary", {}) or {}
    return {
        "card_id": c.get("rowid"),
        "fk_column": c.get("fk_column"),
        "position": c.get("position"),
        "color": c.get("color"),
        "elementtype": c.get("elementtype"),
        "fk_element": c.get("fk_element"),
        "ref": s.get("ref"),
        "title": s.get("title"),
        "project": s.get("project_title"),
        "assignee": s.get("assignee"),
        "status": s.get("native_status_label"),
        "due": s.get("date_due"),
        "overdue": s.get("overdue"),
        "priority": s.get("priority"),
    }


async def _request(method: str, path: str, payload: dict | None = None) -> Any:
    """Appel API REST inno3pilotapi avec petites relances réseau."""
    if not DOLIBARR_URL or not API_KEY:
        raise RuntimeError("DOLIBARR_URL / DOLIBARR_API_KEY non configurés")
    url = DOLIBARR_URL.rstrip("/") + "/api/index.php/inno3pilotapi" + path
    last_exc: Exception | None = None
    for attempt in range(_MAX_RETRIES + 1):
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.request(method, url, headers=HEADERS, json=payload)
            if resp.status_code >= 400:
                detail = resp.text[:300]
                raise RuntimeError(f"HTTP {resp.status_code} sur {path} : {detail}")
            if not resp.content:
                return None
            return resp.json()
        except (httpx.TransportError, httpx.TimeoutException) as exc:  # réseau : on retente
            last_exc = exc
            if attempt < _MAX_RETRIES:
                await asyncio.sleep(_RETRY_DELAY * (attempt + 1))
    raise RuntimeError(f"Réseau KO après {_MAX_RETRIES + 1} tentatives : {last_exc}")


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------

@mcp.tool()
async def inno3pilot_list_boards(fk_projet: int = 0) -> str:
    """Liste les boards inno3pilot (tous, ou ceux d'un projet).

    Paramètres :
    - fk_projet : 0 = tous les boards de l'entité ; sinon les boards du projet.

    Retourne : rowid, label, board_type (projet|perso|equipe), fk_projet, active.
    """
    path = "/boards" + (f"?fk_projet={int(fk_projet)}" if fk_projet else "")
    return _dumps(await _request("GET", path))


@mcp.tool()
async def inno3pilot_get_board(board_id: int) -> str:
    """Détail d'un board : colonnes (rowid, code, label, position, wip_limit)
    et types d'objets activés (project_task, ticket, meetingnotes…).
    """
    return _dumps(await _request("GET", f"/boards/{int(board_id)}"))


@mcp.tool()
async def inno3pilot_get_cards(board_id: int, limit: int = DEFAULT_LIMIT) -> str:
    """Cartes d'un board avec résumé résolu par type (réf, titre, projet,
    assigné, statut natif, échéance, priorité).

    Paramètres :
    - board_id : identifiant du board (cf. inno3pilot_list_boards)
    - limit : nombre max de cartes retournées (défaut 50, max 300). Un champ
      "truncated" indique si des cartes ont été coupées.
    """
    limit = _clamp_limit(limit)
    data = await _request("GET", f"/boards/{int(board_id)}/cards")
    cards = [_card_summary(c) for c in (data or [])]
    out = {"count": len(cards), "truncated": len(cards) > limit, "cards": cards[:limit]}
    return _dumps(out)


@mcp.tool()
async def inno3pilot_move_card(card_id: int, fk_column: int, position: int = 0) -> str:
    """Déplace une carte vers une colonne ET synchronise le statut natif de
    l'objet (mapping colonne↔statut du board, gardé anti-boucle par le module).

    ⚠ Déplacer vers la colonne « Terminé » applique le statut natif terminé
    (tâche → 100 %, ticket → résolu…). Confirmation utilisateur recommandée.

    Paramètres :
    - card_id : identifiant card_state (cf. inno3pilot_get_cards, champ card_id)
    - fk_column : colonne cible (rowid, cf. inno3pilot_get_board)
    - position : position dans la colonne (0 = inchangée)
    """
    payload: dict[str, Any] = {"fk_column": int(fk_column)}
    if position:
        payload["position"] = int(position)
    return _dumps(await _request("PUT", f"/cards/{int(card_id)}/move", payload))


@mcp.tool()
async def inno3pilot_add_card(board_id: int, elementtype: str, fk_element: int,
                              fk_column: int = 0) -> str:
    """Ajoute (ou repositionne) une carte référençant un objet natif existant.

    Paramètres :
    - board_id : board cible
    - elementtype : project_task | ticket | meetingnotes
    - fk_element : rowid de l'objet natif
    - fk_column : colonne cible (0 = colonne par défaut du type)
    """
    payload: dict[str, Any] = {
        "elementtype": str(elementtype),
        "fk_element": int(fk_element),
    }
    if fk_column:
        payload["fk_column"] = int(fk_column)
    return _dumps(await _request("POST", f"/boards/{int(board_id)}/cards", payload))


@mcp.tool()
async def inno3pilot_add_column(board_id: int, code: str, label: str,
                                position: int = 0, wip_limit: int = 0) -> str:
    """Ajoute une colonne à un board (elle devient utilisable pour les tâches :
    son code est câblé au champ d'état i3pstate par le module).

    Paramètres :
    - board_id : board cible
    - code : code technique (minuscules sans espace, ex. "validation")
    - label : libellé affiché
    - position : ordre (0 = fin)
    - wip_limit : limite WIP (0 = aucune)
    """
    payload = {
        "code": str(code),
        "label": str(label),
        "position": int(position),
        "wip_limit": int(wip_limit),
    }
    return _dumps(await _request("POST", f"/boards/{int(board_id)}/columns", payload))


# ---------------------------------------------------------------------------
# Entrée
# ---------------------------------------------------------------------------

def main() -> None:
    """Point d'entrée (convention du repo : chaque connecteur expose main())."""
    if not DOLIBARR_URL or not API_KEY:
        logger.warning("DOLIBARR_URL / DOLIBARR_API_KEY absents : les tools échoueront.")
    mcp.run()


if __name__ == "__main__":
    main()
