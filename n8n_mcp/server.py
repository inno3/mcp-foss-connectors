# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 inno³ and contributors
"""
Serveur MCP générique pour n8n.

Expose les workflows n8n via le protocole MCP (Claude Desktop et autres clients) :
liste, activation, exécutions, debug, mise à jour du code des nœuds.

Configuration : N8N_BASE_URL (URL de base) + N8N_API_KEY (header X-N8N-API-KEY).
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
logger = logging.getLogger("n8n-mcp")

mcp = FastMCP("n8n")

N8N_BASE_URL = os.environ.get("N8N_BASE_URL", "")
API_KEY = os.environ.get("N8N_API_KEY", "")

HEADERS = {
    "X-N8N-API-KEY": API_KEY,
    "Accept": "application/json",
    "Content-Type": "application/json",
}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_MAX_RETRIES = 2
_RETRY_DELAY = 1.0  # seconds, doubles each retry

# Champs autorisés dans un PUT /api/v1/workflows/{id}
# Les champs id, versionId, tags, updatedAt, createdAt, shared, active sont read-only
_WORKFLOW_PUT_ALLOWED = {"name", "nodes", "connections", "settings", "staticData", "pinData"}
# Note: "meta" exclu — l'API n8n le considère read-only même quand non-null

# Dans settings, seuls ces champs sont acceptés par PUT (binaryMode, availableInMCP sont GET-only)
_SETTINGS_ALLOWED = {
    "executionOrder", "saveManualExecutions",
    "errorWorkflow", "timezone", "saveDataSuccessExecution",
    "saveDataErrorExecution", "saveExecutionProgress", "executionTimeout",
}


async def _api_request(method: str, path: str, **kwargs: Any) -> Any:
    """Appel HTTP n8n avec retry et backoff exponentiel."""
    url = f"{N8N_BASE_URL}/api/v1/{path}"
    last_exc: Exception | None = None
    for attempt in range(_MAX_RETRIES + 1):
        try:
            async with httpx.AsyncClient(verify=True, timeout=30) as client:
                logger.info("%s %s (attempt %d)", method, url, attempt + 1)
                resp = await client.request(method, url, headers=HEADERS, **kwargs)
                resp.raise_for_status()
                # Certains endpoints retournent un body vide (204)
                if resp.status_code == 204 or not resp.content:
                    return {}
                return resp.json()
        except (httpx.ConnectError, httpx.ReadTimeout, httpx.WriteTimeout) as exc:
            last_exc = exc
            if attempt < _MAX_RETRIES:
                delay = _RETRY_DELAY * (2 ** attempt)
                logger.warning("Retry %s %s in %.1fs: %s", method, url, delay, exc)
                await asyncio.sleep(delay)
            else:
                raise
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code >= 500 and attempt < _MAX_RETRIES:
                last_exc = exc
                delay = _RETRY_DELAY * (2 ** attempt)
                logger.warning("Retry %s %s (HTTP %d) in %.1fs", method, url, exc.response.status_code, delay)
                await asyncio.sleep(delay)
            else:
                raise
    raise last_exc  # type: ignore[misc]


async def api_get(path: str, params: dict[str, Any] | None = None) -> Any:
    """Appel GET à l'API n8n avec retry."""
    return await _api_request("GET", path, params=params or {})


async def api_post(path: str, data: dict[str, Any] | None = None) -> Any:
    """Appel POST à l'API n8n avec retry."""
    return await _api_request("POST", path, json=data or {})


async def api_put(path: str, data: dict[str, Any] | None = None) -> Any:
    """Appel PUT à l'API n8n avec retry."""
    return await _api_request("PUT", path, json=data or {})


async def api_patch(path: str, data: dict[str, Any] | None = None) -> Any:
    """Appel PATCH à l'API n8n avec retry."""
    return await _api_request("PATCH", path, json=data or {})


async def api_delete(path: str) -> Any:
    """Appel DELETE à l'API n8n avec retry."""
    return await _api_request("DELETE", path)


def _format_error(exc: Exception) -> str:
    """Formate une erreur HTTP en message lisible."""
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        if status == 401:
            return "Erreur 401 : clé API n8n invalide ou manquante."
        if status == 403:
            return "Erreur 403 : permissions insuffisantes pour cette ressource."
        if status == 404:
            return "Erreur 404 : ressource non trouvée dans n8n."
        if status == 400:
            return f"Erreur 400 : requête invalide. Détail : {exc.response.text[:500]}"
        if status == 500:
            return f"Erreur 500 : erreur interne n8n. Détail : {exc.response.text[:500]}"
        return f"Erreur HTTP {status} : {exc.response.text[:500]}"
    return f"Erreur : {exc}"


def _dumps(data: Any) -> str:
    """Sérialise en JSON compact avec support UTF-8."""
    return json.dumps(data, ensure_ascii=False, separators=(",", ":"), default=str)


def _filter_workflow_for_put(wf: dict) -> dict:
    """Retourne un dictionnaire ne contenant que les champs autorisés pour PUT.

    Règles apprises par essais/erreurs avec l'API n8n :
    - settings : binaryMode et availableInMCP sont GET-only → whitelist explicite
    - meta/pinData/staticData null : provoquent "readOnly" validation → supprimés si null
    """
    result = {k: v for k, v in wf.items() if k in _WORKFLOW_PUT_ALLOWED}
    # Filtrer settings avec whitelist (pas blacklist)
    if "settings" in result and isinstance(result["settings"], dict):
        result["settings"] = {k: v for k, v in result["settings"].items() if k in _SETTINGS_ALLOWED}
    # Supprimer les champs optionnels null
    for optional_field in ("meta", "pinData", "staticData"):
        if optional_field in result and result[optional_field] is None:
            del result[optional_field]
    return result


def _nodes_summary(nodes: list) -> list:
    """Résumé compact des nœuds d'un workflow (type + nom)."""
    return [{"name": n.get("name", ""), "type": n.get("type", "").split(".")[-1]} for n in nodes]


# ---------------------------------------------------------------------------
# Tools — Workflows
# ---------------------------------------------------------------------------


