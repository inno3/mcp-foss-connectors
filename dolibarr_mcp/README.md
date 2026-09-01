# Dolibarr ERP connector (`dolibarr-mcp`)

MCP server for the [Dolibarr](https://www.dolibarr.org/) ERP/CRM REST API (Dolibarr **16+**). Projects, tasks, time logging, third parties, contacts, customer and supplier invoicing, proposals, bank accounts, payment settlement, cash forecasting, attachments and support tickets.

Part of [**mcp-foss-connectors**](../README.md). Configured entirely through
environment variables — no host, login or secret is baked into the code.

## Environment variables

| Variable | Required | Description |
|---|---|---|
| `DOLIBARR_URL` | yes | Base URL of the Dolibarr instance, e.g. `https://erp.example.com`. No trailing slash. The REST API module must be enabled. |
| `DOLIBARR_API_KEY` | yes | API key of a Dolibarr user (sent as the `DOLAPIKEY` header). Generate it on the user record → *API key* tab. |
| `DOLIBARR_WEB_LOGIN` | no | Login used **only** for the writes the REST API does not expose — today, bank reconciliation alone. Leave unset to disable that fallback. |
| `DOLIBARR_WEB_PASSWORD` | no | Password for the account above. Needs the *bank → reconcile* permission. |

## Tools (74)

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
- `dolibarr_close_ticket` — Ferme (ou rouvre) un ticket avec message de clôture optionnel, via PUT /tickets/{id}
- `dolibarr_add_ticket_message` — Ajoute un message au fil d'un ticket via POST /tickets/newmessage
- `dolibarr_update_ticket` — Met à jour un ticket : sujet, sévérité, type, rattachements, assignation
- `dolibarr_delete_ticket` — Supprime définitivement un ticket (irréversible)
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
- `dolibarr_list_bank_accounts` — Liste les comptes bancaires, solde et indicateur de rapprochement
- `dolibarr_list_bank_entries` — Écritures d'un compte sur une période, solde cumulé compris
- `dolibarr_get_bank_balance` — Solde d'un compte à aujourd'hui ou à une date d'arrêté
- `dolibarr_create_bank_entry` — Crée une opération diverse (sens explicite : `credit` / `debit`)
- `dolibarr_set_bank_statement` — Affecte un numéro de relevé à un lot d'écritures et les rapproche
- `dolibarr_toggle_bank_reconciliation` — Active ou désactive le rapprochement d'un compte
- `dolibarr_list_invoice_payments` — Règlements d'une facture et leur écriture bancaire
- `dolibarr_add_invoice_payment` — Enregistre un règlement client et l'écriture bancaire
- `dolibarr_add_supplier_invoice_payment` — Enregistre un règlement fournisseur et l'écriture bancaire
- `dolibarr_create_supplier_invoice` — Crée une facture fournisseur (brouillon)
- `dolibarr_create_invoice` — Crée une facture client (brouillon) avec ses lignes
- `dolibarr_add_invoice_lines` — Ajoute des lignes à une facture brouillon
- `dolibarr_delete_invoice_line` — Supprime une ligne d'une facture brouillon
- `dolibarr_create_invoice_from_proposal` — Facture une proposition commerciale
- `dolibarr_list_documents` — Liste les pièces jointes d'un objet
- `dolibarr_upload_document` — Attache un fichier local à un objet
- `dolibarr_build_document` — (Re)génère le PDF d'un document commercial
- `dolibarr_list_users` — Liste les utilisateurs et leurs identifiants internes
- `dolibarr_list_products` — Liste les produits et services du catalogue
- `dolibarr_list_payment_types` — Liste les modes de règlement actifs
- `dolibarr_cash_forecast` — Prévisionnel de trésorerie (soldes + échéances)
- `dolibarr_transfer_between_accounts` — Virement interne entre deux comptes
- `dolibarr_list_bank_statements` — Relevés posés et écritures restant à rapprocher
- `dolibarr_delete_bank_entry` — Supprime une écriture isolée (non rapprochée, sans règlement)
- `dolibarr_delete_invoice` — Supprime une facture brouillon, client ou fournisseur
- `dolibarr_validate_supplier_invoice` — Valide une facture fournisseur brouillon

## Invoicing a proposal: Dolibarr has no such route

`POST /invoices/createfromorder/{orderid}` and `/createfromcontract/{contractid}`
exist; there is **nothing** for a proposal. For a consultancy that quotes and
then bills directly, that is the missing half of the workflow.

`dolibarr_create_invoice_from_proposal` re-reads the proposal and recreates the
invoice client-side: same third party, project, customer reference, and one
`POST /invoices/{id}/lines` per proposal line. Two consequences it states
plainly instead of hiding:

- lines are **recreated, not moved** — the tool compares the resulting invoice
  total against the proposal total and raises `total_warning` if they diverge,
  and reports `success: false` if any line was refused, so a half-copied draft
  never reads as done;
- the link back to the proposal is posted through `linked_objects`, which
  `Facture::create()` turns into a real `add_object_linked('propal', …)` — the
  invoice shows up under the proposal's *Linked objects* tab.

The proposal's own status is **left alone** unless `mark_proposal_invoiced=True`.
Dolibarr's UI flips it when you bill; a connector should not change state the
caller did not ask for.

## Line payloads: absent, or present and null

Both `Invoices::postLine()` and `SupplierInvoices::postLine()` dereference every
field as an object property with no `isset()` guard. A missing key reaches
`addline()` as null and the line is created **truncated**, with no error. The
connector therefore always sends the full field set, taken from the upstream
docblock examples.

The mirror-image trap bit during development: a line copied from another
document carries `tva_tx: None` — the key is *present*, holding null. Reading it
with `float(...)` blew up the whole line, and `create_invoice_from_proposal`
produced an **empty invoice plus a warning**. `_line_number()` now treats a null
value as an absent one. A test pins that behaviour.

## Money in, money out: one sign, three spellings

`llx_bank.amount` is **signed**: positive credits the account (money in),
negative debits it (money out). Dolibarr's own *various payment* form hides that
behind a `sens` field where **1 means Crédit** and **0 means Débit** — the
opposite of the intuition that 0 comes first — and `PaymentVarious::create()`
turns it back into `sign = ($sens == '0' ? -1 : 1)`.

`dolibarr_create_bank_entry` refuses to replay that trap. It takes a **positive**
amount plus an explicit `direction` (`credit` / `debit`), rejects a negative
amount outright rather than risk a double negation, and returns the account
balance **before and after** the write so a sign error shows up in the response
instead of in next month's reconciliation.

The same reasoning drives the payment tools: each returns `balance_delta` next
to `expected_delta` (positive for a customer payment, negative for a supplier
one) and raises `balance_warning` when the two disagree.

## What the REST API does not expose

Three gaps are worth knowing about, because the tools work around them and say
so in their responses rather than pretending otherwise:

1. **Customer payment amounts.** `POST /invoices/{id}/payments` ignores any
   amount and always settles the full remaining balance — partial payments are
   impossible through it. `dolibarr_add_invoice_payment` therefore posts to
   `POST /invoices/paymentsdistributed`, the only route that accepts an explicit
   amount. The supplier route, oddly, *does* take an `amount`, and is used as is.
2. **Bank reconciliation.** `PUT /bankaccounts/{id}/lines/{line_id}` only writes
   the label; `num_releve` and `rappro` are reachable solely through
   `AccountLine::update_conciliation()`, i.e. through the web form.
   `dolibarr_set_bank_statement` falls back to that form, but only when
   `DOLIBARR_WEB_LOGIN`/`DOLIBARR_WEB_PASSWORD` are set — and it **re-reads every
   entry over REST afterwards** to confirm the statement number landed. A form
   that answers 200 without writing is reported as a failure, never as a success.
3. **Paginating bank lines.** `GET /bankaccounts/{id}/lines` accepts neither
   `limit` nor `page`, and instantiates one `AccountLine` per row, so an
   unbounded query walks the whole account history. `dolibarr_list_bank_entries`
   applies a 90-day window when no `date_start` is given, and returns the window
   it used in `date_start` / `date_start_note`.

One more trap in the same family: the criteria passed to that endpoint must use
**bare column names** (`(dateo:>=:'2026-08-01')`). The query is built on
`FROM llx_bank` with no alias, so the `t.` prefix used everywhere else in this
server produces an *unknown table* error and an HTTP 503.

## Cash forecast

`dolibarr_cash_forecast` is the one synthesis this connector did not let you
build: open non-cash account balances, plus validated-unpaid customer invoices
(what comes in), minus validated-unpaid supplier invoices (what goes out),
bucketed by due date — overdue, 0-30, 31-60, 61-90, beyond — and a projected
balance at the horizon.

Two honest limits, both stated in the response:

- it is a **due-date projection**, not a commitment: each invoice is counted on
  its due date, and overdue ones count immediately;
- the amount used is `remaintopay` when Dolibarr provides it, the TTC total
  otherwise — a partly-settled invoice must not be forecast in full.

## Credit notes are negative, and that broke two things

A Dolibarr credit note (`AV…`) is an invoice with a **negative** total. Two
defects in 1.6.0 came from forgetting that, both found during the 31/08/2026
acceptance run against production:

- `list_invoices` compared `total_ttc < min_amount` with `min_amount` defaulting
  to **0**, so every credit note was silently dropped — 13 of the last 100
  invoices on the inno³ instance. `min_amount` now filters only when it is
  actually given, and the response carries `credit_notes_count`.
- `get_revenue_summary` was *correct* but unreadable: August 2026 showed
  6 350 € against an apparent 52 400 €, because FA2608-0940 (46 050 €) was
  issued and credited the same day by AV2608-0651 and AV2608-0652. The net
  figure was right; nothing said so. The response now breaks out
  `month_gross_ht`, `month_credit_notes_ht` and `month_nb_credit_notes`, so
  gross + credit notes = net can be checked at a glance.

The lesson generalises: a filter whose neutral value is `0` silently excludes
negative data. Neutral must mean *absent*, not *zero*.

## Revenue: which figure are you looking at?

`dolibarr_get_revenue_summary` takes a `basis` parameter and always echoes it
back, because the two answers are both legitimate and are not comparable:

- `emises` (default) — every **validated** invoice, paid or not, counted at its
  invoice date. This is commercial revenue: an invoice issued this month counts
  this month.
- `encaissees` — the customer payments actually **seen on a bank account** over
  the period, at their operation date, VAT included.

Until v1.4 the summary pulled 500 invoices sorted by `rowid` ascending with no
date filter. Past 500 validated invoices on the instance, that returned the
**oldest** ones and the current month quietly collapsed to zero. The scan is now
bounded by the requested year and paged to the end.

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

## Creating a task: three fields the API validates before anything else

`POST tasks` runs `_validate()` first, and it demands **`ref`, `label` and
`fk_project`**. Two traps follow from that, both of which made every creation
fail with a bare `400`:

- The API can number a task itself, but only if asked. The auto-generation
  block runs *after* validation and only fires on the sentinel value, so
  omitting `ref` returns `400 ref field missing` even though the instance has a
  numbering module. `dolibarr_create_task` therefore sends `ref: "auto"` by
  default, which yields a reference like `TK2608-0259` from
  `PROJECT_TASK_ADDON`; pass `ref` explicitly to force your own.
- The field is **`fk_project`**, not `fk_projet`. The latter is the SQL column
  name, and `Task::create()` does not read it, so it would silently produce a
  task attached to no project if validation ever let it through.

This trio is unchanged from Dolibarr 16 to 23. Since the API returns only the
new id, the tool re-reads the task to report the reference the numbering module
actually assigned; a failed re-read leaves `ref` null rather than turning a
successful creation into a reported failure.

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

### Bank round trip: balance before, balance after

Same rules — real instance, run by hand. This one is worth running on every new
Dolibarr version, because a sign inversion on a bank entry produces no error at
all: it produces a wrong balance.

```sh
export DOLIBARR_URL=https://erp.example.com DOLIBARR_API_KEY=…
python - <<'PY'
import asyncio, json
from dolibarr_mcp.server import (dolibarr_create_bank_entry, dolibarr_get_bank_balance,
                                 dolibarr_list_bank_accounts, dolibarr_list_bank_entries)
ACCOUNT = 0   # <- id d'un compte de test, JAMAIS un compte de production

async def main():
    accounts = json.loads(await dolibarr_list_bank_accounts())
    account = next(a for a in accounts["accounts"] if a["id"] == ACCOUNT)
    print("compte :", account["label"], account["balance"], "rapprochable :", account["reconcilable"])
    before = json.loads(await dolibarr_get_bank_balance(account_id=ACCOUNT))["balance"]

    credit = json.loads(await dolibarr_create_bank_entry(
        account_id=ACCOUNT, date="2026-08-18", label="TEST credit",
        amount=100, direction="credit"))
    assert credit["amount_signed"] == 100, credit          # entrée d'argent = positif
    assert credit["balance_delta"] == 100, credit
    assert credit["entry"]["credit"] == 100, credit

    debit = json.loads(await dolibarr_create_bank_entry(
        account_id=ACCOUNT, date="2026-08-18", label="TEST debit",
        amount=40, direction="debit"))
    assert debit["amount_signed"] == -40, debit            # sortie = négatif
    assert debit["balance_delta"] == -40, debit
    assert debit["entry"]["debit"] == 40, debit

    after = json.loads(await dolibarr_get_bank_balance(account_id=ACCOUNT))["balance"]
    assert round(after - before, 2) == 60, (before, after)

    listed = json.loads(await dolibarr_list_bank_entries(
        account_id=ACCOUNT, date_start="2026-08-01", date_end="2026-08-31"))
    entry = next(e for e in listed["entries"] if e["id"] == debit["entry_id"])
    assert entry["balance_after"] == after, entry          # solde cumulé = solde réel

    print("OK — supprimer les deux écritures de test dans Dolibarr.")

asyncio.run(main())
PY
```

Expected: `+100` then `-40`, a balance that moved by exactly `+60`, and a
running balance on the last entry equal to the account balance. Delete both
test entries afterwards with `dolibarr_delete_bank_entry`, which refuses any
entry that is already reconciled or that carries a payment link — deleting the
bank line of a settled invoice would leave the payment orphaned.

The payment tools follow the same contract on a throwaway invoice:
`dolibarr_add_invoice_payment` must report `balance_delta == expected_delta`
(positive), `dolibarr_add_supplier_invoice_payment` the same with a **negative**
`expected_delta`, and both must list the new payment under `created_payments`
with a `bank_line_id`.

## Acceptance test against a live instance

[`RECETTE_COWORK.md`](RECETTE_COWORK.md) is a script to run from an MCP client
against a real Dolibarr. It starts with a version check — three acceptance runs
were spent chasing "defects" that were only stale deployments — then walks
banking, settlement, invoicing and attachments with expected values measured on
the live instance. It validates no invoice, so it burns no accounting sequence
number. It needs a throwaway bank account and third party, keeps every amount
under 10 €, and cleans up after itself — flagging what it could not remove.

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
