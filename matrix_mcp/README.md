# Matrix connector (`matrix-mcp`)

MCP server for the [Matrix](https://matrix.org/) client-server API (v3). Rooms, messages (text & HTML), members, invitations and topics.

Part of [**mcp-foss-connectors**](../README.md). Configured entirely through
environment variables — no host, login or secret is baked into the code.

## Environment variables

| Variable | Required | Description |
|---|---|---|
| `MATRIX_HOMESERVER_URL` | yes | Base URL of the Matrix homeserver, e.g. `https://matrix.example.com`. |
| `MATRIX_ACCESS_TOKEN` | yes | Access token of the bot/user account (sent as a Bearer token). |

## Tools (10)

- `matrix_whoami` — Retourne les informations du compte Matrix authentifié (user_id, device_id, server)
- `matrix_list_rooms` — Liste tous les salons Matrix rejoints avec leur nom et sujet
- `matrix_get_room_messages` — Lit les derniers messages d'un salon Matrix
- `matrix_send_message` — Envoie un message texte brut dans un salon Matrix
- `matrix_send_html_message` — Envoie un message HTML formaté dans un salon Matrix
- `matrix_search_messages` — Recherche des messages dans les salons Matrix
- `matrix_get_room_members` — Liste les membres d'un salon Matrix
- `matrix_create_room` — Crée un nouveau salon Matrix
- `matrix_invite_user` — Invite un utilisateur dans un salon Matrix
- `matrix_set_topic` — Définit ou modifie le sujet (topic) d'un salon Matrix

## License

[Apache-2.0](../LICENSE) — © 2026 inno³ and contributors.
