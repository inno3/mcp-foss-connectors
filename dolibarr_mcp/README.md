# Dolibarr ERP connector (`dolibarr-mcp`)

MCP server for the [Dolibarr](https://www.dolibarr.org/) ERP/CRM REST API (Dolibarr **16+**). Projects, tasks, time logging, third parties, contacts, invoices, proposals and support tickets.

Part of [**mcp-foss-connectors**](../README.md). Configured entirely through
environment variables — no host, login or secret is baked into the code.

## Environment variables

| Variable | Required | Description |
|---|---|---|
| `DOLIBARR_URL` | yes | Base URL of the Dolibarr instance, e.g. `https://erp.example.com`. No trailing slash. The REST API module must be enabled. |
| `DOLIBARR_API_KEY` | yes | API key of a Dolibarr user (sent as the `DOLAPIKEY` header). Generate it on the user record → *API key* tab. |

## Tools (48)

- `dolibarr_list_projects` — Liste les projets Dolibarr
- `dolibarr_get_project` — Détail d'un projet par ID numérique ou par référence
- `dolibarr_list_tasks` — Liste les tâches d'un projet Dolibarr
- `dolibarr_log_time` — Saisir du temps passé sur une tâche Dolibarr
- `dolibarr_list_time_entries` : Liste les écritures de temps d'une tâche ou d'un projet
- `dolibarr_update_time_entry` : Corrige une écriture de temps existante
- `dolibarr_delete_time_entry` : Supprime une écriture de temps
- `dolibarr_list_task_contacts` : Liste les intervenants affectés à une tâche
- `dolibarr_assign_task_user` : Affecte un utilisateur comme intervenant d'une tâche
- `dolibarr_unassign_task_user` : Retire un utilisateur des intervenants d'une tâche
- `dolibarr_create_task` — Crée une tâche dans un projet Dolibarr
- `dolibarr_close_task` — Marque une tâche Dolibarr comme terminée (progress=100 par défaut)
- `dolibarr_list_invoices` — Liste les factures clients
- `dolibarr_list_supplier_invoices` — Liste les factures fournisseur
- `dolibarr_list_proposals` — Liste les propositions commerciales (devis)
- `dolibarr_list_thirdparties` — Liste les tiers (clients, fournisseurs, prospects)
- `dolibarr_get_thirdparty` — Détail complet d'un tiers par son ID
- `dolibarr_add_contact` — Ajoute un contact à un tiers existant dans Dolibarr
- `dolibarr_create_thirdparty` — Crée un nouveau tiers dans Dolibarr
- `dolibarr_update_thirdparty` — Modifie un tiers existant dans Dolibarr
- `dolibarr_list_contacts` — Liste les contacts rattachés à un tiers
- `dolibarr_search_contacts` — Cherche un contact par email ou nom à travers tous les tiers
- `dolibarr_update_contact` — Modifie un contact existant dans Dolibarr
- `dolibarr_delete_contact` — Supprime un contact dans Dolibarr
- `dolibarr_list_tickets` — Liste les tickets de support
- `dolibarr_get_ticket` — Récupère le détail complet d'un ticket de support
- `dolibarr_create_ticket` — Crée un ticket de support dans Dolibarr
- `dolibarr_resolve_email` — Résout une adresse email vers un tiers et/ou contact Dolibarr
- `dolibarr_get_revenue_summary` — Synthèse du chiffre d'affaires depuis les factures Dolibarr
- `dolibarr_list_actions_open` — Liste les actions ouvertes (agenda, CR, suivi) dans Dolibarr
- `dolibarr_dashboard` — Tableau de bord de direction (synthèse multi-modules)
- `dolibarr_create_project` — Crée un nouveau projet dans Dolibarr
- `dolibarr_update_project` — Met à jour un projet existant dans Dolibarr
- `dolibarr_delete_project` — Supprime un projet Dolibarr via DELETE /api/projects/{id}
- `dolibarr_create_proposal` — Crée une proposition commerciale dans Dolibarr
- `dolibarr_add_proposal_lines` — Ajoute des lignes à une proposition commerciale Dolibarr (brouillon)
- `dolibarr_get_invoice` — Détail d'une facture client par ID numérique ou par référence
- `dolibarr_get_proposal` — Détail d'une proposition commerciale par ID numérique ou par référence
- `dolibarr_get_supplier_invoice` — Détail d'une facture FOURNISSEUR par ID numérique ou par référence
- `dolibarr_validate_invoice` — Valide une facture brouillon (statut 0 → 1) via POST /invoices/{id}/validate
- `dolibarr_update_invoice` — Met à jour une facture : projet, référence client, notes, date d'échéance
- `dolibarr_validate_proposal` — Valide une proposition commerciale brouillon via POST /proposals/{id}/validate
- `dolibarr_update_proposal` — Met à jour une proposition : projet, titre, dates, notes, réf. client
- `dolibarr_set_proposal_draft` — Repasse une proposition validée en brouillon (pour rééditer ses lignes)
- `dolibarr_delete_proposal` — Supprime une proposition commerciale
- `dolibarr_update_proposal_line` — Met à jour une ligne de proposition (brouillon uniquement)
- `dolibarr_delete_proposal_line` — Supprime une ligne de proposition (brouillon uniquement)
- `dolibarr_get_invoice_pdf_url` — Retourne l'URL Dolibarr du PDF d'une facture (lien direct pour téléchargement

## Durations are seconds, everywhere

Dolibarr stores and returns **every** duration in seconds, usually as a string
(`"117000"`). Read as hours, that is a figure 3600 times too high — and nothing
errors out. Every tool that exposes a duration therefore returns it twice: a
converted value rounded to two decimals (`spent_hours`, `planned_hours`,
`total_spent_hours`) alongside the untouched original (`spent_seconds`,
`planned_seconds`, `total_spent_seconds`). A task carrying 32.5 h reports
`spent_hours: 32.5` and `spent_seconds: 117000`.

A missing duration reports `null`, never `0` — zero would read as "no time
logged" when the information is simply absent.

In the other direction, `dolibarr_log_time` takes `duration` in **hours** and
converts it before the call (1.5 → 5400), because the API accepts seconds only.

## Time: which routes exist, and which one is broken

Several near-identical routes exist and only one accepts a `POST`; the others
answer with a bare `404` that is indistinguishable from a permission problem or
a bad id.

| Route | Verb | Status |
|---|---|---|
| `tasks/{id}/addtimespent` | `POST` | the only add route, **fatally broken on Dolibarr 23.0.x**; see below |
| `tasks/{id}/timespent` | `GET` only | works; a `POST` here returns `404` |
| `tasks/{id}/timespent/{entry_id}` | `PUT` / `DELETE` | work (Dolibarr 23+) |
| `tasks/{id}/getTimeSpent/{entry_id}` | `GET` | works (Dolibarr 23+) |
| `projects/tasks/{id}/…` | — | does not exist: the `Tasks` class is mounted at the root, never under `/projects` |

### Known upstream bug: adding time is impossible on Dolibarr 23.0.x

`POST tasks/{id}/addtimespent` returns **HTTP 500 with a zero-byte body** for
every request, whatever it contains. It is a PHP fatal, not an application
error:

```
PHP Fatal error: Uncaught TypeError: Cannot access offset of type array
  in isset or empty
  in includes/restler/framework/Luracast/Restler/Data/Validator.php:427
```

`Tasks::addTimeSpent()` declares two **union types** in its doc block:
`@param datetime|string $date` and `@param int|null $progress`. Restler's
comment parser turns a union into a PHP *array*, and `Validator.php:427` uses
that value as an array key (`isset(static::$preFilters[$info->type])`), which
PHP 8 rejects with a `TypeError`. The crash happens during **parameter
validation**, before the task is even fetched, so no request body avoids it,
and a `POST` on a task id that does not exist fails the same way instead of
returning a clean `404`. Dolibarr 22 declared `@param datetime $date` with no
union and had no `$progress` parameter: the route works there. This is a
regression introduced in 23, of the same family as
[Dolibarr#35373](https://github.com/Dolibarr/dolibarr/issues/35373) on
`/products/{id}/purchase_prices`.

Until the instance is patched, **time entry goes through the web UI**
(`projet/tasks/time.php`). Reading and correcting time are unaffected:
`dolibarr_list_time_entries`, `dolibarr_update_time_entry` and
`dolibarr_delete_time_entry` all work on 23.0.x, so a mis-keyed entry can be
repaired without reopening the interface.

`dolibarr_log_time` detects this exact signature and returns
`cause: bug_dolibarr_addtimespent` with the file and line above, rather than
sending the caller off to fix a configuration that is not at fault.

### Failure diagnosis states what was verified, and nothing more

Because a naked 404 is undebuggable, `dolibarr_log_time` names the cause
instead of forwarding the status code: `bug_dolibarr_addtimespent`,
`tache_inexistante`, `route_ou_methode_invalide`, `utilisateur_inconnu`,
`droits_insuffisants`, `parametres_refuses`, `aucun_changement`,
`erreur_interne_dolibarr`, `erreur_interne_sans_corps`. It echoes the endpoint
it used and surfaces Dolibarr's own error body, saying so explicitly
(`dolibarr_error_body_empty`) when there is none, rather than guessing.

A previous version blamed "user not assigned to the task" on any 5xx. That was
wrong: `Tasks::addTimeSpent()` checks the API key's `projet->creer` right and
the caller's access to the project, and **never** looks at task assignment.
Assignment now appears only under `checks`, and only when the connector really
looked it up:

```json
"checks": {"tache_existe": true, "utilisateur_existe": true,
           "utilisateur_affecte": false, "roles_utilisateur": []}
```

`null` means "not verified", never "false".

## Task assignment

Dolibarr stores task participants in `llx_element_contact` under the
`project_task` element, with two internal roles:

| Code | Standard label |
|---|---|
| `TASKCONTRIBUTOR` | Intervenant (contributor) |
| `TASKEXECUTIVE` | Responsable (manager) |

Always address a role by its **code**. The numeric `rowid` of a contact type is
instance-local (on one 23.0.3 instance `TASKCONTRIBUTOR`/internal is `181`,
nowhere near the value a fresh install assigns) and the label is translated and
customisable. `dolibarr_assign_task_user` validates the code against the
instance's own dictionary (`setup/dictionary/contact_types?type=project_task`)
before calling, because Dolibarr answers an unknown code with a `500` whose
message is empty.

Assignment is **not** required to log time through the API, only through the web
UI. That is precisely why it matters while the upstream bug above stands.

## Correcting a time entry: the API overwrites, it does not patch

`PUT tasks/{id}/timespent/{entry_id}` rewrites the whole row from what you send,
and its server-side defaults are destructive: an omitted `note` empties the
comment, and an omitted `user_id` reassigns the entry to user `0` (the signature
is `$user_id = 0` and the code does `$user_id ?? …`, so `0` is kept rather than
falling back to the caller). `dolibarr_update_time_entry` therefore re-reads the
entry and resends every field, replacing only what was asked for. Timestamps are
read and written back in GMT, so correcting a note does not shift the entry's
date.

## Manual integration test

The unit suite is fully offline. This end-to-end check needs a real instance and
is run by hand; it is the only way to prove the round trip against a given
Dolibarr version. Use a throwaway task, and clean up after yourself.

```sh
export DOLIBARR_URL=https://erp.example.com DOLIBARR_API_KEY=…
python - <<'PY'
import asyncio, json
from dolibarr_mcp.server import (dolibarr_assign_task_user, dolibarr_delete_time_entry,
                                 dolibarr_list_task_contacts, dolibarr_list_time_entries,
                                 dolibarr_log_time)
TASK, USER = 0, 0   # <- id d'une tâche jetable, id d'un utilisateur llx_user

async def main():
    print(await dolibarr_assign_task_user(task_id=TASK, user_id=USER))
    assert any(c["user_id"] == USER for c in
               json.loads(await dolibarr_list_task_contacts(task_id=TASK))["contacts"])

    logged = json.loads(await dolibarr_log_time(task_id=TASK, duration=1.5,
                                                user_id=USER, note="test integration"))
    if not logged.get("success"):
        # Attendu sur Dolibarr 23.0.x : la route d'ajout est cassée en amont.
        assert logged["cause"] == "bug_dolibarr_addtimespent", logged
        print("ajout indisponible sur cette version :", logged["cause"]); return

    listed = json.loads(await dolibarr_list_time_entries(task_id=TASK, user_id=USER))
    entry = next(e for e in listed["entries"] if e["note"] == "test integration")
    assert entry["seconds"] == 5400, entry     # 1,5 h stockée en secondes
    assert entry["hours"] == 1.5, entry
    print(await dolibarr_delete_time_entry(task_id=TASK, entry_id=entry["entry_id"]))

asyncio.run(main())
PY
```

Expected on a working instance: the entry reads back at **5400 seconds** and
**1.5 hours**. Expected on Dolibarr 23.0.x: `dolibarr_log_time` fails with
`cause: bug_dolibarr_addtimespent`. Assignment, reading and deletion still
pass, which is exactly the boundary of the upstream bug.

## Extending the connector

This connector is **extensible without forking**. At startup it discovers any
package that advertises a `register` callable in the `dolibarr_mcp.extensions`
[entry-point group](https://packaging.python.org/en/latest/specifications/entry-points/)
and lets it register extra `@mcp.tool()` tools on the live server. A vanilla
install ships **no** such entry point, so the core stays strictly generic.

```toml
# in your extension package's pyproject.toml
[project.entry-points."dolibarr_mcp.extensions"]
my_org = "my_org_pkg.extension:register"
```

```python
# my_org_pkg/extension.py
def register(mcp):
    mcp.tool()(my_extra_tool)
```

A bad extension can never break the core: load failures are caught and logged
to stderr, and the server keeps running with its 48 generic tools.

### inno³ extension package

Tools that depend on inno³'s **custom** Dolibarr modules — `meetingnotes`,
`supportcredits` (carnets), `inno3pilot` (boards) and the signed
`inno3dashboard` / `supportcredits` portal URLs — are published as a separate
add-on, **`inno3-mcp-extensions`** (29 tools), *not* in this repository.
Installing it next to `dolibarr-mcp` raises the tool count from 48 to 77;
uninstalling it restores the generic 48. No environment flag needed.

To ship both in a single Claude Desktop bundle, vendor the extension at build
time — pip installs it *with* its `.dist-info`, without which the entry point is
invisible and the extra tools silently absent:

```sh
python scripts/build_mcpb.py dolibarr --with-extension ../inno3-mcp-extensions
```

**Updating an already-installed bundle.** A Desktop extension is identified by
`<publisher>.<manifest name>` — that key indexes both its install directory and
its settings file (URLs, secrets). Publishing under a different `name` therefore
installs a *second* extension side by side and does not carry the configuration
over. To update in place, republish under the name already installed, without
touching the repository's generic manifest:

```sh
python scripts/build_mcpb.py dolibarr --with-extension ../inno3-mcp-extensions \
  --bundle-name my-org-dolibarr --display-name "Dolibarr ERP (my org)"
```

## License

[Apache-2.0](../LICENSE) — © 2026 inno³ and contributors.
