# n8n connector (`n8n-mcp`)

MCP server for the [n8n](https://n8n.io/) public REST API. Inspect, create, edit, activate and debug workflows; manage credentials and global variables.

Part of [**mcp-foss-connectors**](../README.md). Configured entirely through
environment variables — no host, login or secret is baked into the code.

## Environment variables

| Variable | Required | Description |
|---|---|---|
| `N8N_BASE_URL` | yes | Base URL of the n8n instance, e.g. `https://n8n.example.com`. No trailing slash. The *public API* must be enabled. |
| `N8N_API_KEY` | yes | n8n API key, sent as the `X-N8N-API-KEY` header (Settings → n8n API). |

## Tools (22)

- `n8n_list_workflows` — Liste tous les workflows n8n avec id, nom, statut actif et dernière exécution
- `n8n_get_workflow` — Récupère les détails complets d'un workflow n8n (nœuds, connexions, paramètres)
- `n8n_update_workflow_code` — Met à jour le jsCode d'un nœud Code spécifique dans un workflow n8n
- `n8n_activate_workflow` — Active un workflow n8n (le met en production, il s'exécutera selon son trigger)
- `n8n_deactivate_workflow` — Désactive un workflow n8n (il ne s'exécutera plus automatiquement)
- `n8n_get_executions` — Récupère les exécutions récentes d'un workflow (pour voir les erreurs)
- `n8n_get_execution_detail` — Récupère le détail complet d'une exécution n8n pour déboguer les erreurs
- `n8n_trigger_workflow` — Déclenche manuellement un workflow n8n en mode test
- `n8n_list_credentials` — Liste les credentials disponibles dans n8n (id, nom, type)
- `n8n_update_node_credentials` — Assigne des credentials à un nœud spécifique dans un workflow n8n
- `n8n_patch_workflow_nodes` — Remplace la liste complète des nœuds et connexions d'un workflow n8n
- `n8n_get_workflow_full` — Retourne le JSON complet d'un workflow n8n (nœuds avec TOUS leurs paramètres)
- `n8n_update_node_params` — Met à jour les paramètres d'un nœud (httpRequest, code, etc.) dans un workflow n8n
- `n8n_list_variables` — Liste les variables globales n8n (Settings > Variables)
- `n8n_create_variable` — Crée une variable globale n8n (référençable via {{ $vars.KEY }} dans tous les workflows)
- `n8n_create_workflow` — Crée un nouveau workflow n8n via POST /api/v1/workflows
- `n8n_delete_workflow` — Supprime un workflow n8n via DELETE /api/v1/workflows/{id}

- `n8n_list_failures` — Exécutions en échec, tous workflows confondus, avec leur cause
- `n8n_workflows_health` — État de santé : échecs consécutifs, dernier succès, cause
- `n8n_retry_execution` — Rejoue une exécution en échec
- `n8n_delete_execution` — Supprime une exécution de l'historique
- `n8n_list_tags` — Liste les étiquettes de l'instance

## Debugging executions: three things that made it harder than it had to be

Measured on 1 617 local sessions, **58 % of all n8n calls go to
`get_executions` and `get_execution_detail`** — this connector is used to work
out what broke. Three defects stood in the way, all found on the live instance
on 31/08/2026 and all fixed:

1. **`get_executions` required a `workflow_id`.** You could not ask *what*
   failed, only whether a workflow you already suspected had. With seventeen
   active workflows that is seventeen calls. `workflow_id` is now optional, and
   `n8n_list_failures` answers the real question in one call, grouped by
   workflow.
2. **The `error` field was never populated.** `GET /executions` does not return
   the `data` envelope, so the extraction attempted on the list response found
   nothing — while the docstring promised an error message. The cause is now
   resolved by re-reading each failed execution with `includeData=true`, one
   call each, capped and announced.
3. **`get_execution_detail` returned 195 000 characters** on execution 31027,
   past what an MCP client can ingest — the diagnostic tool broke on exactly
   the executions worth diagnosing. It attached each node's first item in full.
   Payloads are now omitted by default; `include_output=True` gives a bounded
   preview that says when it truncated.

What this surfaced immediately: *Brief quotidien direction → Matrix* had failed
every morning at 05:30 since at least 25 August, on
`Converting circular structure to JSON` in its CalDAV node. Nothing had
reported it.

## License

[Apache-2.0](../LICENSE) — © 2026 inno³ and contributors.

## Acceptance test against a live instance

[`RECETTE_COWORK.md`](RECETTE_COWORK.md) is a script to run from an MCP
client against a real instance. It is read-only except where marked, and
cleans up after itself.
