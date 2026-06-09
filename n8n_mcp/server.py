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
    workflow_id: str,
    limit: int = 10,
    status: str = "",
) -> str:
    """Récupère les exécutions récentes d'un workflow (pour voir les erreurs).

    Paramètres :
    - workflow_id : identifiant du workflow
    - limit : nombre d'exécutions à retourner (défaut 10, max 50)
    - status : filtrer par statut ('error', 'success', 'waiting', '' = tous)

    Retourne : id, startedAt, stoppedAt, finished, status, mode, et message d'erreur si présent.
    """
    try:
        params: dict[str, Any] = {
            "workflowId": workflow_id,
            "limit": min(limit, 50),
        }
        if status:
            params["status"] = status

        data = await api_get("executions", params=params)
        executions = data.get("data", data) if isinstance(data, dict) else data

        results = []
        for ex in executions:
            entry: dict[str, Any] = {
                "id": ex.get("id"),
                "startedAt": ex.get("startedAt"),
                "stoppedAt": ex.get("stoppedAt"),
                "finished": ex.get("finished"),
                "status": ex.get("status"),
                "mode": ex.get("mode"),
            }
            # Extraire le message d'erreur si présent
            error = ex.get("data", {}) or {}
            if isinstance(error, dict):
                result_data = error.get("resultData", {}) or {}
                run_data = result_data.get("runData", {}) or {}
                for node_name, node_runs in run_data.items():
                    if isinstance(node_runs, list):
                        for run in node_runs:
                            err = run.get("error") if isinstance(run, dict) else None
                            if err:
                                entry["error"] = {
                                    "node": node_name,
                                    "message": err.get("message", str(err))[:300],
                                }
                                break
                    if "error" in entry:
                        break
            results.append(entry)

        return _dumps(results)
    except Exception as exc:
        return _format_error(exc)


@mcp.tool()
async def n8n_get_execution_detail(execution_id: str) -> str:
    """Récupère le détail complet d'une exécution n8n pour déboguer les erreurs.

    Inclut les données d'entrée/sortie de chaque nœud et les messages d'erreur détaillés.

    Paramètres :
    - execution_id : identifiant de l'exécution (obtenu via n8n_get_executions)

    Retourne : statut global, résultats par nœud (output ou erreur).
    """
    try:
        data = await api_get(f"executions/{execution_id}", params={"includeData": "true"})

        status = data.get("status")
        started = data.get("startedAt")
        stopped = data.get("stoppedAt")
        mode = data.get("mode")

        # Extraire les résultats par nœud
        run_data = (data.get("data") or {})
        result_data = run_data.get("resultData", {}) if isinstance(run_data, dict) else {}
        node_run_data = result_data.get("runData", {}) if isinstance(result_data, dict) else {}

        nodes_summary = {}
        for node_name, runs in node_run_data.items():
            if not isinstance(runs, list):
                continue
            node_info: dict[str, Any] = {}
            for run in runs:
                if not isinstance(run, dict):
                    continue
                err = run.get("error")
                if err:
                    node_info["error"] = {
                        "message": err.get("message", str(err))[:500],
                        "stack": (err.get("stack") or "")[:300],
                    }
                else:
                    # Résumé de l'output (premier item de chaque branche)
                    output_data = run.get("data", {}) or {}
                    main_output = output_data.get("main", []) if isinstance(output_data, dict) else []
                    if main_output and isinstance(main_output, list) and main_output[0]:
                        branch = main_output[0]
                        if isinstance(branch, list):
                            node_info["output_count"] = len(branch)
                            if branch:
                                first = branch[0]
                                node_info["first_item"] = (first.get("json") or {})
            nodes_summary[node_name] = node_info

        return _dumps({
            "id": execution_id,
            "status": status,
            "startedAt": started,
            "stoppedAt": stopped,
            "mode": mode,
            "nodes": nodes_summary,
        })
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

def main() -> None:
    """Point d'entree du serveur MCP (stdio)."""
    mcp.run()


if __name__ == "__main__":
    main()
