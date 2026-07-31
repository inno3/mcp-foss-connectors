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

## Tools (49)

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
- `kanboard_check_project_access` — Diagnostic : quels projets chaque compte configure peut-il atteindre
- `kanboard_who_am_i` — Diagnostic : affiche l'identite de l'utilisateur authentifie pour un compte
- `kanboard_list_accounts` — Liste les comptes Kanboard configures sur ce MCP (multi-compte)

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

## License

[Apache-2.0](../LICENSE) — © 2026 inno³ and contributors.
