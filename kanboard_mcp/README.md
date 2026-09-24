# Kanboard connector (`kanboard-mcp`)

MCP server for the [Kanboard](https://kanboard.org/) JSON-RPC API. Projects, boards, tasks, columns, swimlanes, categories, subtasks and comments, with optional **dual-account** support (a human account + a dedicated AI-agent account).

Part of [**mcp-foss-connectors**](../README.md). Configured entirely through
environment variables — no host, login or secret is baked into the code.

## Environment variables

| Variable | Required | Description |
|---|---|---|
| `KANBOARD_URL` | yes | URL of the JSON-RPC endpoint, e.g. `https://kanboard.example.com/jsonrpc.php`. |
| `KANBOARD_USER` | yes | Login of the primary (human) account. Used by default for reads. |
| `KANBOARD_TOKEN` | yes | Personal API token of the primary account (Kanboard → Profile → API). |
| `KANBOARD_USER_ALT` | no | Login of a secondary account dedicated to an AI agent. Empty → falls back to the primary account. |
| `KANBOARD_TOKEN_ALT` | no | Personal API token of the agent account. |
| `KANBOARD_DEFAULT_AS_USER` | no | `primary` or `agent`: which account is used when a tool's `as_user` argument is empty. Default `primary`. |

## Tools (53)

- `kanboard_my_dashboard` — Tableau de bord personnel Kanboard
- `kanboard_list_projects` — Liste tous les projets Kanboard accessibles
- `kanboard_list_tasks` — Liste les taches d'un projet Kanboard
- `kanboard_get_task` — Detail complet d'une tache par son ID
- `kanboard_search_tasks` — Recherche de taches par mot-cle dans tous les projets accessibles
- `kanboard_my_overdue` — Liste mes taches en retard dans tous les projets
- `kanboard_get_board` — Etat du tableau Kanboard d'un projet
- `kanboard_move_task` — Deplace une tache vers une colonne du tableau
- `kanboard_update_task` — Modifie une tache existante dans Kanboard
- `kanboard_assign_task` — Assigne (ou desassigne) le porteur d'une tache existante
- `kanboard_add_comment` — Ajoute un commentaire a une tache Kanboard
- `kanboard_list_comments` — Liste tous les commentaires d'une tache, contenu integral
- `kanboard_get_comment` — Detail d'un commentaire par son ID, contenu integral
- `kanboard_update_comment` — Modifie le texte d'un commentaire existant
- `kanboard_remove_comment` — Supprime definitivement un commentaire
- `kanboard_close_task` — Ferme (clôture) une tâche dans Kanboard
- `kanboard_open_task` — Rouvre une tâche dans Kanboard
- `kanboard_create_task` — Cree une nouvelle tache dans un projet Kanboard
- `kanboard_delete_task` — Supprime definitivement une tache Kanboard
- `kanboard_create_project` — Cree un nouveau projet Kanboard
- `kanboard_get_project_metadata` — Detail complet d'un projet Kanboard (description, dates, owner...)
- `kanboard_list_columns` — Liste les colonnes d'un projet Kanboard
- `kanboard_create_column` — Cree une nouvelle colonne dans un projet Kanboard
- `kanboard_update_column` — Modifie une colonne existante
- `kanboard_remove_column` — Supprime definitivement une colonne d'un projet
- `kanboard_change_column_position` — Change la position d'une colonne dans un projet (1-based)
- `kanboard_list_swimlanes` — Liste les swimlanes d'un projet Kanboard
- `kanboard_create_swimlane` — Cree un nouveau swimlane dans un projet
- `kanboard_update_swimlane` — Modifie un swimlane existant
- `kanboard_remove_swimlane` — Supprime definitivement un swimlane
- `kanboard_list_categories` — Liste les categories d'un projet Kanboard
- `kanboard_create_category` — Cree une categorie (tag structure) pour un projet
- `kanboard_update_category` — Modifie une categorie existante
- `kanboard_remove_category` — Supprime definitivement une categorie
- `kanboard_recent_activity` — Activite recente d'un projet (creations, mouvements, commentaires...)
- `kanboard_delete_project` — Supprime definitivement un projet Kanboard
- `kanboard_list_project_users` — Liste les utilisateurs assignes a un projet
- `kanboard_assign_project_user` — Assigne un utilisateur a un projet avec un role
- `kanboard_remove_project_user` — Retire un utilisateur d'un projet
- `kanboard_disable_swimlane` — Desactive un swimlane (soft-delete : la lane reste mais devient invisible)
- `kanboard_enable_swimlane` — Reactive un swimlane precedemment desactive
- `kanboard_change_swimlane_position` — Change la position d'un swimlane dans un projet (1-based)
- `kanboard_create_subtask` — Cree une sous-tache pour une tache existante
- `kanboard_update_subtask` — Modifie une sous-tache existante
- `kanboard_delete_subtask` — Supprime definitivement une sous-tache
- `kanboard_toggle_subtask_status` — Bascule le statut d'une sous-tache (cycle todo -> in_progress -> done -> todo)
- `kanboard_list_link_types` — Liste les types de liens entre taches configures sur l'instance
- `kanboard_list_task_links` — Liste les liens d'une tache, avec titre et statut des taches liees
- `kanboard_create_task_link` — Cree un lien entre deux taches (jalon, blocage...)
- `kanboard_remove_task_link` — Supprime un lien entre deux taches
- `kanboard_check_project_access` — Diagnostic : quels projets chaque compte configure peut-il atteindre
- `kanboard_who_am_i` — Diagnostic : affiche l'identite de l'utilisateur authentifie pour un compte
- `kanboard_list_accounts` — Liste les comptes Kanboard configures sur ce MCP (multi-compte)

## A page that did not say it was a page

`kanboard_list_tasks` returned a bare JSON list, cut at `limit` with nothing
saying it had been cut. Measured on the inno³ instance on 02/09/2026: project 29
holds 109 closed cards; `status=0, limit=100` returned 100 of them, and the
response was indistinguishable from a project that holds exactly 100. The 9
others were reachable by no parameter at all — the tool had no `offset`.

The response is now the same envelope as `kanboard_list_projects`:
`total_available` counted *before* the cut, `offset`, `count`, `has_more`, and
`next_offset` — the value to pass next — present only when there is a next page.
An empty project returns that envelope too, rather than `[]`.

Two limits stay, and are stated in the docstring:

- **`getAllTasks` does not paginate server-side.** The connector always receives
  every task of the project and slices afterwards. `offset` saves the label
  resolution — one call per rendered task, for column, swimlane, project,
  category and assignee — not the JSON-RPC call itself.
- **The order is Kanboard's**, by column then position, not a guaranteed stable
  sort. A card moved between two calls changes rank, so paginating a board that
  is being reshuffled can skip or repeat a line.

## A sweep that stopped early, and did not push back

Kanboard has no global search in JSON-RPC, so the connector walks projects one
by one. It stopped as soon as `limit` results were found — which biases the
answer towards low-numbered projects and leaves the rest unread. Measured on the
inno³ instance on 31/08/2026: a search at `limit=20` stopped at project 26 of
65. The response did report `projects_scanned`, but a caller reading `count: 0`
had nothing telling them what to do about it.

`scan_all=True` now walks every project and reports `total_matches`, the real
count, while the returned list stays capped. When the sweep does stop early, a
`scan_warning` says so in words and names the remedy.

## Names, not identifiers

Creating a task needed the numeric ids of its column, swimlane and category —
so three lookups before every creation. Across the measured sessions,
`list_swimlanes`, `list_columns` and `list_categories` accounted for 31 calls
against 25 task creations.

`column_name` already existed; `swimlane_name` and `category_name` now join it.
Identifiers still work, and passing both an id and a name for the same thing is
refused rather than silently resolved one way. An unknown name fails the
creation and enumerates what exists — better than a task filed in the wrong
swimlane.

### A name that went through HTML

A name rarely arrives as Kanboard stores it. Copied out of a meeting note, a
recopied page or an export, `Missions & Hermine` comes back as
`Missions &amp; Hermine`, and a plain space as `&nbsp;`. Compared raw, that name
matched nothing, and the refusal listed the available values next to it — two
strings that read as identical, differing only by `&amp;` against `&`. The
caller re-read the list, saw no gap, and replayed the same call.

Every name-to-id resolution — column, swimlane, category in
`kanboard_create_task` and `kanboard_move_task`, and the member name in
`kanboard_assign_task` — now compares on a folded key: HTML entities decoded,
Unicode normalised to NFC, runs of whitespace (non-breaking space included)
reduced to one, leading and trailing spaces dropped, case folded. The same fold
backs the `name_filter` of `kanboard_list_projects`.

Three limits are deliberate:

- **Accents are not folded.** `Reunion` does not resolve `Réunion`; two distinct
  labels stay distinct. The gap is only named in the error message.
- **An exact name wins.** A row matching the way it always did is picked before
  any row that only matches after folding, so nothing that resolved before
  resolves elsewhere now.
- **Folding may not arbitrate.** If the fold alone makes two rows match, the
  call is refused and asks for the identifier, rather than writing into a
  swimlane nobody asked for.

A name that still resolves to nothing gets the available values *and* the
closest one, saying when the gap is only accents, case or typographic variants.

## Multi-account model

Write tools accept an `as_user` argument (`primary` | `agent`). This lets a human
and an AI agent share the same MCP server while keeping a correct authorship
trail on comments and task changes. If the agent account is not configured, all
calls transparently fall back to the primary account.

### Editing and deleting comments: authorship applies

Kanboard enforces an author check on `updateComment` and `removeComment`: unless
the account is an administrator, it can only edit or delete **its own** comments.
With two accounts configured, a comment posted as `primary` is therefore not
editable as `agent`, and vice versa. The API answers a bare `false` in that case;
`kanboard_update_comment` and `kanboard_remove_comment` turn it into an explicit
message naming the comment's author and the `as_user` value to retry with.

### A bad id and a real refusal look identical

Kanboard answers **403 rather than an empty result** when a comment or task id
does not exist — its authorization layer cannot resolve the object's project, so
it denies (verified on 1.2.53 with a deliberately absurd id). A bare "insufficient
permissions" would send you hunting for a rights problem that isn't there, and is
in any case indistinguishable from a genuine one. All four comment tools return
`not_found_or_forbidden` naming both causes, and point at
`kanboard_check_project_access` to tell them apart.

Comments are not versioned by Kanboard. Both tools re-read the comment before
writing and return the previous text (`previous_comment` / `deleted_comment`),
which is the only remaining trace of it. To fetch a full comment before rewriting
it, use `kanboard_list_comments` or `kanboard_get_comment` — `kanboard_get_task`
previews comments at 500 characters and flags the cut with `comment_truncated` /
`comment_full_length` (or pass `full_comments=True`).

### Admin rights change what an account can see

`getAllProjects` is **administrator-only**. A non-admin agent account gets a bare
403 on it, which used to break `kanboard_my_dashboard` and `kanboard_my_overdue`
outright under `as_user="agent"`. Those tools now fall back to `getMyProjects`
(projects the account is a member of) and `kanboard_list_projects` reports which
of the two applied through a `scope` field: `all` for the whole instance,
`member` for the narrower set — the difference matters, because a `member` list
is not proof that a project does not exist.

`kanboard_my_overdue` no longer sweeps every project: it calls `getOverdueTasks`
once for the whole instance and filters on `owner_id` client-side, since that
procedure returns everybody's overdue tasks. Where the account may not call it,
the tool falls back to the project sweep and says so through `source`
(`getOverdueTasks` or `scan_projets`). The fast path carries no column,
swimlane, category or priority — `getOverdueTasks` does not return them, and
resolving them would cost one call per task.

Run `kanboard_check_project_access` before writing to find out which account can
reach a given project, rather than discovering it as a 403 at post time. Without
arguments it reports the gap between the two accounts; with `project_id` it
answers directly with `writable_by`.

`kanboard_search_tasks` reports the same `scope`, plus `projects_scanned` /
`projects_total` — Kanboard has no global search, so the connector sweeps
projects one by one and stops at `limit`. An empty result is not proof of
absence while `projects_scanned` is below `projects_total`.

## Known API ceiling: project activity

Kanboard caps `getProjectActivity` at **50 events** and exposes no way to raise
it — the procedure takes only `project_id`, and passing a `limit` is rejected
with JSON-RPC `-32602 Too many arguments` (verified against Kanboard 1.2.53).
The cap is **global, not per project**: asking for several projects at once still
yields 50 events in total, so a quiet project can be crowded out entirely by a
busy one.

`kanboard_recent_activity` therefore reports `api_cap_reached` along with
`oldest_available` / `newest_available` instead of returning a full-looking list.
Its `limit` argument can only narrow that window, never widen it. Because
`since_iso` filters *after* the cap, asking for a date older than the window
cannot bring back the missing history; that case is flagged with
`window_incomplete`.

## Task links: three traps

Kanboard models milestones as **link types between tasks**, so attaching a task
to a milestone means creating a link — but three details bite.

**Link type ids are instance-specific.** They depend on the instance language
and on any customisation, so a hardcoded id silently creates the wrong relation
on the next instance. Always resolve the id from `kanboard_list_link_types`,
which also returns `opposite_label` so the pair reads correctly. A type with
`opposite_id: null` is symmetric — it is its own opposite.

**The reverse link is created for you.** `kanboard_create_task_link` produces
the relation on *both* tasks in a single call. Seeing it appear on the opposite
task is not a duplicate, and calling the tool again in the other direction is
wrong.

**`getAllTaskLinks` returns a misleading `task_id`.** Kanboard aliases
`opposite_task_id AS task_id`, so that column is the *linked* task, never the
one you asked about. `kanboard_list_task_links` re-exposes it as
`linked_task_id`, and surfaces the link row's own id as `task_link_id` — that
last one is what `kanboard_remove_task_link` expects. Passing a task id there
would target an unrelated link.

## When a call fails, what the message tells you

On 24/09/2026 the connector answered `Erreur : ` — nothing else — twice in one
session, once on `kanboard_add_comment` and once on `kanboard_get_task`; both
calls succeeded when replayed unchanged. An empty string separates nothing: a
refused authentication, a missing object, an oversized payload and a passing
network fault all read alike, and the caller cannot tell whether to replay, fix
the call or stop. Four things now hold.

**No message is ever bare.** An httpx exception carrying no text (`ReadError`,
a naked `ReadTimeout`, `RemoteProtocolError`) is rendered with its type and what
that family means — `Erreur réseau ReadError : connexion coupée par le serveur
avant la réponse.` Any other empty exception keeps at least its class name. A
non-JSON body served with HTTP 200, which is how a proxy error page arrives, is
reported as such with an excerpt instead of a parser message.

**The JSON-RPC error code is no longer dropped.** Kanboard's `code` used to be
discarded in favour of `message` alone; it is now rendered with its meaning —
`Kanboard API error -32602 (paramètres invalides) sur getProjectActivity`.

**A write is never replayed once the request has left.** Reads are replayed on
any transport error, on 5xx and on an unreadable body. Writes are replayed only
when the connection never succeeded (`ConnectError`, `ConnectTimeout`), which is
the only proof Kanboard received nothing. Before this, a `createComment` losing
its response was replayed twice and could post the comment three times.

**The error says what became of the write.** `write_state` is `sans_objet` for a
read, `non_appliquee` when the request never reached Kanboard or Kanboard refused
it before executing it (JSON-RPC -32700/-32600/-32601/-32602) — replaying is then
free of any duplicate risk — and `inconnu` when the request left and the answer
was lost (read timeout, 5xx, -32603): the message then asks you to re-read the
object before replaying. A 401 or 403 writes nothing and keeps its usual message.

**Writes already applied earlier in the same tool call are named.** Several tools
chain writes — create then categorise, add then move. When one of them has
succeeded before the failure, the message lists it, so a caller knows what to
re-read rather than guessing which half of the tool ran.

The limit that remains: a write whose response is lost leaves a state the client
cannot decide. Kanboard exposes neither an idempotency key nor a request id to
check against, so `inconnu` means exactly that — re-read the object, do not
replay blind.

## License

[Apache-2.0](../LICENSE) — © 2026 inno³ and contributors.

## Acceptance test against a live instance

[`RECETTE_COWORK.md`](RECETTE_COWORK.md) is a script to run from an MCP
client against a real instance. It is read-only except where marked, and
cleans up after itself.
