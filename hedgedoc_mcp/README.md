# HedgeDoc connector (`hedgedoc-mcp`)

MCP server for [HedgeDoc](https://hedgedoc.org/) 1.x. Read, create, update, search and list collaborative Markdown notes.

Part of [**mcp-foss-connectors**](../README.md). Configured entirely through
environment variables — no host, login or secret is baked into the code.

## Environment variables

| Variable | Required | Description |
|---|---|---|
| `HEDGEDOC_URL` | yes | Base URL of the HedgeDoc instance, e.g. `https://pad.example.com`. |
| `HEDGEDOC_API_TOKEN` | no | Session/API token (preferred). If set, used directly. |
| `HEDGEDOC_USER` | no | Email/login for password authentication (when no token is supplied). |
| `HEDGEDOC_PASSWORD` | no | Password for the login above. |

## Tools (8)

- `hedgedoc_get_note` — Télécharge le contenu Markdown d'une note HedgeDoc
- `hedgedoc_get_note_info` — Récupère les métadonnées d'une note HedgeDoc (titre, tags, dates, vues)
- `hedgedoc_create_note` — Crée une nouvelle note HedgeDoc avec un contenu Markdown
- `hedgedoc_update_note` — Met à jour le contenu Markdown d'une note HedgeDoc existante
- `hedgedoc_list_history` — Liste les notes récentes de l'utilisateur connecté (historique HedgeDoc)
- `hedgedoc_me` — Retourne les informations de l'utilisateur HedgeDoc actuellement connecté
- `hedgedoc_search_notes` — Recherche des notes dans l'historique HedgeDoc par titre ou tag
- `hedgedoc_delete_history_entry` — Supprime une note de l'historique HedgeDoc de l'utilisateur

## Authentication

Provide **either** `HEDGEDOC_API_TOKEN` **or** the `HEDGEDOC_USER` /
`HEDGEDOC_PASSWORD` pair. HedgeDoc 1.x has no first-class API-token concept, so
the connector logs in and reuses the session cookie when a user/password is
given.

## License

[Apache-2.0](../LICENSE) — © 2026 inno³ and contributors.
