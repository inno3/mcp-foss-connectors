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

## When a call fails, what the message tells you (1.2.0)

The same error contract as the Dolibarr and Kanboard connectors, which was
written after both answered `Erreur : ` — nothing else — in production on
08/09/2026 and 24/09/2026. An empty string separates nothing: an expired
session, a missing note, an instance option that is turned off and a passing
network fault all read alike, and the caller cannot tell whether to replay, fix
the call or stop. Five things now hold.

**No message is ever bare.** An httpx exception carrying no text (`ReadError`,
a naked `ReadTimeout`, `RemoteProtocolError`) is rendered with its type and what
that family means — `Erreur réseau ReadError : connexion coupée par le serveur
avant la réponse.` Any other empty exception keeps at least its class name.

**The whole `httpx.TransportError` family is handled.** Only `ConnectError` and
the two timeouts used to be caught; a `ReadError`, a `PoolTimeout` or a
`RemoteProtocolError` went through with neither replay nor wrapping.

**The status and the body are rendered.** A 5xx keeps HedgeDoc's own body rather
than an empty tail, and an unknown status no longer ends on a colon.

**A write is never replayed once the request has left.** `POST /new` creates a
note on every call: replayed after a lost response, it leaves two notes with the
same content under different ids, and nothing points that out — the caller walks
away with the second URL believing there is only one. Reads (`GET`, `HEAD`,
`OPTIONS`) are replayed on any transport error and on 5xx. Writes are replayed
only when the connection never succeeded (`ConnectError`, `ConnectTimeout`). The
re-login retry on 401/403 still applies to writes, and safely so: those codes
prove HedgeDoc refused the request before executing it.

**The error says what became of the write.** `write_state` is `sans_objet` for a
read, `non_appliquee` when the request never reached HedgeDoc, and `inconnu`
when the request left and the answer was lost (read timeout, 5xx): the message
then asks you to re-read before replaying. Writes already applied earlier in the
same tool call are named — and a 4xx never counts as applied, since `_hd_request`
returns those responses for each tool to qualify itself (`hedgedoc_create_note`
tells a refused alias from a session problem, `hedgedoc_update_note` a 404 from a
405).

Unchanged: `_hd_request` still returns the raw `httpx.Response`, a 4xx is still
returned rather than raised, and every existing 401/403/404 message is kept word
for word.

The limit that remains: a write whose response is lost leaves a state the client
cannot decide. HedgeDoc exposes neither an idempotency key nor a request id to
check against, and a note created twice differs only by its id — so `inconnu`
means exactly that: re-read the history, do not replay blind.

## License

[Apache-2.0](../LICENSE) — © 2026 inno³ and contributors.