@mcp.tool()
async def n8n_list_workflows(limit: int = 50) -> str:
    """Liste tous les workflows n8n avec id, nom, statut actif et dernière exécution.

    Appeler ce tool pour découvrir les workflows présents sur votre instance et
    leurs identifiants (à utiliser avec n8n_get_workflow, n8n_activate_workflow…).

    Paramètres :
    - limit : nombre max de workflows (défaut 50)

    Retourne : id, name, active, updatedAt pour chaque workflow.
    """
    try:
        data = await api_get("workflows", params={"limit": min(limit, 200)})
        workflows = data.get("data", data) if isinstance(data, dict) else data
        results = []
        for wf in workflows:
            results.append({
                "id": wf.get("id"),
                "name": wf.get("name"),
                "active": wf.get("active", False),
                "updatedAt": wf.get("updatedAt"),
                "createdAt": wf.get("createdAt"),
            })
        return _dumps(results)
    except Exception as exc:
        return _format_error(exc)


@mcp.tool()
async def n8n_get_workflow(workflow_id: str) -> str:
    """Récupère les détails complets d'un workflow n8n (nœuds, connexions, paramètres).

    Paramètres :
    - workflow_id : identifiant du workflow (obtenu via n8n_list_workflows)

    Retourne : name, active, nodes (résumé), settings, tags, dates.
    """
    try:
        wf = await api_get(f"workflows/{workflow_id}")
        nodes = wf.get("nodes", [])
        result = {
            "id": wf.get("id"),
            "name": wf.get("name"),
            "active": wf.get("active", False),
            "createdAt": wf.get("createdAt"),
            "updatedAt": wf.get("updatedAt"),
            "tags": [t.get("name") for t in (wf.get("tags") or [])],
            "settings": wf.get("settings"),
            "nodes": _nodes_summary(nodes),
            "node_count": len(nodes),
        }
        return _dumps(result)
    except Exception as exc:
        return _format_error(exc)


@mcp.tool()
async def n8n_update_workflow_code(
    workflow_id: str,
    node_name: str,
    new_code: str,
) -> str:
    """Met à jour le jsCode d'un nœud Code spécifique dans un workflow n8n.

    Récupère le workflow complet, trouve le nœud par son nom, met à jour
    le champ jsCode, puis sauvegarde via PUT.

    Attention — règles Code nodes n8n :
    - Pas de template literals (backticks)
    - Pas de for...of → utiliser .filter().map() ou forEach()
    - Pas de continue dans forEach → utiliser return

    Paramètres :
    - workflow_id : identifiant du workflow
    - node_name : nom exact du nœud Code (ex: 'Calcul période')
    - new_code : nouveau code JavaScript à injecter dans jsCode

    Retourne : confirmation avec le nom du nœud mis à jour.
    """
    try:
        wf = await api_get(f"workflows/{workflow_id}")
        nodes = wf.get("nodes", [])

        node_found = False
        for node in nodes:
            if node.get("name") == node_name:
                if "parameters" not in node:
                    node["parameters"] = {}
                node["parameters"]["jsCode"] = new_code
                node_found = True
                break

        if not node_found:
            available = [n.get("name") for n in nodes]
            return f"Erreur : nœud '{node_name}' non trouvé. Nœuds disponibles : {available}"

        payload = _filter_workflow_for_put(wf)
        payload["nodes"] = nodes

        result = await api_put(f"workflows/{workflow_id}", payload)
        return _dumps({
            "success": True,
            "workflow_id": workflow_id,
            "node_updated": node_name,
            "workflow_name": result.get("name", wf.get("name")),
        })
    except Exception as exc:
        return _format_error(exc)


@mcp.tool()
async def n8n_activate_workflow(workflow_id: str) -> str:
    """Active un workflow n8n (le met en production, il s'exécutera selon son trigger).

    Paramètres :
    - workflow_id : identifiant du workflow

    Retourne : confirmation avec le statut actif.
    """
    try:
        result = await api_patch(f"workflows/{workflow_id}/activate")
        return _dumps({
            "success": True,
            "workflow_id": workflow_id,
            "active": result.get("active", True),
            "name": result.get("name"),
        })
    except Exception as exc:
        return _format_error(exc)


@mcp.tool()
async def n8n_deactivate_workflow(workflow_id: str) -> str:
    """Désactive un workflow n8n (il ne s'exécutera plus automatiquement).

    Paramètres :
    - workflow_id : identifiant du workflow

    Retourne : confirmation avec le statut désactivé.
    """
    try:
        result = await api_patch(f"workflows/{workflow_id}/deactivate")
        return _dumps({
            "success": True,
            "workflow_id": workflow_id,
            "active": result.get("active", False),
            "name": result.get("name"),
        })
    except Exception as exc:
        return _format_error(exc)


# ---------------------------------------------------------------------------
# Tools — Exécutions
# ---------------------------------------------------------------------------


@mcp.tool()
async def n8n_get_executions(
    workflow_id: str = "",
    limit: int = 10,
    status: str = "",
    started_after: str = "",
    started_before: str = "",
    with_errors: bool = True,
) -> str:
    """Exécutions récentes, d'un workflow ou de toute l'instance.

    Paramètres :
    - workflow_id : identifiant du workflow. VIDE = tous les workflows.
    - limit : nombre d'exécutions (défaut 10, plafond 250)
    - status : 'error', 'crashed', 'success', 'waiting', 'running', 'canceled',
      ou vide pour tous
    - started_after / started_before : bornes ISO 8601
      (« 2026-08-30T00:00:00Z »), optionnelles
    - with_errors : résoudre la cause des exécutions en échec (défaut : oui)

    Deux corrections par rapport à la version précédente. `workflow_id` n'est
    plus obligatoire : on peut enfin demander « qu'est-ce qui a tourné », sans
    savoir où chercher. Et le message d'erreur est réellement renseigné :
    `GET /executions` ne renvoie pas le champ `data`, si bien que l'extraction
    qui y était tentée ne trouvait jamais rien et laissait `error` absent malgré
    la promesse de la docstring. La cause est donc relue par exécution en
    échec — un appel chacune, plafonné, le dépassement étant signalé.

    Pour une revue d'incidents, préférer n8n_list_failures, qui regroupe par
    workflow et résout les noms.
    """
    try:
        params: dict[str, Any] = {"limit": max(1, min(int(limit), EXEC_MAX_LIMIT))}
        if workflow_id:
            params["workflowId"] = workflow_id
        if status:
            params["status"] = status
        if started_after:
            params["startedAfter"] = started_after
        if started_before:
            params["startedBefore"] = started_before

        rows, note = await _fetch_executions(params)
        results = [_exec_summary(r) for r in rows]
        looked_up = 0
        if with_errors:
            results, looked_up = await _attach_errors(results)

        response: dict[str, Any] = {
            "count": len(results),
            "workflow_id": workflow_id or None,
            "status_filter": status or None,
            "errors_resolved": looked_up,
            "executions": results,
        }
        if note:
            response["note"] = note
        return _dumps({k: v for k, v in response.items() if v is not None})
    except Exception as exc:
        return _format_error(exc)


