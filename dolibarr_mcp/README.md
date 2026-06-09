# Dolibarr ERP connector (`dolibarr-mcp`)

MCP server for the [Dolibarr](https://www.dolibarr.org/) ERP/CRM REST API (Dolibarr **16+**). Projects, tasks, time logging, third parties, contacts, invoices, proposals and support tickets.

Part of [**mcp-foss-connectors**](../README.md). Configured entirely through
environment variables — no host, login or secret is baked into the code.

## Environment variables

| Variable | Required | Description |
|---|---|---|
| `DOLIBARR_URL` | yes | Base URL of the Dolibarr instance, e.g. `https://erp.example.com`. No trailing slash. The REST API module must be enabled. |
| `DOLIBARR_API_KEY` | yes | API key of a Dolibarr user (sent as the `DOLAPIKEY` header). Generate it on the user record → *API key* tab. |

## Tools (36)

- `dolibarr_list_projects` — Liste les projets Dolibarr
- `dolibarr_get_project` — Détail d'un projet par ID numérique ou par référence
- `dolibarr_list_tasks` — Liste les tâches d'un projet Dolibarr
- `dolibarr_log_time` — Saisir du temps passé sur une tâche Dolibarr
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
- `dolibarr_validate_proposal` — Valide une proposition commerciale brouillon via POST /proposals/{id}/validate
- `dolibarr_get_invoice_pdf_url` — Retourne l'URL Dolibarr du PDF d'une facture (lien direct pour téléchargement

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
to stderr, and the server keeps running with its 36 generic tools.

### inno³ extension package

Tools that depend on inno³'s **custom** Dolibarr modules — `meetingnotes`
(7 tools) and the signed `inno3dashboard` / `supportcredits` portal URLs
(2 tools) — are published as a separate add-on, **`inno3-mcp-extensions`**, *not* in
this repository. Installing it next to `dolibarr-mcp` raises the tool count from
36 to 45; uninstalling it restores the generic 36. No environment flag needed.

## License

[Apache-2.0](../LICENSE) — © 2026 inno³ and contributors.
