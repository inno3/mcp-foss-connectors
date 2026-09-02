# Matrix connector (`matrix-mcp`)

MCP server for the [Matrix](https://matrix.org/) client-server API (v3). Rooms, messages (text & HTML), members, invitations and topics.

Part of [**mcp-foss-connectors**](../README.md). Configured entirely through
environment variables — no host, login or secret is baked into the code.

## Environment variables

| Variable | Required | Description |
|---|---|---|
| `MATRIX_HOMESERVER_URL` | yes | Base URL of the Matrix homeserver, e.g. `https://matrix.example.com`. |
| `MATRIX_ACCESS_TOKEN` | yes | Access token of the bot/user account (sent as a Bearer token). |

## Tools (22)

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
- `matrix_get_reactions` — Relit les réactions posées sur un message
- `matrix_list_files` — Liste les fichiers et images partagés dans un salon
- `matrix_list_unread` — Salons ayant du non-lu, avec leur compteur
- `matrix_mark_read` — Marque un salon comme lu jusqu'à un message

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

## Counting messages, not events (v1.4.0)

Matrix paginates **events**, not messages. Redactions, reactions, edits and
state changes fill the server's window, then vanish when the connector filters
them out. Every tool that takes a count of messages has to widen its window
until the count is met — otherwise it silently returns less than asked, or
nothing at all, with no way to tell that apart from an empty room.

`matrix_get_room_messages` already did this. `matrix_get_event_context` did
not: on a room where a batch of redactions sat just before the target event,
`before` came back **empty** while hundreds of messages preceded it. Measured
on 2026-09-02 across three consecutive messages of the same batch: `before`
returned 3, then 2, then 0, purely as a function of how many bodyless events
the server happened to place in its window.

It now paginates from the `start` / `end` tokens `/context` returns, per side,
and reports `pages_fetched`, `events_scanned` and `complete` for each — plus a
`truncation_note` when the page budget runs out before the count is met. An
announced amputation, never a silent empty list. The budget of
`matrix_get_room_messages` is likewise surfaced (`max_pages`,
`budget_exhausted`): reading 3 messages after a redaction spree took 6 pages
and 72 scanned events, against 1 page and 12 normally.

## Unread counters: auditable rather than falsely aligned

`matrix_list_unread` reads the homeserver's own `unread_notifications`. Those
counters are **not** what a Matrix client shows: a client recomputes locally,
counts threads separately and decrements as you scroll. Measured on
2026-09-02, seven rooms out of seventeen diverged from Element, in both
directions — including a room dormant since 2022 that claimed 42 unread and 3
highlights, close to half the reported total.

That gap cannot be closed from a connector, so the tool stops pretending and
makes the counters checkable instead:

- `read_marker_event_id` / `read_marker_at` — how far you have actually read,
  read from the room's `m.fully_read` account data;
- `counter_suspect` — set when the room's last message **predates** the read
  marker. There is then nothing left to read after the marker, so a non-zero
  counter is arithmetically impossible, not merely surprising;
- `total_unread_confirmed` / `total_highlights_confirmed` — totals without
  those rooms. `total_unread` still reports the raw figure.

Two related fixes: `last_message` is now filled for every rendered room (the
one-event sync window often held a reaction or a state change, leaving 150
rooms out of 200 unlabelled), and direct chats are named after the person on
the other side, from the server's `m.heroes`, instead of an opaque id. Both
lookups are budgeted (`lookups_budget`, `lookups_spent`).

`matrix_mark_read` re-reads the room it just marked through a **single-room**
sync. Re-reading it through the general list failed exactly in the common
case — a room you just handled drops to zero, so to the tail of the list, past
the cap — and `unread_after` came back `null`, indistinguishable from a room
genuinely at zero. It is now an integer, zero included; when the re-read does
fail, `read_state_confirmed` is false and `unread_after_note` says why.

## License

[Apache-2.0](../LICENSE) — © 2026 inno³ and contributors.

## Acceptance test against a live instance

[`RECETTE_COWORK.md`](RECETTE_COWORK.md) is a script to run from an MCP
client against a real instance. It is read-only except where marked, and
cleans up after itself.