@mcp.tool()
async def n8n_get_execution_detail(
    execution_id: str,
    include_output: bool = False,
    max_item_chars: int = 800,
) -> str:
    """Détail d'une exécution : statut, erreurs par nœud, volumétrie.

    Paramètres :
    - execution_id : identifiant de l'exécution
    - include_output : joindre un aperçu du premier item produit par chaque
      nœud (défaut : non)
    - max_item_chars : taille maximale de cet aperçu, par nœud (défaut 800)

    Par défaut la réponse ne contient AUCUNE charge utile : statut global,
    erreurs nœud par nœud, et nombre d'items produits. La version précédente
    joignait systématiquement le premier item complet de chaque nœud : sur
    l'exécution 31027 de cette instance, cela produisait 195 000 caractères,
    au-delà de ce qu'un client MCP peut ingérer — l'outil de diagnostic
    devenait inutilisable précisément sur les cas à diagnostiquer.

    Avec `include_output`, chaque aperçu est borné et sa coupe est annoncée
    dans `preview_truncated`.

    Retourne aussi `errors`, la liste à plat de toutes les erreurs rencontrées,
    pour lire la cause sans parcourir l'arborescence des nœuds.
    """
    try:
        if not str(execution_id or "").strip():
            return "Veuillez fournir un execution_id."

        data = await api_get(f"executions/{execution_id}", params={"includeData": "true"})
        if not isinstance(data, dict):
            return f"Réponse inattendue pour l'exécution {execution_id}."

        result_data = (data.get("data") or {}).get("resultData", {}) if isinstance(
            data.get("data"), dict
        ) else {}
        node_run_data = result_data.get("runData", {}) if isinstance(result_data, dict) else {}

        nodes: dict[str, Any] = {}
        truncated_any = False
        for node_name, runs in (node_run_data or {}).items():
            if not isinstance(runs, list):
                continue
            info: dict[str, Any] = {"runs": len(runs)}
            for run in runs:
                if not isinstance(run, dict):
                    continue
                err = run.get("error")
                if err:
                    info["error"] = _clip(
                        (err.get("message") if isinstance(err, dict) else str(err)) or ""
                    )
                    continue
                output = run.get("data") or {}
                branches = output.get("main", []) if isinstance(output, dict) else []
                if branches and isinstance(branches[0], list):
                    info["output_count"] = len(branches[0])
                    if include_output and branches[0]:
                        raw = json.dumps(
                            (branches[0][0] or {}).get("json") or {}, ensure_ascii=False
                        )
                        if len(raw) > max_item_chars:
                            truncated_any = True
                            info["preview_truncated"] = True
                            info["first_item_chars"] = len(raw)
                        info["first_item"] = raw[:max_item_chars]
            nodes[node_name] = info

        errors = _exec_errors(data)
        summary = _exec_summary(data)
        response: dict[str, Any] = {
            **summary,
            "duration_s": _duration_seconds(summary.get("started_at"), summary.get("stopped_at")),
            "last_node_executed": result_data.get("lastNodeExecuted"),
            "nodes_run": len(nodes),
            "errors": errors,
            "nodes": nodes,
        }
        if not include_output:
            response["output_note"] = (
                "Charges utiles omises. Passer include_output=True pour un aperçu borné."
            )
        if truncated_any:
            response["preview_note"] = (
                f"Au moins un aperçu dépasse {max_item_chars} caractères et a été coupé."
            )
        return _dumps({k: v for k, v in response.items() if v not in (None, {}, [])})
    except Exception as exc:
        return _format_error(exc)


@mcp.tool()
async def n8n_trigger_workflow(workflow_id: str) -> str:
    """Déclenche manuellement un workflow n8n en mode test.

    Utilise l'endpoint POST /api/v1/workflows/{id}/run de l'API publique n8n.
    Note : le workflow doit être configuré avec un trigger manuel ou webhook pour
    accepter les déclenchements via API.

    Paramètres :
    - workflow_id : identifiant du workflow

    Retourne : executionId si le déclenchement a réussi.
    """
    try:
        result = await api_post(f"workflows/{workflow_id}/run")
        return _dumps({
            "success": True,
            "workflow_id": workflow_id,
            "executionId": result.get("executionId") or result.get("id"),
            "raw": result,
        })
    except Exception as exc:
        return _format_error(exc)


# ---------------------------------------------------------------------------
# Tools — Credentials
# ---------------------------------------------------------------------------


@mcp.tool()
async def n8n_list_credentials(limit: int = 50) -> str:
    """Liste les credentials disponibles dans n8n (id, nom, type).

    Utile pour connaître les IDs avant d'assigner des credentials à un nœud
    avec n8n_update_node_credentials.

    Paramètres :
    - limit : nombre max de credentials (défaut 50)

    Retourne : id, name, type pour chaque credential.
    """
    try:
        data = await api_get("credentials", params={"limit": min(limit, 200)})
        credentials = data.get("data", data) if isinstance(data, dict) else data
        results = []
        for cred in credentials:
            results.append({
                "id": cred.get("id"),
                "name": cred.get("name"),
                "type": cred.get("type"),
                "createdAt": cred.get("createdAt"),
            })
        return _dumps(results)
    except Exception as exc:
        return _format_error(exc)


