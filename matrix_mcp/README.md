# Matrix connector (`matrix-mcp`)

MCP server for the [Matrix](https://matrix.org/) client-server API (v3). Rooms, messages (text & HTML), members, invitations and topics.

Part of [**mcp-foss-connectors**](../README.md). Configured entirely through
environment variables — no host, login or secret is baked into the code.

## Environment variables

| Variable | Required | Description |
|---|---|---|
| `MATRIX_HOMESERVER_URL` | yes | Base URL of the Matrix homeserver, e.g. `https://matrix.example.com`. |
| `MATRIX_ACCESS_TOKEN` | yes | Access token of the bot/user account (sent as a Bearer token). |

## Tools (18)

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

- `matrix_reply_in_thread` — Répond dans le fil d'un message
- `matrix_get_thread` — Lit toutes les réponses d'un fil
- `matrix_react` — Pose une réaction sur un message
- `matrix_edit_message` — Corrige un message déjà envoyé
- `matrix_redact_message` — Supprime un message d'un salon
- `matrix_send_file` — Envoie un fichier local dans un salon
- `matrix_get_event_context` — Lit les messages autour d'un message
- `matrix_get_permalink` — Construit un lien matrix.to

## Search was returning one page and not saying so

Across 1 617 local sessions, **46 % of Matrix calls are searches** — this is
mostly a tool for finding what was said. Three things made that harder:

- the homeserver returns a **total count** and a **`next_batch` cursor**; both
  were discarded. Past the first page — ten results by default server-side —
  there was no way to continue, and nothing said more existed. Both are now
  surfaced, along with `has_more`;
- results carried only an opaque `room_id`. The room name is now resolved, once
  per room and cached for the session, and each hit gets a `matrix.to`
  permalink so it can be cited from a meeting note or a Kanboard card;
- the search API can return the messages **surrounding** a hit. It was never
  asked. `context_size` now does, and `matrix_get_event_context` does it for a
  known event.

`matrix_get_room_messages` had the same blind spot: the `end` token was dropped,
so a room could only ever be read one page deep.

## Writing: a thread is not a new message

An alert posted by an n8n workflow deserves a reply attached to it, not another
line in the room. Until now sending a message into the room was the only way to
write, so incident follow-up drifted apart from the alert it answered.

`matrix_reply_in_thread` sets the `m.thread` relation, including the
`is_falling_back` flag — without it, clients that do not render threads show the
reply quoted twice.

Two asymmetries worth knowing: **editing does not rewrite**. Matrix publishes a
replacement event that clients display instead; the original stays in room
history. To make a message actually go away, use `matrix_redact_message`, which
verifies the removal by re-reading the event — an emptied content is the proof.
And a **reaction returns its own event id**: that is the id needed to remove it,
not the id of the message it annotates.

## License

[Apache-2.0](../LICENSE) — © 2026 inno³ and contributors.

## Acceptance test against a live instance

[`RECETTE_COWORK.md`](RECETTE_COWORK.md) is a script to run from an MCP
client against a real instance. It is read-only except where marked, and
cleans up after itself.
