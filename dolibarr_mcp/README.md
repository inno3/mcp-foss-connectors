# Dolibarr ERP connector (`dolibarr-mcp`)

MCP server for the [Dolibarr](https://www.dolibarr.org/) ERP/CRM REST API (Dolibarr **16+**). Projects, tasks, time logging, third parties, contacts, customer and supplier invoicing, proposals, bank accounts, payment settlement, cash forecasting, attachments and support tickets.

Part of [**mcp-foss-connectors**](../README.md). Configured entirely through
environment variables : no host, login or secret is baked into the code.

**Version courante : 1.8.7, non publiée.** Voir le [CHANGELOG](CHANGELOG.md).

`dolibarr_update_thirdparty` accepte `cond_reglement_id`, identifiant entier
strictement positif du dictionnaire des conditions de règlement. Le paramètre
omis ou `null` conserve la condition existante ; zéro, booléens, chaînes et
nombres décimaux sont refusés avant tout appel. Les autres paramètres gardent
leur comportement.

Avec cette option, le PUT REST du tiers est suivi d'une relecture, même si le
PUT échoue. `write_state` distingue `accepted`, `rejected`, `unknown` et, lors
du refus local, `not_attempted`. `write_may_have_succeeded` indique si l'écriture
a pu passer. `readback` rend les champs relus, dont l'identifiant brut
`cond_reglement_id_raw`, l'identifiant normalisé, le code et le libellé.
`verified` compare l'identifiant brut à la demande ; `success` exige aussi un
PUT accepté. Une réponse perdue peut donc donner `verified=true` avec
`success=false` : la valeur est présente, sans preuve que cet appel l'a écrite.
Les erreurs restent dans `error`, `refetch_warning` ou `payment_terms_warning`.
Une relecture impossible ne permet pas de conclure à l'échec de l'écriture ;
aucun PUT n'est rejoué automatiquement. Le détail `dolibarr_get_thirdparty`
expose déjà l'identifiant et le libellé, avec une réserve si le dictionnaire
est inaccessible.

## Environment variables

| Variable | Required | Description |
|---|---|---|
| `DOLIBARR_URL` | yes | Base URL of the Dolibarr instance, e.g. `https://erp.example.com`. No trailing slash. The REST API module must be enabled. |
| `DOLIBARR_API_KEY` | yes | API key of a Dolibarr user (sent as the `DOLAPIKEY` header). Generate it on the user record → *API key* tab. |
| `DOLIBARR_THIRDPARTY_PAYMENT_TERM` | no | Default payment term for new customers and prospects: dictionary code (case-insensitive) or positive integer ID. Empty or unset: no default. Read on each call; see [Conditions de règlement](#conditions-de-règlement). |
| `DOLIBARR_WEB_LOGIN` | no | Login used **only** for the writes the REST API does not expose : today, bank reconciliation alone. Leave unset to disable that fallback. |
| `DOLIBARR_WEB_PASSWORD` | no | Password for the account above. Needs the *bank → reconcile* permission. |

## Outils (78)

- `dolibarr_list_projects` : Liste les projets Dolibarr
- `dolibarr_get_project` : Détail d'un projet par ID numérique ou par référence
- `dolibarr_list_tasks` : Liste les tâches d'un projet Dolibarr
- `dolibarr_log_time` : Saisir du temps passé sur une tâche Dolibarr
- `dolibarr_list_time_entries` : Liste les écritures de temps d'une tâche ou d'un projet
- `dolibarr_update_time_entry` : Corrige une écriture de temps existante
- `dolibarr_delete_time_entry` : Supprime une écriture de temps
- `dolibarr_list_task_contacts` : Liste les intervenants affectés à une tâche
- `dolibarr_assign_task_user` : Affecte un utilisateur comme intervenant d'une tâche
- `dolibarr_unassign_task_user` : Retire un utilisateur des intervenants d'une tâche
- `dolibarr_create_task` : Crée une tâche dans un projet Dolibarr
- `dolibarr_close_task` : Marque une tâche Dolibarr comme terminée (progress=100 par défaut)
- `dolibarr_list_invoices` : Liste les factures clients
- `dolibarr_list_supplier_invoices` : Liste les factures fournisseur
- `dolibarr_list_proposals` : Liste les propositions commerciales (devis)
- `dolibarr_list_thirdparties` : Liste les tiers (clients, fournisseurs, prospects)
- `dolibarr_get_thirdparty` : Détail complet d'un tiers par son ID
- `dolibarr_add_contact` : Ajoute un contact à un tiers existant dans Dolibarr
- `dolibarr_create_thirdparty` : Crée un nouveau tiers dans Dolibarr
- `dolibarr_update_thirdparty` : Modifie un tiers existant dans Dolibarr
- `dolibarr_list_contacts` : Liste les contacts rattachés à un tiers
- `dolibarr_search_contacts` : Cherche un contact par email ou nom à travers tous les tiers
- `dolibarr_update_contact` : Modifie un contact existant dans Dolibarr
- `dolibarr_delete_contact` : Supprime un contact dans Dolibarr
- `dolibarr_list_tickets` : Liste les tickets de support
- `dolibarr_get_ticket` : Récupère le détail complet d'un ticket de support
- `dolibarr_create_ticket` : Crée un ticket de support dans Dolibarr
- `dolibarr_close_ticket` : Ferme (ou rouvre) un ticket avec message de clôture optionnel, via PUT /tickets/{id}
- `dolibarr_add_ticket_message` : Ajoute un message au fil d'un ticket via POST /tickets/newmessage
- `dolibarr_update_ticket` : Met à jour un ticket : sujet, sévérité, type, rattachements, assignation
- `dolibarr_delete_ticket` : Supprime définitivement un ticket (irréversible)
- `dolibarr_resolve_email` : Résout une adresse email vers un tiers et/ou contact Dolibarr
- `dolibarr_get_revenue_summary` : Synthèse du chiffre d'affaires depuis les factures Dolibarr
- `dolibarr_list_actions_open` : Liste les actions ouvertes (agenda, CR, suivi) dans Dolibarr
- `dolibarr_dashboard` : Tableau de bord de direction (synthèse multi-modules)
- `dolibarr_create_project` : Crée un nouveau projet dans Dolibarr
- `dolibarr_update_project` : Met à jour un projet existant dans Dolibarr
- `dolibarr_delete_project` : Supprime un projet Dolibarr via DELETE /api/projects/{id}
- `dolibarr_create_proposal` : Crée une proposition commerciale dans Dolibarr
- `dolibarr_add_proposal_lines` : Ajoute des lignes à une proposition commerciale Dolibarr (brouillon)
- `dolibarr_get_invoice` : Détail d'une facture client par ID numérique ou par référence
- `dolibarr_get_proposal` : Détail d'une proposition commerciale par ID numérique ou par référence
- `dolibarr_get_supplier_invoice` : Détail d'une facture FOURNISSEUR par ID numérique ou par référence
- `dolibarr_validate_invoice` : Valide une facture brouillon (statut 0 → 1) via POST /invoices/{id}/validate
- `dolibarr_update_invoice` : Met à jour une facture : projet, référence client, notes, date d'échéance
- `dolibarr_validate_proposal` : Valide une proposition commerciale brouillon via POST /proposals/{id}/validate
- `dolibarr_update_proposal` : Met à jour une proposition : projet, titre, dates, notes, réf. client
- `dolibarr_set_proposal_draft` : Repasse une proposition validée en brouillon (pour rééditer ses lignes)
- `dolibarr_delete_proposal` : Supprime une proposition commerciale
- `dolibarr_update_proposal_line` : Met à jour une ligne de proposition (brouillon uniquement)
- `dolibarr_delete_proposal_line` : Supprime une ligne de proposition (brouillon uniquement)
- `dolibarr_get_invoice_pdf_url` : Retourne l'URL Dolibarr du PDF d'une facture (lien direct pour téléchargement
- `dolibarr_list_bank_accounts` : Liste les comptes bancaires, solde et indicateur de rapprochement
- `dolibarr_list_bank_entries` : Écritures d'un compte sur une période, solde cumulé compris
- `dolibarr_get_bank_balance` : Solde d'un compte à aujourd'hui ou à une date d'arrêté
- `dolibarr_create_bank_entry` : Crée une opération diverse (sens explicite : `credit` / `debit`)
- `dolibarr_set_bank_statement` : Affecte un numéro de relevé à un lot d'écritures et les rapproche
- `dolibarr_toggle_bank_reconciliation` : Active ou désactive le rapprochement d'un compte
- `dolibarr_list_invoice_payments` : Règlements d'une facture et leur écriture bancaire
- `dolibarr_add_invoice_payment` : Enregistre un règlement client et l'écriture bancaire
- `dolibarr_add_supplier_invoice_payment` : Enregistre un règlement fournisseur et l'écriture bancaire
- `dolibarr_create_supplier_invoice` : Crée une facture fournisseur (brouillon)
- `dolibarr_create_invoice` : Crée une facture client (brouillon) avec ses lignes
- `dolibarr_add_invoice_lines` : Ajoute des lignes à une facture brouillon
- `dolibarr_delete_invoice_line` : Supprime une ligne d'une facture brouillon
- `dolibarr_create_invoice_from_proposal` : Facture une proposition commerciale
- `dolibarr_list_documents` : Liste les pièces jointes d'un objet
- `dolibarr_upload_document` : Attache un fichier local à un objet
- `dolibarr_build_document` : (Re)génère le PDF d'un document commercial
- `dolibarr_list_users` : Liste les utilisateurs et leurs identifiants internes
- `dolibarr_list_products` : Liste les produits et services du catalogue
- `dolibarr_list_payment_types` : Liste les modes de règlement actifs
- `dolibarr_cash_forecast` : Prévisionnel de trésorerie (soldes + échéances)
- `dolibarr_transfer_between_accounts` : Virement interne entre deux comptes
- `dolibarr_list_bank_statements` : Relevés posés et écritures restant à rapprocher
- `dolibarr_delete_bank_entry` : Supprime une écriture isolée (non rapprochée, sans règlement)
- `dolibarr_delete_invoice` : Supprime une facture brouillon, client ou fournisseur
- `dolibarr_validate_supplier_invoice` : Valide une facture fournisseur brouillon

## Invoicing a proposal: Dolibarr has no such route

`POST /invoices/createfromorder/{orderid}` and `/createfromcontract/{contractid}`
exist; there is **nothing** for a proposal. For a consultancy that quotes and
then bills directly, that is the missing half of the workflow.

`dolibarr_create_invoice_from_proposal` re-reads the proposal and recreates the
invoice client-side: same third party, project, customer reference, and one
`POST /invoices/{id}/lines` per proposal line. Two consequences it states
plainly instead of hiding:

- lines are **recreated, not moved** : the tool compares the resulting invoice
  total against the proposal total and raises `total_warning` if they diverge,
  and reports `success: false` if any line was refused, so a half-copied draft
  never reads as done;
- the link back to the proposal is posted through `linked_objects`, which
  `Facture::create()` turns into a real `add_object_linked('propal', …)` : the
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
document carries `tva_tx: None` : the key is *present*, holding null. Reading it
with `float(...)` blew up the whole line, and `create_invoice_from_proposal`
produced an **empty invoice plus a warning**. `_line_number()` now treats a null
value as an absent one. A test pins that behaviour.

## Money in, money out: one sign, three spellings

`llx_bank.amount` is **signed**: positive credits the account (money in),
negative debits it (money out). Dolibarr's own *various payment* form hides that
behind a `sens` field where **1 means Crédit** and **0 means Débit** : the
opposite of the intuition that 0 comes first : and `PaymentVarious::create()`
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
   amount and always settles the full remaining balance : partial payments are
   impossible through it. `dolibarr_add_invoice_payment` therefore posts to
   `POST /invoices/paymentsdistributed`, the only route that accepts an explicit
   amount. The supplier route, oddly, *does* take an `amount`, and is used as is.
2. **Bank reconciliation.** `PUT /bankaccounts/{id}/lines/{line_id}` only writes
   the label; `num_releve` and `rappro` are reachable solely through
   `AccountLine::update_conciliation()`, i.e. through the web form.
   `dolibarr_set_bank_statement` falls back to that form, but only when
   `DOLIBARR_WEB_LOGIN`/`DOLIBARR_WEB_PASSWORD` are set : and it **re-reads every
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
bucketed by due date : overdue, 0-30, 31-60, 61-90, beyond : and a projected
balance at the horizon.

Two honest limits, both stated in the response:

- it is a **due-date projection**, not a commitment: each invoice is counted on
  its due date, and overdue ones count immediately;
- the amount used is `remaintopay` when Dolibarr provides it, the TTC total
  otherwise : a partly-settled invoice must not be forecast in full.

## Credit notes are negative, and that broke two things

A Dolibarr credit note (`AV…`) is an invoice with a **negative** total. Two
defects in 1.6.0 came from forgetting that, both found during the 31/08/2026
acceptance run against production:

- `list_invoices` compared `total_ttc < min_amount` with `min_amount` defaulting
  to **0**, so every credit note was silently dropped : 13 of the last 100
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

- `emises` (default) : every **validated** invoice, paid or not, counted at its
  invoice date. This is commercial revenue: an invoice issued this month counts
  this month.
- `encaissees` : the customer payments actually **seen on a bank account** over
  the period, at their operation date, VAT included.

Until v1.4 the summary pulled 500 invoices sorted by `rowid` ascending with no
date filter. Past 500 validated invoices on the instance, that returned the
**oldest** ones and the current month quietly collapsed to zero. The scan is now
bounded by the requested year and paged to the end.

## Durations are seconds, everywhere

Dolibarr stores and returns **every** duration in seconds, usually as a string
(`"117000"`). Read as hours, that is a figure 3600 times too high : and nothing
errors out. Every tool that exposes a duration therefore returns it twice: a
converted value rounded to two decimals (`spent_hours`, `planned_hours`,
`total_spent_hours`) alongside the untouched original (`spent_seconds`,
`planned_seconds`, `total_spent_seconds`). A task carrying 32.5 h reports
`spent_hours: 32.5` and `spent_seconds: 117000`.

A missing duration reports `null`, never `0` : zero would read as "no time
logged" when the information is simply absent.

In the other direction, `dolibarr_log_time` takes `duration` in **hours** and
converts it before the call (1.5 → 5400), because the API accepts seconds only.

## Time: which routes exist, and which one is broken

Several near-identical routes exist and only one accepts a `POST`; the others
answer with a bare `404` that is indistinguishable from a permission problem or
a bad id.

| Route | Verb | Status |
|---|---|---|
| `tasks/{id}/addtimespent` | `POST` | the only add route; works, but **fatally broken on some Dolibarr 23.0.x builds**; see below |
| `tasks/{id}/timespent` | `GET` only | works; a `POST` here returns `404` |
| `tasks/{id}/timespent/{entry_id}` | `PUT` / `DELETE` | work (Dolibarr 23+) |
| `tasks/{id}/getTimeSpent/{entry_id}` | `GET` | works (Dolibarr 23+) |
| `projects/tasks/{id}/…` | : | does not exist: the `Tasks` class is mounted at the root, never under `/projects` |

### Known upstream bug: adding time fails on some Dolibarr 23.0.x builds

**This is a Dolibarr 23 regression, and this section used to read as a
verdict on the route itself.** The inno³ production instance runs **Dolibarr
24.0.1** (read off the login page on 25/09/2026) and writes through this route
without trouble: 17 entries on 18/09/2026, plus a control entry written, read
back and deleted on 25/09/2026. Treat what follows as a signature to
recognise on a 23.0.x, not as the fate of the route.

On an affected build, `POST tasks/{id}/addtimespent` returns **HTTP 500 with a
zero-byte body** for every request, whatever it contains. It is a PHP fatal,
not an application error:

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

On an affected instance, and until it is patched, **time entry goes through
the web UI** (`projet/tasks/time.php`). Reading and correcting time are
unaffected:
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

### Transport errors are named, and reads are replayed

A dropped connection or a read timeout used to reach the caller as a bare
`Erreur : ` : `httpx.ReadError` and `ReadTimeout` carry no message : so a
`dolibarr_get_meetingnote` failing on a busy server read like a missing note
(08/09/2026, three notes, all found on replay). Every transport failure is now
retried on reads (`GET`), and on any method when the request never left
(`ConnectError`, `ConnectTimeout`). Writes are never replayed once the request
was sent: Dolibarr may have created the object before the response was lost.
What still fails is rendered as
`Erreur réseau ReadError sur GET meetingnotesapi/251 (…) après 3 tentatives`,
naming the route and whether a replay is safe.

### Notes on a detail read are rendered whole, or say they were cut

`dolibarr_get_proposal` used to cut `note_public` at 300 characters, with no
marker and no length: a note written through `dolibarr_update_proposal` could
not be read back for checking, and the cut read like a loss in the database
(14/09/2026, proposal 172, before and after a successful update). The same
silent cut sat in `dolibarr_get_invoice`, `dolibarr_get_supplier_invoice` and
`dolibarr_get_thirdparty` (300 characters), and in the `description` and
`note_public` of `dolibarr_get_project` (500). Those fields are now rendered
whole, notes with their HTML cleaned, up to `DETAIL_NOTE_MAX_CHARS` (20 000
characters). Beyond that they are cut at the ceiling and the cut is announced:
`<field>_truncated` is `true` and `<field>_length` carries the real length of
the text. Both companions are always present, so a caller can compare what it
wrote with what it reads.

## Creating a ticket can write somewhere else, and says so

`POST /tickets` fires Dolibarr's `TICKET_CREATE` trigger, and any installed
module may write on the back of it. The connector cannot prevent that : it is
server-side code, triggered by the write itself : so it reports it instead.

`dolibarr_create_ticket` returns two extra fields:

- `side_effects_checked` : whether the connector was *able* to look. It is
  `false` on a vanilla install, where nothing teaches it which module writes
  what. "I did not look" is not "nothing happened", and the two must not read
  alike.
- `side_effects` : one entry per module that wrote, each naming what it wrote.
  An entry carrying `error` means an observer failed: the ticket exists, but
  that side effect could not be verified.

An extension registers an observer through the public core API:

```python
from dolibarr_mcp import server

async def my_observer(ticket_id: int, thirdparty_id: int) -> dict | None:
    ...  # read back what the module wrote; None means nothing to report

server.register_ticket_create_observer(my_observer)
```

The observer returns a dict (added to `side_effects`, its `message` key
appended to the tool's human-readable message) or `None`. An exception it
raises never fails the ticket creation.

One consequence worth spelling out: on an instance that debits automatically,
the `severity` argument also **prices** the ticket. The module maps `low` to
one unit and `high` / `critical` to five; `normal` is not in its dictionary at
all, so it falls back to one unit and logs a warning. A ticket raised as
`critical` costs the client five times one raised as `low`, and nothing in the
parameter's name says so : hence the note in the tool's docstring.

What prompted this: on 18/09/2026, eleven tickets created through this tool
each debited one unit from the client's support notebooks (inno³'s in-house
`supportcredits` module, which debits the oldest active notebook on
`TICKET_CREATE`). Nothing in the answers said so; the writes only surfaced when
the next explicit debit was refused with `statut=2, restant=0`. The observer
for that module ships in the [inno³ extension package](#inno3-extension-package).

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

Same rules : real instance, run by hand. This one is worth running on every new
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

    print("OK : supprimer les deux écritures de test dans Dolibarr.")

asyncio.run(main())
PY
```

Expected: `+100` then `-40`, a balance that moved by exactly `+60`, and a
running balance on the last entry equal to the account balance. Delete both
test entries afterwards with `dolibarr_delete_bank_entry`, which refuses any
entry that is already reconciled or that carries a payment link : deleting the
bank line of a settled invoice would leave the payment orphaned.

The payment tools follow the same contract on a throwaway invoice:
`dolibarr_add_invoice_payment` must report `balance_delta == expected_delta`
(positive), `dolibarr_add_supplier_invoice_payment` the same with a **negative**
`expected_delta`, and both must list the new payment under `created_payments`
with a `bank_line_id`.

## Acceptance test against a live instance

[`RECETTE_COWORK.md`](RECETTE_COWORK.md) is a script to run from an MCP client
against a real Dolibarr. It starts with a version check : three acceptance runs
were spent chasing "defects" that were only stale deployments : then walks
banking, settlement, invoicing and attachments with expected values measured on
the live instance. It validates no invoice, so it burns no accounting sequence
number. It needs a throwaway bank account and third party, keeps every amount
under 10 €, and cleans up after itself : flagging what it could not remove.

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

An extension can also observe what a write triggers server-side, through
`server.register_ticket_create_observer()` : see
[Creating a ticket can write somewhere else](#creating-a-ticket-can-write-somewhere-else-and-says-so).

A bad extension can never break the core: load failures are caught and logged
to stderr, and the server keeps running with its 48 generic tools.

### inno³ extension package

Tools that depend on inno³'s **custom** Dolibarr modules : `meetingnotes`,
`supportcredits` (carnets), `inno3pilot` (boards) and the signed
`inno3dashboard` / `supportcredits` portal URLs : are published as a separate
add-on, **`inno3-mcp-extensions`** (29 tools), *not* in this repository.
Installing it next to `dolibarr-mcp` raises the tool count from 48 to 77;
uninstalling it restores the generic 48. No environment flag needed.

To ship both in a single Claude Desktop bundle, vendor the extension at build
time : pip installs it *with* its `.dist-info`, without which the entry point is
invisible and the extra tools silently absent:

```sh
python scripts/build_mcpb.py dolibarr --with-extension ../inno3-mcp-extensions
```

**Updating an already-installed bundle.** A Desktop extension is identified by
`<publisher>.<manifest name>` : that key indexes both its install directory and
its settings file (URLs, secrets). Publishing under a different `name` therefore
installs a *second* extension side by side and does not carry the configuration
over. To update in place, republish under the name already installed, without
touching the repository's generic manifest:

```sh
python scripts/build_mcpb.py dolibarr --with-extension ../inno3-mcp-extensions \
  --bundle-name my-org-dolibarr --display-name "Dolibarr ERP (my org)"
```

## License

[Apache-2.0](../LICENSE) : © 2026 inno³ and contributors.

## Contrat des exports depuis 1.8.0

Toutes les listes ci-dessous acceptent `limit` (20 par défaut, de 1 à 100)
et `cursor` (vide au premier appel). Réutiliser `next_cursor` avec les mêmes
filtres et le même tri. Le curseur est un décalage dans la liste dédoublonnée.
Exception depuis 1.8.2 : les temps utilisent un curseur opaque par tâche,
avec budget global et agrégats sur les seules tâches lues (voir ci-dessous).
Chaque appel relit les données. Il faut donc exporter une période stable pour
obtenir une union reproductible, sans création ou suppression concurrente.

Chaque réponse contient `total`, `returned`, `count`, `truncated`,
`next_cursor`, `total_exact`, `total_lower_bound`, `scan_truncated`, `limit`
et `offset`. `count` est toujours le nombre de lignes effectivement rendues.
`total` compte les identifiants uniques après filtrage, sur toutes les pages.
Ce n'est pas un compteur natif séparé, mais un décompte du parcours REST.
`truncated` indique des lignes restant à rendre ou un parcours incomplet.
`next_cursor=null` signifie qu'aucune autre page collectée n'est disponible.
Si `scan_truncated=true`, `total=null` et `scan_note` explique la limite.
Il faut alors restreindre les filtres, même sans curseur de suite.
Une erreur HTTP ou une réponse de forme inattendue ne devient jamais une
liste vide réussie.

Les routes paginées sont lues par lots de 100, avec un plafond de 100 appels
(`EXPORT_SCAN_PAGE`, `EXPORT_SCAN_MAX_PAGES`). Les IDs sont dédoublonnés avant
la coupe et avant les filtres locaux. Le tri serveur ajoute `rowid` pour les
égalités de dates ou de noms. Un serveur répétant une page est signalé.
Les routes de collections complètes sont paginées localement.
Les documents sont identifiés par chemin relatif, les relevés par numéro.
Une ligne sans identifiant est conservée, sans fusion par ressemblance.

| Outil, préfixe `dolibarr_` | Tableau et champs principaux | Filtres et statuts | Période et collecte |
| --- | --- | --- | --- |
| `list_projects` | `projects`, id, ref, title, status, thirdparty, dates, budget | status 0 brouillon, 1 ouvert par défaut, 2 fermé, vide tous, thirdparty_id, search | Toutes dates, REST paginé, recherche locale |
| `list_tasks` | `tasks`, id, ref, label, progress, dates, heures et secondes prévues et passées | project_id obligatoire, assigned_to | Collection complète du projet |
| `list_task_contacts` | `contacts`, contact_id, user_id, source, rôle, identité | task_id obligatoire, role | Collection complète de la tâche |
| `list_time_entries` | `entries`, entry_id, task_id, user_id, date, seconds, hours, note | task_id ou project_id, user_id | date_start et date_end incluses, pagination par tâche et budget global, agrégats sur les tâches lues |
| `list_invoices` | `invoices`, id, ref, total_ht, total_ttc, échéance, statut, retard, reste | status unpaid par défaut, paid, draft, all, thirdparty_id, min_amount, search_ref | date_start et date_end incluses sur date de facture, sort asc ou desc, REST paginé |
| `list_supplier_invoices` | `invoices`, id, ref, ref_supplier, total_ttc, échéance, statut, retard | mêmes statuts, thirdparty_id, search_ref sur les deux références | mêmes bornes et tri que les factures clients |
| `list_proposals` | `proposals`, id, ref, tiers, total_ht, total_ttc, date_validity, status | status vide tous, 0 brouillon, 1 validé, 2 signé, 3 refusé, 4 facturé, thirdparty_id | Toutes dates, datep décroissante, REST paginé |
| `list_thirdparties` | `thirdparties`, id, nom, email, téléphone, ville, types, statut | type_filter customer, supplier, prospect, search, search_email, search_code, category | Toutes dates, nom croissant, offset conservé comme alternative au curseur |
| `list_contacts` | `contacts`, id, identité, email, téléphone, poste | thirdparty_id obligatoire | Toutes dates, ID croissant, REST paginé |
| `search_contacts` | `contacts`, id, identité, tiers, email, téléphone | search ou email obligatoire, union des critères | Toutes dates, ID croissant, REST paginé |
| `list_tickets` | `tickets`, id, ref, sujet, statut, sévérité, tiers, assignation | status natif, thirdparty_id, severity | Toutes dates, REST paginé |
| `list_actions_open` | `actions`, id, libellé, dates, progression, projet, tiers | project_id, thirdparty_id, assigned_to, actions non terminées | Toutes dates, REST paginé |
| `list_bank_accounts` | `accounts`, id, libellé, type, devise, solde, rapprochement | include_closed faux par défaut | Comptes ouverts par défaut, REST paginé, solde agrégé sur la sélection |
| `list_bank_entries` | `entries`, id, dates, montant signé, solde cumulé, rapprochement, liens | account_id obligatoire, reconciled yes, no ou vide, with_links, sort, offset | date_start et date_end incluses, début à 90 jours avant date_end ou aujourd'hui par défaut, collection complète de la fenêtre |
| `list_invoice_payments` | `payments`, date, montant, mode, référence, type, ligne bancaire | invoice_id obligatoire, supplier faux par défaut | Tous règlements et imputations de la facture, collection complète |
| `list_documents` | `documents`, name, relative_path, size, type, date | module et object_id ou ref obligatoires | Collection complète des pièces de l'objet |
| `list_users` | `users`, id, login, identité, email, statut, employee | search local, include_disabled faux par défaut | Toutes dates, REST paginé |
| `list_products` | `products`, id, ref, label, type, prix, TVA, on_sell | search, kind product, service ou vide | Toutes dates, REST paginé |
| `list_payment_types` | `payment_types`, id, code, label | modes actifs | Dictionnaire complet |
| `list_bank_statements` | `statements`, num_releve, dates, débits, crédits, rapprochement | account_id obligatoire | bornes incluses, début à 365 jours avant date_end ou aujourd'hui par défaut, regroupement de la fenêtre complète |

Les agrégats sont calculés sur la sélection collectée, avant pagination de
sortie. Ils ne sont complets que lorsque `scan_truncated=false`.

### Champs financiers et paiements

Les listes et détails de factures rendent `total_ht`, `total_ttc`, `status`,
`date_due`, `remaining_to_pay`, `type`, `source_invoice_id` et
`unavailable_fields`. Les propositions rendent aussi `date_validity`.
Une valeur absente de la source est rendue `null` et nommée dans
`unavailable_fields`. Une valeur explicitement `null` reste `null` sans y
figurer. Zéro reste zéro, notamment pour le reste à payer et le statut brouillon.
L'échéance fournisseur accepte `date_echeance` puis `date_lim_reglement`.
Un reste inconnu n'est jamais remplacé par le total TTC.

`include_payments=true` par défaut sur les listes et détails de factures lit
les règlements détaillés de chaque facture rendue. `payments_total` additionne
les règlements et les imputations renvoyés par la route, y compris avoirs et
acomptes, ce n'est donc pas un encaissement bancaire. Une route refusée,
inexistante ou invalide rend `payments=null`, avec `payments_error` et un
champ indisponible explicite. `include_payments=false` évite ces appels
supplémentaires et signale que les paiements n'ont pas été demandés.

### Conditions de règlement

Depuis la **1.8.6**, `dolibarr_create_thirdparty` accepte le paramètre facultatif
`cond_reglement_id`, entier strictement positif, prioritaire sur le réglage
d'installation `DOLIBARR_THIRDPARTY_PAYMENT_TERM`. Omis ou `null`, il utilise
ce réglage. Zéro, valeurs négatives, booléens, chaînes et décimaux sont refusés
pour le paramètre avant tout appel, y compris via MCP.

La variable est lue à chaque appel : code du dictionnaire insensible à la casse
(par exemple `30D` ou `30d`), ou identifiant entier positif (par exemple `2`).
Elle est vide par défaut ; vide ou absente et sans paramètre explicite, aucune
condition n'est envoyée et les appels restent ceux de la 1.8.5. Un code inconnu,
un réglage invalide ou un dictionnaire illisible pour un code laisse créer le tiers sans condition, avec
une erreur explicative. Un identifiant numérique reste envoyé même sans
dictionnaire lisible. Ce défaut concerne uniquement les clients et prospects
(`client` 1, 2 ou 3), y compris les clients également fournisseurs. Un tiers
uniquement fournisseur ne reçoit aucune condition, même avec le paramètre.

La condition est transmise dans le POST de création, puis le tiers est relu
une seule fois par `thirdparties/{id}` si une condition a été envoyée.
`payment_terms_applied` contient `cond_reglement_id`, `code`, `label`, `source`,
`verified` et `error`. Les provenances sont `parametre`, `configuration`,
`aucune` si aucune condition n'a pu être choisie, et `non_applicable` hors
clients/prospects. `verified` vaut `true` si l'identifiant brut relu correspond
à celui envoyé, `false` en cas d'écart ou de condition absente, `null` si rien
n'a été envoyé ou si la relecture a échoué. L'écart ou l'erreur de relecture
figure dans `error`, sans transformer une création réussie en échec ni rejouer
le POST. L'anti-doublon conserve son comportement et précède toute lecture du
dictionnaire : un doublon détecté n'entraîne aucun appel supplémentaire.

Depuis la 1.8.5, les créations de facture et de devis acceptent
`cond_reglement_id`, entier strictement positif facultatif. Zéro, valeurs
négatives, booléens, chaînes et décimaux sont
refusés avant tout appel, y compris via MCP. Omis ou `null`, il active la reprise :

- `dolibarr_create_invoice` : paramètre prioritaire, sinon condition du tiers lue avant création, sinon aucune.
- `dolibarr_create_proposal` : paramètre prioritaire, sinon condition du tiers lue avant création, sinon aucune.
- `dolibarr_create_invoice_from_proposal` : paramètre prioritaire, sinon condition du devis, sinon celle du tiers, sinon aucune.

Le tiers n'est pas lu quand le paramètre est fourni ou que le devis porte déjà
une condition. Sans condition sur le tiers, aucun `cond_reglement_id` n'est
envoyé. Une lecture du tiers impossible laisse aussi la création se poursuivre
sans condition, avec l'erreur dans la réponse.

Pour ces factures et devis, `payment_terms_applied` décrit la condition envoyée :
`cond_reglement_id`, `code`, `label`, `source` (`parametre`, `devis`, `tiers` ou `aucune`) et `error`
(`null` en l'absence d'erreur). Le code et le libellé proviennent du dictionnaire
en cache décrit ci-dessous. Son indisponibilité est signalée dans `error` et
ne bloque pas l'envoi d'un identifiant connu. `source=aucune` laisse les trois
champs de condition à `null`. Pour les deux créations de facture, `due_date`
est envoyé dans `date_lim_reglement` et prime toujours sur le calcul lié à la
condition, héritée ou forcée. Le mode de règlement reste inchangé.

`dolibarr_list_invoices`, `dolibarr_list_supplier_invoices`,
`dolibarr_get_invoice` et `dolibarr_get_supplier_invoice` rendent depuis la 1.8.1
`cond_reglement_id`, `cond_reglement_code`, `cond_reglement_label` pour la
facture, et les trois champs préfixés par `thirdparty_` pour son tiers.
En lecture, la condition du tiers ne remplace jamais celle de la facture. Hors erreur de
lecture signalée, `null` sur les deux conditions signifie qu'aucune condition
de règlement n'est paramétrée dans Dolibarr. `dolibarr_get_thirdparty` rend
aussi les trois champs : tous à `null`, ils indiquent l'absence de condition
paramétrée sur le tiers.

Une `date_due` à date de facture + 1 jour correspond notamment à la condition
« À réception » (`RECEP`, `nbjour = 1`) de Dolibarr, parfois appliquée par défaut.
Cet intervalle ne prouve pas l'absence de condition ni un retard réel.
`date_due_is_default` signale cette heuristique :
`true` pour cet intervalle à vérifier, `false` pour un autre intervalle, `null` si l'une des
dates manque ou est illisible. Vérifier la condition négociée avant une relance :
si elle prévoit un délai plus long, `days_late` surévalue le retard de règlement.
Son calcul reste inchangé. Les listes donnent `date_due_default_count` sur la seule page rendue
et `payment_terms_note` seulement si ce nombre est non nul. Cette même note
accompagne le détail d'une facture dont `date_due_is_default=true`.

`include_payment_terms=true` par défaut ajoute une lecture de
`thirdparties/{id}` par tiers distinct de la page rendue, après pagination.
Le dictionnaire `setup/dictionary/payment_terms` est lu une seule fois par
session, échec compris, avec les libellés `label`, `libelle` ou
`libelle_facture`. Un dictionnaire inaccessible donne
`payment_terms_unavailable` ; un tiers inaccessible donne
`thirdparty_payment_terms_error` sur la facture. Ces échecs ne bloquent pas
la lecture des factures : les champs impossibles à résoudre restent `null`
et figurent dans `unavailable_fields`. Les données déjà portées par la
facture restent disponibles. Redémarrer le serveur pour relire un dictionnaire
après correction des droits ou modification des conditions.

`include_payment_terms=false` supprime ces appels complémentaires et rend les
sept champs, y compris `date_due_is_default`, à `null`, avec
`payment_terms_error` et leur présence dans `unavailable_fields`. Dans ce cas,
`date_due_default_count=0` signifie qu'aucun défaut n'a été détecté, sans
certifier les échéances puisque leur contrôle a été désactivé.

### Avoirs de la page rendue

La liste client contient aussi `credit_notes`, les objets avoirs de la page
rendue, avec leur facture source quand elle est connue. `credit_notes_count`
compte ces objets. Ce tableau suit les mêmes filtres que `invoices` et n'est
pas la liste des avoirs rattachés à chaque facture hors de la période.

### Définition de la synthèse générique

La réponse de `get_revenue_summary` rend un objet `definition` exploitable :
statuts 1 et 2, date propre de chaque facture et de chaque avoir, bornes
incluses sur l'année demandée. Les champs historiques `ytd_*` couvrent l'année
entière, même si le mois demandé n'est pas décembre. `definition.ytd_scope`
le rappelle. Le mois est une sélection séparée. La variante `encaissees`
porte sur les opérations bancaires TTC, avec les exclusions déjà décrites.
Un parcours de factures interrompu, répété ou invalide refuse désormais de
publier un total financier. Aucune définition propre à une organisation ne
remplace cette formule dans le cœur.

### Compte technique et refus de droits

`DOLIBARR_API_KEY` accepte la clef de tout utilisateur Dolibarr. Aucun statut
administrateur ni identifiant personnel n'est requis par le cœur. Les droits
natifs de l'API définissent les ressources accessibles. Un HTTP 401 signale une
clef invalide et un HTTP 403 des permissions insuffisantes. Ces refus ne sont
pas rejoués. La suppression d'une proposition et le retrait d'un intervenant
conservent désormais aussi le refus rencontré pendant leur lecture préalable.

Le rapprochement bancaire par formulaire reste facultatif et nécessite les
identifiants web du compte autorisé. Laisser les deux variables web absentes
pour le désactiver. Créer le compte technique et attribuer ses droits se fait
dans Dolibarr, puis remplacer la clef dans la configuration du client MCP.
La recette doit vérifier les droits propres à chaque module activé.

Les montants de paiement absents sont aussi nommés dans `unavailable_fields`.
Un total de paiements dont un montant manque reste `null`. La synthèse émise
refuse également un montant ou une date absents. Pour les encaissements,
`unresolved_entries` signale les liens bancaires illisibles et rend le résultat
tronqué. Les appels MCP réussis ne produisent plus de journaux INFO sur stderr.
Les avertissements et erreurs restent visibles.

### Échéances cohérentes depuis 1.8.1

La liste des factures, leur détail et la projection de trésorerie partagent
la même lecture : `date_echeance` prime sur `date_lim_reglement`, même quand
sa valeur est explicitement nulle. Le signal `date_due_is_default` est une
heuristique (facture + un jour), pas une preuve de l'absence d'accord ; vérifier
les conditions avant une relance.

### Lecture des temps bornée depuis 1.8.2

`dolibarr_list_time_entries` accepte `task_limit` (20 par défaut, 100 maximum)
et `time_budget` (20 secondes par défaut, 25 maximum). `next_cursor` reprend
au premier élément non rendu, sans relire les tâches terminées. La découverte
des tâches et les attentes réseau sont comprises dans le budget.

`truncated`, `reason`, `tasks_scanned` et `aggregates_complete` distinguent
une fin de parcours d'un arrêt sur limite, erreur ou budget. `total` vaut null
si le périmètre complet n'a pas été observé. Les agrégats couvrent les tâches
lues dans cet appel, y compris les écritures non rendues d'une tâche partielle.
Ne pas additionner ces agrégats de page : additionner les écritures exportées.
Ordre par ID de tâche puis ID d'écriture, données supposées stables entre pages.
Les anciens curseurs numériques et ceux du prototype 1.8.2 doivent être remplacés
par un nouveau parcours : le curseur v2 de 1.8.3 conserve aussi le décalage cumulé.

Les métadonnées d'export de 1.8.0 sont conservées : `limit`, `offset` (écritures
rendues avant la page), `total_exact`, `total_lower_bound` et `scan_truncated`.
`total_exact` suit `aggregates_complete` ; `total_lower_bound` compte les lignes
des tâches lues dans cet appel. `scan_truncated` signale ces agrégats partiels,
y compris à la fin du parcours, tandis que `truncated` indique uniquement une
suite à lire. `scan_note` explique cette différence. Ces minorants ne se somment pas.

## Modulepart non servis par le cœur (1.8.7)

L'API documents de Dolibarr code en dur les modulepart qu'elle connaît. Un
module maison n'en fait pas partie : `dolibarr_list_documents(module="meetingnotes")`
reçoit un 500 « Modulepart meetingnotes not implemented yet ». L'outil le rend
désormais comme `unsupported_modulepart: true`, sans le confondre avec un objet
absent. Les comptes rendus se listent par `dolibarr_list_meetingnote_documents`
(extension inno³ 0.8.8, module meetingnotes 4.45.0-alpha.77).