@mcp.tool()
async def n8n_update_node_credentials(
    workflow_id: str,
    node_name: str,
    credential_type: str,
    credential_id: str,
    credential_name: str,
) -> str:
    """Assigne des credentials à un nœud spécifique dans un workflow n8n.

    Récupère le workflow, trouve le nœud par son nom, met à jour ses credentials,
    puis sauvegarde via PUT.

    Paramètres :
    - workflow_id : identifiant du workflow
    - node_name : nom exact du nœud (ex: 'API Dolibarr — Factures')
    - credential_type : type de credential (ex: 'httpHeaderAuth', 'smtp')
    - credential_id : ID du credential n8n (obtenu via n8n_list_credentials)
    - credential_name : nom du credential (ex: 'Header Auth account 2')

    Retourne : confirmation avec le nœud mis à jour.
    """
    try:
        wf = await api_get(f"workflows/{workflow_id}")
        nodes = wf.get("nodes", [])

        node_found = False
        for node in nodes:
            if node.get("name") == node_name:
                if "credentials" not in node or not isinstance(node["credentials"], dict):
                    node["credentials"] = {}
                node["credentials"][credential_type] = {
                    "id": credential_id,
                    "name": credential_name,
                }
                node_found = True
                break

        if not node_found:
            available = [n.get("name") for n in nodes]
            return f"Erreur : nœud '{node_name}' non trouvé. Nœuds disponibles : {available}"

        payload = _filter_workflow_for_put(wf)
        payload["nodes"] = nodes

        result = await api_put(f"workflows/{workflow_id}", payload)
        return _dumps({
            "success": True,
            "workflow_id": workflow_id,
            "node_updated": node_name,
            "credential_type": credential_type,
            "credential_id": credential_id,
            "workflow_name": result.get("name", wf.get("name")),
        })
    except Exception as exc:
        return _format_error(exc)


# ---------------------------------------------------------------------------
# Tool — Mise à jour complète d'un workflow (nodes + connections + code)
# ---------------------------------------------------------------------------


@mcp.tool()
async def n8n_patch_workflow_nodes(
    workflow_id: str,
    nodes_json: str,
    connections_json: str,
) -> str:
    """Remplace la liste complète des nœuds et connexions d'un workflow n8n.

    Paramètres :
    - workflow_id : identifiant du workflow
    - nodes_json : tableau JSON complet des nœuds (remplace l'existant)
    - connections_json : objet JSON complet des connexions (remplace l'existant)

    Retourne : confirmation avec le nombre de nœuds mis à jour.
    """
    try:
        import json as _json

        wf = await api_get(f"workflows/{workflow_id}")

        try:
            new_nodes = _json.loads(nodes_json)
        except Exception as e:
            return f"Erreur parsing nodes_json : {e}"

        try:
            new_connections = _json.loads(connections_json)
        except Exception as e:
            return f"Erreur parsing connections_json : {e}"

        payload = _filter_workflow_for_put(wf)
        payload["nodes"] = new_nodes
        payload["connections"] = new_connections

        result = await api_put(f"workflows/{workflow_id}", payload)
        return _dumps({
            "success": True,
            "workflow_id": workflow_id,
            "workflow_name": result.get("name", wf.get("name")),
            "node_count": len(result.get("nodes", new_nodes)),
        })
    except Exception as exc:
        return _format_error(exc)


# ---------------------------------------------------------------------------
# Tools — Inspection et mise à jour avancée
# ---------------------------------------------------------------------------


@mcp.tool()
async def n8n_get_workflow_full(workflow_id: str) -> str:
    """Retourne le JSON complet d'un workflow n8n (nœuds avec TOUS leurs paramètres).

    À utiliser pour inspecter les URLs, credentials ou paramètres exacts des nœuds
    avant une mise à jour via n8n_update_node_params ou n8n_patch_workflow_nodes.

    Paramètres :
    - workflow_id : identifiant du workflow

    Retourne : JSON complet du workflow (nodes avec parameters, connections, settings).
    """
    try:
        wf = await api_get(f"workflows/{workflow_id}")
        return _dumps(wf)
    except Exception as exc:
        return _format_error(exc)


@mcp.tool()
async def n8n_update_node_params(
    workflow_id: str,
    node_name: str,
    params_json: str,
) -> str:
    """Met à jour les paramètres d'un nœud (httpRequest, code, etc.) dans un workflow n8n.

    Fusionne les paramètres fournis avec les paramètres existants du nœud (merge partiel).
    Permet de modifier l'URL d'un httpRequest, le body, les headers, etc.

    Paramètres :
    - workflow_id : identifiant du workflow
    - node_name : nom exact du nœud (ex: 'Envoi Matrix', 'Matrix — Notifier')
    - params_json : objet JSON des paramètres à mettre à jour (merge sur parameters existants)

    Exemple params_json pour changer l'URL d'un nœud httpRequest :
    {"url": "={{ 'https://matrix.example.org/_matrix/client/v3/rooms/' + $vars.MATRIX_GENERAL + '/send/m.room.message/' + Date.now() }}"}

    Retourne : confirmation avec le nom du nœud mis à jour.
    """
    try:
        import json as _json

        try:
            new_params = _json.loads(params_json)
        except Exception as e:
            return f"Erreur parsing params_json : {e}"

        wf = await api_get(f"workflows/{workflow_id}")
        nodes = wf.get("nodes", [])

        node_found = False
        for node in nodes:
            if node.get("name") == node_name:
                if "parameters" not in node:
                    node["parameters"] = {}
                node["parameters"].update(new_params)
                node_found = True
                break

        if not node_found:
            available = [n.get("name") for n in nodes]
            return f"Erreur : nœud '{node_name}' non trouvé. Nœuds disponibles : {available}"

        payload = _filter_workflow_for_put(wf)
        payload["nodes"] = nodes

        result = await api_put(f"workflows/{workflow_id}", payload)
        return _dumps({
            "success": True,
            "workflow_id": workflow_id,
            "node_updated": node_name,
            "workflow_name": result.get("name", wf.get("name")),
        })
    except Exception as exc:
        return _format_error(exc)


# ---------------------------------------------------------------------------
# Tools — Variables globales n8n
# ---------------------------------------------------------------------------


@mcp.tool()
async def n8n_list_variables() -> str:
    """Liste les variables globales n8n (Settings > Variables).

    Les variables sont référençables dans les workflows via {{ $vars.NOM_VARIABLE }}.

    Retourne : liste des variables (id, key, value).
    """
    try:
        result = await api_get("variables")
        return _dumps(result)
    except Exception as exc:
        return _format_error(exc)


@mcp.tool()
async def n8n_create_variable(key: str, value: str) -> str:
    """Crée une variable globale n8n (référençable via {{ $vars.KEY }} dans tous les workflows).

    Paramètres :
    - key : nom de la variable (ex: MATRIX_GENERAL) — sans espaces, sans caractères spéciaux
    - value : valeur de la variable (ex: !roomid:matrix.example.org)

    Retourne : confirmation avec l'id et la clé de la variable créée.
    """
    try:
        result = await api_post("variables", {"key": key, "value": value})
        return _dumps({"success": True, "variable": result})
    except Exception as exc:
        return _format_error(exc)


