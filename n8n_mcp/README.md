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
- `n8n_get_executions` — Récupère les exécutions récentes d'un workflow ou de toute l'instance (`exclude_workflow_ids`, `one_per_workflow` pour balayer)
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

## Counts that stop lying when you change `limit` (1.2.1)

Four defects found during the 1.2.0 acceptance test against the live instance
on 01 and 02/09/2026. The first three are silent losses — nothing in the
response said the number was wrong.

1. **`n8n_list_failures` truncated at `limit` without saying so.** On the same
   168 h window, `limit=50` returned `36 / 9 / 5` per workflow and `limit=60`
   returned `45 / 10 / 5`. A count that moves with `limit` is not a total.
   `by_workflow` and the new `total_failures` now cover the whole window: the
   history is paged through with `nextCursor` until the window's bound is
   crossed. `limit` still caps the detail in `executions` — and the response
   says so in `truncation_note`. When the page guard (`EXEC_MAX_PAGES`) stops
   the walk first, `window_complete` is `false` and `oldest_seen_at` says how
   far back it actually got.
2. **`hours` was not a window.** This instance rejects date bounds on
   `/executions` (HTTP 400), so the filter only ever applied to the page that
   had been fetched: `hours=168` really meant "the last N failures, then
   filtered by date". The walk now pages back to the bound client-side. The
   limitation itself cannot be lifted — it is the instance's — so it is stated
   in the docstrings of both `n8n_list_failures` and `n8n_get_executions`.
3. **`consecutive_failures` was capped at `per_workflow`.** Workflow
   `esFmnG1NIMOQD2C0` reported 5 where the real value was 9: a nine-day outage
   read as a five-day one, and it is the age of a failure that sets its
   severity. When the first pass shows nothing but failures, the history is now
   walked back to the last success, up to `HEALTH_MAX_DEPTH`. `inspected` and
   `oldest_failure_at` say what was really read, and `last_success_at: null` is
   disambiguated by `never_succeeded` (no success in the retained history)
   versus `last_success_unknown` (last success beyond the depth read).
   `per_workflow` is now the size of the first pass, not a ceiling.
4. **No way to set aside a workflow that floods the history** (functional
   extension, not a fix). One workflow triggered every two minutes took 13 of
   15 rows: `n8n_get_executions` without `workflow_id` returned one workflow's
   history, not a view of the instance. `exclude_workflow_ids` (comma
   separated) and `one_per_workflow` page until they have `limit` rows, and
   report `pages_scanned` — plus a `sweep_note` when the page guard stopped
   them early.

Response schemas are unchanged: every field above is additive.

## When a call fails, what the message tells you (1.3.0)

The same error contract as the Dolibarr and Kanboard connectors, which was
written after both answered `Erreur : ` — nothing else — in production on
08/09/2026 and 24/09/2026. An empty string separates nothing: a refused API key,
a missing workflow, a malformed node list and a passing network fault all read
alike, and the caller cannot tell whether to replay, fix the call or stop. Five
things now hold.

**No message is ever bare.** An httpx exception carrying no text (`ReadError`,
a naked `ReadTimeout`, `RemoteProtocolError`) is rendered with its type and what
that family means — `Erreur réseau ReadError : connexion coupée par le serveur
avant la réponse.` Any other empty exception keeps at least its class name.

**The whole `httpx.TransportError` family is handled.** Only `ConnectError` and
the two timeouts used to be caught; a `ReadError`, a `PoolTimeout` or a
`RemoteProtocolError` went through with neither replay nor wrapping. A non-JSON
body served with HTTP 200, which is how a proxy error page arrives, is now
reported with an excerpt instead of a parser message, and replayed like a read.

**The API's error code is rendered.** The HTTP status keeps its own message and
n8n's own body is quoted rather than dropped — `Erreur 400 : requête invalide.
Détail : request/body must have required property 'nodes'`.

**A write is never replayed once the request has left.** This matters more here
than elsewhere: `PUT /workflows/{id}` rewrites the whole definition, so replaying
it overwrites a version the caller still believes is in place. Reads (`GET`,
`HEAD`, `OPTIONS`) are replayed on any transport error, on 5xx and on an
unreadable body. Writes are replayed only when the connection never succeeded
(`ConnectError`, `ConnectTimeout`), which is the only proof n8n received nothing.

**The error says what became of the write.** `write_state` is `sans_objet` for a
read, `non_appliquee` when the request never reached n8n or n8n refused it before
executing it (HTTP 4xx) — replaying is then free of any overwrite or duplicate
risk — and `inconnu` when the request left and the answer was lost (read timeout,
5xx, unreadable body): the message then asks you to re-read the object before
replaying. A 401, 403 or 404 writes nothing and keeps its usual message. Writes
already applied earlier in the same tool call are named — several tools chain a
rewrite and an (de)activation, and knowing which half ran saves a guess.

The limit that remains: a write whose response is lost leaves a state the client
cannot decide. The public API exposes neither an idempotency key nor a request id
to check against, so `inconnu` means exactly that — re-read the workflow or the
execution, do not replay blind.

## License

[Apache-2.0](../LICENSE) — © 2026 inno³ and contributors.

## Acceptance test against a live instance

[`RECETTE_COWORK.md`](RECETTE_COWORK.md) is a script to run from an MCP
client against a real instance. It is read-only except where marked, and
cleans up after itself.