@mcp.tool()
async def n8n_create_workflow(
    name: str,
    nodes_json: str = "",
    connections_json: str = "",
    settings_json: str = "",
    activate: bool = False,
) -> str:
    """Crée un nouveau workflow n8n via POST /api/v1/workflows.

    Confirmation utilisateur requise avant exécution.

    Paramètres :
    - name : nom du workflow (obligatoire, ex: "Relance facture J+30")
    - nodes_json : JSON-array des nœuds n8n (défaut : un seul nœud Start vide).
      Format attendu (exemple minimal) :
        [{"id":"trigger","name":"When clicking 'Test workflow'",
          "type":"n8n-nodes-base.manualTrigger","typeVersion":1,
          "position":[100,100],"parameters":{}}]
    - connections_json : JSON-object des connexions entre nœuds (défaut : {}).
      Format : {"NodeName":{"main":[[{"node":"OtherNode","type":"main","index":0}]]}}
    - settings_json : JSON-object de réglages (executionOrder, timezone, etc.)
      Si vide, défaut n8n : {"executionOrder":"v1"}.
    - activate : si True, active le workflow après création (POST /workflows/{id}/activate).
      Défaut : False (workflow créé en brouillon).

    Particularités API n8n :
    - Le payload POST exige `name`, `nodes`, `connections`, `settings`. Sans nodes,
      l'API rejette avec 400. On force un nœud manuel vide si nodes_json est vide
      pour que la création passe — il faudra ensuite remplir le workflow via
      n8n_update_workflow_code ou via l'UI.
    - L'API renvoie l'objet workflow complet avec son `id` et `versionId`.

    Cas d'usage typiques :
    - Générer un brouillon de workflow puis le compléter par UI
    - Cloner un workflow existant : récupérer via n8n_get_workflow_full,
      modifier le name, puis n8n_create_workflow avec les mêmes nodes/connections

    Retourne : id, name, active, et URL d'édition n8n.
    """
    try:
        if not name or not name.strip():
            return "Le nom (name) est obligatoire."

        # Default minimal manual-trigger node if no nodes provided
        default_nodes = [{
            "id": "manual-trigger",
            "name": "When clicking 'Test workflow'",
            "type": "n8n-nodes-base.manualTrigger",
            "typeVersion": 1,
            "position": [240, 300],
            "parameters": {},
        }]

        try:
            nodes = json.loads(nodes_json) if nodes_json.strip() else default_nodes
            if not isinstance(nodes, list):
                return "nodes_json doit être un JSON-array."
            if not nodes:
                nodes = default_nodes
        except json.JSONDecodeError as exc:
            return f"nodes_json invalide : {exc}"

        try:
            connections = json.loads(connections_json) if connections_json.strip() else {}
            if not isinstance(connections, dict):
                return "connections_json doit être un JSON-object."
        except json.JSONDecodeError as exc:
            return f"connections_json invalide : {exc}"

        try:
            settings = json.loads(settings_json) if settings_json.strip() else {"executionOrder": "v1"}
            if not isinstance(settings, dict):
                return "settings_json doit être un JSON-object."
        except json.JSONDecodeError as exc:
            return f"settings_json invalide : {exc}"

        payload: dict[str, Any] = {
            "name": name.strip(),
            "nodes": nodes,
            "connections": connections,
            "settings": settings,
        }

        logger.info("n8n_create_workflow POST workflows name=%s nodes=%d", name, len(nodes))
        wf = await api_post("workflows", payload)

        if not isinstance(wf, dict) or "id" not in wf:
            return _dumps({"success": False, "raw_response": wf, "error": "Réponse inattendue de l'API n8n"})

        wf_id = wf["id"]
        response = {
            "success": True,
            "workflow_id": wf_id,
            "name": wf.get("name"),
            "active": bool(wf.get("active", False)),
            "url": f"{N8N_BASE_URL}/workflow/{wf_id}",
            "node_count": len(wf.get("nodes", [])),
        }

        if activate:
            try:
                await api_post(f"workflows/{wf_id}/activate", {})
                response["active"] = True
                response["activated"] = True
            except Exception as act_exc:
                response["activate_warning"] = (
                    f"Workflow créé mais activation échouée : {act_exc}. "
                    "À activer manuellement via n8n_activate_workflow."
                )

        return _dumps(response)

    except Exception as exc:
        return _format_error(exc)


@mcp.tool()
async def n8n_delete_workflow(workflow_id: str) -> str:
    """Supprime un workflow n8n via DELETE /api/v1/workflows/{id}.

    ⚠️ DESTRUCTIF — confirmation utilisateur requise avant exécution.

    L'API n8n refuse la suppression d'un workflow actif → désactive d'abord
    automatiquement si nécessaire (POST /workflows/{id}/deactivate).

    Paramètres :
    - workflow_id : ID du workflow (obligatoire)

    Retourne : confirmation + nom du workflow supprimé. En cas d'échec,
    diagnostic structuré (état du workflow, code HTTP).

    Cas d'usage : nettoyer des brouillons / workflows de test après vérification.
    """
    try:
        if not workflow_id or not str(workflow_id).strip():
            return "Le workflow_id est obligatoire."

        # Snapshot pour diagnostic + log
        snapshot: dict[str, Any] = {}
        try:
            wf = await api_get(f"workflows/{workflow_id}")
            if isinstance(wf, dict):
                snapshot = {
                    "name": wf.get("name"),
                    "active": bool(wf.get("active", False)),
                    "node_count": len(wf.get("nodes", [])),
                }
        except Exception:
            return _dumps({
                "success": False,
                "workflow_id": workflow_id,
                "error": f"Workflow {workflow_id} introuvable ou inaccessible.",
            })

        # Désactiver d'abord si actif (sinon DELETE 400 / 409)
        if snapshot.get("active"):
            try:
                await api_post(f"workflows/{workflow_id}/deactivate", {})
                snapshot["deactivated_before_delete"] = True
            except Exception as deact_exc:
                logger.warning("n8n_delete_workflow deactivate failed: %s", deact_exc)

        logger.info("n8n_delete_workflow DELETE workflows/%s (%s)", workflow_id, snapshot.get("name"))
        try:
            await api_delete(f"workflows/{workflow_id}")
        except httpx.HTTPStatusError as http_exc:
            return _dumps({
                "success": False,
                "workflow_id": workflow_id,
                "http_status": http_exc.response.status_code,
                "detail": http_exc.response.text[:500],
                "workflow_state": snapshot,
            })

        return _dumps({
            "success": True,
            "workflow_id": workflow_id,
            "deleted": snapshot.get("name", "(unknown)"),
            "message": f"Workflow {workflow_id} ({snapshot.get('name')}) supprimé.",
        })

    except Exception as exc:
        return _format_error(exc)


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Exécutions : diagnostic et surveillance
# ---------------------------------------------------------------------------

#: Plafond de `limit` accepté par l'API publique n8n.
EXEC_MAX_LIMIT = 250
#: Nombre d'exécutions en échec dont on va rechercher le message d'erreur.
#: Chaque recherche coûte un appel : sans plafond, une liste large devient
#: interminable. Le dépassement est signalé, jamais silencieux.
EXEC_ERROR_LOOKUP_MAX = 25
#: Longueur retenue d'un message d'erreur. La coupe est annoncée.
EXEC_ERROR_CHARS = 400


def _clip(text: Any, limit: int = EXEC_ERROR_CHARS) -> str:
    """Tronque un texte en signalant la coupe plutôt qu'en la taisant."""
    s = "" if text is None else str(text)
    return s if len(s) <= limit else s[:limit] + f"… (+{len(s) - limit} car.)"


def _exec_errors(raw: dict) -> list[dict]:
    """Extrait TOUTES les erreurs d'une exécution, nœud par nœud.

    Deux emplacements coexistent dans la charge n8n et il faut les deux :
    `resultData.error` porte l'échec global, `resultData.runData[nœud][i].error`
    porte l'échec d'un nœud précis. Ne lire que le premier trouvé, comme le
    faisait la version précédente, masque les échecs en cascade.
    """
    if not isinstance(raw, dict):
        return []
    result_data = ((raw.get("data") or {}).get("resultData") or {}) if isinstance(
        raw.get("data"), dict
    ) else {}
    errors: list[dict] = []
    seen: set = set()

    def _add(node: Any, err: Any) -> None:
        if not isinstance(err, dict):
            if not err:
                return
            err = {"message": str(err)}
        message = err.get("message") or err.get("description") or ""
        key = (node, message)
        if not message or key in seen:
            return
        seen.add(key)
        entry = {
            "node": node,
            "message": _clip(message),
            "type": err.get("name") or err.get("type") or None,
        }
        errors.append({k: v for k, v in entry.items() if v is not None})

    _add(result_data.get("lastNodeExecuted"), result_data.get("error"))
    run_data = result_data.get("runData") or {}
    if isinstance(run_data, dict):
        for node_name, runs in run_data.items():
            if isinstance(runs, list):
                for run in runs:
                    if isinstance(run, dict):
                        _add(node_name, run.get("error"))
    return errors


def _exec_summary(raw: dict) -> dict:
    """Résumé compact d'une exécution, sans ses charges utiles."""
    if not isinstance(raw, dict):
        return {}
    out = {
        "id": str(raw.get("id")) if raw.get("id") is not None else None,
        "workflow_id": raw.get("workflowId"),
        "workflow_name": (raw.get("workflowData") or {}).get("name")
        if isinstance(raw.get("workflowData"), dict) else None,
        "status": raw.get("status"),
        "mode": raw.get("mode"),
        "started_at": raw.get("startedAt"),
        "stopped_at": raw.get("stoppedAt"),
        "finished": raw.get("finished"),
        "retry_of": raw.get("retryOf"),
        "retry_success_id": raw.get("retrySuccessId"),
    }
    return {k: v for k, v in out.items() if v is not None}


def _duration_seconds(started: Any, stopped: Any) -> float | None:
    """Durée d'une exécution en secondes, ou None si les bornes manquent."""
    from datetime import datetime

    if not started or not stopped:
        return None
    try:
        fmt = lambda s: datetime.fromisoformat(str(s).replace("Z", "+00:00"))  # noqa: E731
        return round((fmt(stopped) - fmt(started)).total_seconds(), 2)
    except (ValueError, TypeError):
        return None


async def _fetch_executions(params: dict) -> "tuple[list, str]":
    """Liste des exécutions, avec repli si l'instance ignore les bornes de date.

    `startedAfter` / `startedBefore` sont apparus tardivement dans l'API
    publique. Sur une instance plus ancienne, les envoyer peut produire un 400 :
    on retente alors sans eux et on filtre côté client, en le disant.
    """
    try:
        data = await api_get("executions", params=params)
        return (data.get("data", data) if isinstance(data, dict) else data) or [], ""
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code != 400 or not (
            "startedAfter" in params or "startedBefore" in params
        ):
            raise
    stripped = {k: v for k, v in params.items() if k not in ("startedAfter", "startedBefore")}
    data = await api_get("executions", params=stripped)
    rows = (data.get("data", data) if isinstance(data, dict) else data) or []
    after, before = params.get("startedAfter"), params.get("startedBefore")
    kept = [
        r for r in rows
        if (not after or str(r.get("startedAt") or "") >= after)
        and (not before or str(r.get("startedAt") or "") <= before)
    ]
    return kept, (
        "Cette instance n'accepte pas les bornes de date sur /executions : "
        "filtrage effectué côté client, sur la page demandée uniquement."
    )


async def _attach_errors(rows: list, budget: int = EXEC_ERROR_LOOKUP_MAX) -> "tuple[list, int]":
    """Complète les exécutions en échec avec leur message d'erreur.

    `GET /executions` ne renvoie PAS le champ `data` : la version précédente y
    cherchait pourtant l'erreur, si bien que le champ `error` promis par la
    docstring n'était jamais rempli. Le message ne s'obtient qu'en relisant
    chaque exécution avec `includeData=true` — d'où un appel par échec, borné.
    """
    looked_up = 0
    for row in rows:
        if row.get("status") not in ("error", "crashed"):
            continue
        if looked_up >= budget:
            row["error_lookup_skipped"] = True
            continue
        looked_up += 1
        try:
            detail = await api_get(
                f"executions/{row['id']}", params={"includeData": "true"}
            )
            errors = _exec_errors(detail if isinstance(detail, dict) else {})
            if errors:
                row["errors"] = errors
                row["error"] = errors[0]["message"]
        except Exception as exc:
            row["error_lookup_failed"] = _format_error(exc)
    return rows, looked_up


@mcp.tool()
async def n8n_list_failures(
    hours: int = 24,
    limit: int = 50,
    workflow_id: str = "",
    include_crashed: bool = True,
) -> str:
    """Liste les exécutions en ÉCHEC, tous workflows confondus.

    Paramètres :
    - hours : profondeur de la fenêtre, en heures (défaut 24)
    - limit : nombre max d'exécutions rendues (défaut 50, plafond 250)
    - workflow_id : restreindre à un workflow (défaut : tous)
    - include_crashed : inclure le statut `crashed` en plus de `error`

    C'est la réponse à « qu'est-ce qui a échoué cette nuit ? ». Jusqu'ici il
    fallait interroger chaque workflow séparément — dix-sept appels sur cette
    instance — puis relire chaque exécution pour en obtenir la cause.

    Chaque échec est rendu avec le nom de son workflow ET son message d'erreur,
    résolu par relecture de l'exécution. Les échecs sont regroupés par workflow
    dans `by_workflow`, avec le nombre d'occurrences : un même workflow qui
    tombe chaque matin se voit d'un coup d'œil.
    """
    try:
        from datetime import datetime, timedelta, timezone

        window = max(1, int(hours))
        since = (datetime.now(timezone.utc) - timedelta(hours=window)).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
        capped = max(1, min(int(limit), EXEC_MAX_LIMIT))

        rows: list = []
        notes: set = set()
        for status in ("error", "crashed") if include_crashed else ("error",):
            params: dict[str, Any] = {
                "status": status,
                "limit": capped,
                "startedAfter": since,
            }
            if workflow_id:
                params["workflowId"] = workflow_id
            try:
                found, note = await _fetch_executions(params)
            except httpx.HTTPStatusError as exc:
                # Un statut inconnu de l'instance ne doit pas emporter le reste.
                if exc.response.status_code == 400 and status == "crashed":
                    notes.add("Le statut `crashed` n'est pas reconnu par cette instance.")
                    continue
                raise
            if note:
                notes.add(note)
            rows.extend(found)

        rows.sort(key=lambda r: str(r.get("startedAt") or ""), reverse=True)
        rows = [_exec_summary(r) for r in rows[:capped]]
        rows, looked_up = await _attach_errors(rows)

        # Nom du workflow : absent de la réponse de liste, résolu une fois par
        # workflow distinct plutôt qu'une fois par exécution.
        names: dict[str, str] = {}
        for row in rows:
            wid = row.get("workflow_id")
            if not wid or wid in names:
                continue
            try:
                wf = await api_get(f"workflows/{wid}")
                names[wid] = (wf or {}).get("name") or wid
            except Exception:
                names[wid] = wid
        for row in rows:
            row["workflow_name"] = names.get(row.get("workflow_id"), row.get("workflow_id"))

        grouped: dict[str, dict] = {}
        for row in rows:
            key = row.get("workflow_name") or row.get("workflow_id") or "?"
            entry = grouped.setdefault(
                key, {"workflow_id": row.get("workflow_id"), "failures": 0,
                      "last_started_at": None, "last_error": None}
            )
            entry["failures"] += 1
            if entry["last_started_at"] is None:
                entry["last_started_at"] = row.get("started_at")
                entry["last_error"] = row.get("error")

        response: dict[str, Any] = {
            "window_hours": window,
            "since": since,
            "count": len(rows),
            "workflows_affected": len(grouped),
            "errors_resolved": looked_up,
            "by_workflow": sorted(
                ({"workflow_name": k, **v} for k, v in grouped.items()),
                key=lambda g: -g["failures"],
            ),
            "executions": rows,
        }
        if looked_up >= EXEC_ERROR_LOOKUP_MAX:
            response["error_lookup_note"] = (
                f"Causes résolues pour les {EXEC_ERROR_LOOKUP_MAX} premiers échecs "
                "seulement ; les suivants portent `error_lookup_skipped`."
            )
        if notes:
            response["notes"] = sorted(notes)
        return _dumps(response)

    except Exception as exc:
        return _format_error(exc)


@mcp.tool()
async def n8n_workflows_health(active_only: bool = True, per_workflow: int = 5) -> str:
    """État de santé des workflows : dernière exécution, échecs consécutifs, cause.

    Paramètres :
    - active_only : ne regarder que les workflows actifs (défaut : oui)
    - per_workflow : profondeur d'historique inspectée par workflow (défaut 5)

    Pour chaque workflow : statut de la dernière exécution, date du dernier
    succès, nombre d'échecs CONSÉCUTIFS et cause du dernier. C'est ce compteur
    qui distingue un incident isolé d'un workflow cassé depuis des jours —
    celui qui échoue toutes les nuits sans que personne ne le voie.

    Les workflows sont rendus les plus dégradés en tête. `never_ran` signale
    ceux qui n'ont aucune exécution : actifs mais jamais déclenchés, ce qui est
    souvent un défaut de configuration plutôt qu'une absence d'événement.

    Coûte un appel par workflow inspecté : sur un parc important, préférer
    n8n_list_failures pour une vue rapide.
    """
    try:
        data = await api_get("workflows", params={"limit": 250})
        workflows = (data.get("data", data) if isinstance(data, dict) else data) or []
        if active_only:
            workflows = [w for w in workflows if w.get("active")]

        report = []
        for wf in workflows:
            wid = wf.get("id")
            entry: dict[str, Any] = {
                "workflow_id": wid,
                "name": wf.get("name"),
                "active": bool(wf.get("active")),
            }
            try:
                rows, _ = await _fetch_executions(
                    {"workflowId": wid, "limit": max(1, min(int(per_workflow), 50))}
                )
            except Exception as exc:
                entry["health"] = "inconnu"
                entry["lookup_error"] = _format_error(exc)
                report.append(entry)
                continue

            rows = sorted(rows, key=lambda r: str(r.get("startedAt") or ""), reverse=True)
            if not rows:
                entry.update({"health": "jamais exécuté", "never_ran": True,
                              "consecutive_failures": 0})
                report.append(entry)
                continue

            summaries = [_exec_summary(r) for r in rows]
            last = summaries[0]
            consecutive = 0
            for row in summaries:
                if row.get("status") in ("error", "crashed"):
                    consecutive += 1
                else:
                    break
            last_success = next(
                (r["started_at"] for r in summaries
                 if r.get("status") == "success" and r.get("started_at")), None
            )
            entry.update({
                "health": "en échec" if consecutive else "ok",
                "last_status": last.get("status"),
                "last_started_at": last.get("started_at"),
                "last_duration_s": _duration_seconds(
                    last.get("started_at"), last.get("stopped_at")
                ),
                "consecutive_failures": consecutive,
                "last_success_at": last_success,
                "inspected": len(summaries),
            })
            if consecutive:
                # `consecutive == inspected` veut dire que la fenêtre inspectée
                # ne contient aucun succès : l'échec peut être plus ancien.
                if consecutive >= len(summaries):
                    entry["consecutive_failures_at_least"] = True
                enriched, _ = await _attach_errors([dict(last)], budget=1)
                if enriched[0].get("errors"):
                    entry["last_error"] = enriched[0]["errors"][0]["message"]
                    entry["last_error_node"] = enriched[0]["errors"][0].get("node")
            report.append(entry)

        rank = {"en échec": 0, "jamais exécuté": 1, "inconnu": 2, "ok": 3}
        report.sort(key=lambda e: (rank.get(e.get("health"), 9),
                                   -(e.get("consecutive_failures") or 0)))
        failing = [e for e in report if e.get("health") == "en échec"]
        return _dumps({
            "checked": len(report),
            "active_only": active_only,
            "failing": len(failing),
            "never_ran": sum(1 for e in report if e.get("never_ran")),
            "workflows": report,
        })

    except Exception as exc:
        return _format_error(exc)


@mcp.tool()
async def n8n_retry_execution(execution_id: str, load_workflow: bool = False) -> str:
    """Rejoue une exécution en échec.

    Confirmation utilisateur requise avant exécution.

    Paramètres :
    - execution_id : identifiant de l'exécution à rejouer
    - load_workflow : rejouer avec la version ACTUELLE du workflow (défaut :
      non, c'est-à-dire avec la version qui avait échoué). Passer True après
      avoir corrigé le workflow, sinon la correction n'est pas prise en compte.

    Rejouer relance le traitement réel : si le workflow envoie un message ou
    écrit quelque part, il le refera. Vérifier d'abord la cause avec
    n8n_get_execution_detail.

    Retourne l'identifiant de la nouvelle exécution et son issue. Une instance
    trop ancienne pour exposer cette route le dit explicitement.
    """
    try:
        if not str(execution_id or "").strip():
            return "Veuillez fournir un execution_id."
        try:
            result = await api_post(
                f"executions/{execution_id}/retry", {"loadWorkflow": bool(load_workflow)}
            )
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code in (404, 405):
                return _dumps({
                    "success": False,
                    "execution_id": execution_id,
                    "message": (
                        "Cette instance n'expose pas /executions/{id}/retry "
                        f"(HTTP {exc.response.status_code}). Rejouer depuis "
                        "l'interface n8n, ou déclencher le workflow avec "
                        "n8n_trigger_workflow."
                    ),
                })
            raise

        summary = _exec_summary(result if isinstance(result, dict) else {})
        enriched, _ = await _attach_errors([summary], budget=1) if summary else ([summary], 0)
        return _dumps({
            "success": summary.get("status") not in ("error", "crashed"),
            "retried_execution_id": execution_id,
            "loaded_current_workflow": bool(load_workflow),
            "new_execution": enriched[0] if enriched else summary,
            "message": (
                f"Exécution {execution_id} rejouée ; nouvelle exécution "
                f"{summary.get('id')} en statut {summary.get('status')}."
            ),
        })

    except Exception as exc:
        return _format_error(exc)


@mcp.tool()
async def n8n_delete_execution(execution_id: str) -> str:
    """Supprime une exécution de l'historique n8n.

    Confirmation utilisateur requise avant exécution (action destructive).

    Paramètres :
    - execution_id : identifiant de l'exécution à supprimer

    Sert à purger un historique encombré par un workflow qui a bouclé. La
    suppression ne touche PAS le workflow, seulement la trace de son passage,
    et elle est définitive : les données d'entrée et de sortie disparaissent
    avec, donc plus aucun diagnostic possible après coup.

    Retourne la confirmation, vérifiée par relecture.
    """
    try:
        if not str(execution_id or "").strip():
            return "Veuillez fournir un execution_id."

        before = await api_get(f"executions/{execution_id}")
        summary = _exec_summary(before if isinstance(before, dict) else {})
        await api_delete(f"executions/{execution_id}")

        gone = True
        try:
            again = await api_get(f"executions/{execution_id}")
            gone = not (isinstance(again, dict) and again.get("id"))
        except httpx.HTTPStatusError as exc:
            gone = exc.response.status_code == 404

        return _dumps({
            "success": gone,
            "execution_id": execution_id,
            "deleted": summary,
            "message": (
                f"Exécution {execution_id} supprimée." if gone else
                f"Le DELETE a répondu sans erreur mais l'exécution {execution_id} "
                "est toujours lisible : suppression non effective."
            ),
        })

    except Exception as exc:
        return _format_error(exc)


@mcp.tool()
async def n8n_list_tags() -> str:
    """Liste les étiquettes définies sur l'instance n8n.

    Sans paramètre. Retourne id, nom et dates de chaque étiquette.

    Les étiquettes servent à filtrer les workflows par domaine. Une instance
    trop ancienne pour exposer /tags le dit explicitement plutôt que de rendre
    une liste vide, qui se lirait comme « aucune étiquette définie ».
    """
    try:
        try:
            data = await api_get("tags", params={"limit": 250})
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code in (404, 405):
                return _dumps({
                    "success": False,
                    "message": (
                        "Cette instance n'expose pas /tags "
                        f"(HTTP {exc.response.status_code})."
                    ),
                })
            raise

        rows = (data.get("data", data) if isinstance(data, dict) else data) or []
        tags = [
            {"id": t.get("id"), "name": t.get("name"), "updated_at": t.get("updatedAt")}
            for t in rows if isinstance(t, dict)
        ]
        return _dumps({"success": True, "count": len(tags), "tags": tags})

    except Exception as exc:
        return _format_error(exc)


def main() -> None:
    """Point d'entree du serveur MCP (stdio)."""
    mcp.run()


if __name__ == "__main__":
    main()
