# GitLab connector (`gitlab-mcp`)

MCP server for the [GitLab](https://gitlab.com/) REST API v4. Projects, issues, merge requests, repository files & branches, and CI/CD pipelines. Works against gitlab.com or any self-managed instance.

Part of [**mcp-foss-connectors**](../README.md). Configured entirely through
environment variables — no host, login or secret is baked into the code.

## Environment variables

| Variable | Required | Description |
|---|---|---|
| `GITLAB_URL` | no | Base URL of the GitLab instance. Default `https://gitlab.com`. No trailing slash. |
| `GITLAB_TOKEN` | yes | Personal Access Token with the `api` (or `read_api`) scope. |

## Tools (18)

- `gitlab_list_projects` — Liste les projets GitLab auxquels l'utilisateur appartient
- `gitlab_get_project` — Retourne les informations essentielles d'un projet GitLab
- `gitlab_list_issues` — Liste les issues d'un projet GitLab
- `gitlab_get_issue` — Retourne une issue complète avec sa description
- `gitlab_create_issue` — Crée une nouvelle issue dans un projet GitLab
- `gitlab_update_issue` — Met à jour une issue existante dans GitLab
- `gitlab_add_issue_comment` — Ajoute un commentaire (note) sur une issue GitLab
- `gitlab_list_merge_requests` — Liste les merge requests d'un projet GitLab
- `gitlab_get_merge_request` — Retourne un merge request complet avec tous ses détails
- `gitlab_create_merge_request` — Crée un nouveau merge request dans un projet GitLab
- `gitlab_list_repo_tree` — Liste les fichiers et dossiers d'un répertoire du dépôt GitLab
- `gitlab_get_file` — Retourne le contenu d'un fichier du dépôt GitLab (décodé depuis base64)
- `gitlab_list_pipelines` — Liste les pipelines CI/CD d'un projet GitLab
- `gitlab_get_pipeline` — Retourne les détails complets d'un pipeline GitLab avec ses jobs
- `gitlab_whoami` — Retourne les informations de l'utilisateur GitLab authentifié
- `gitlab_create_file` — Crée un nouveau fichier dans un projet GitLab via POST /projects/{id}/repository/files
- `gitlab_update_file` — Met à jour un fichier existant dans un projet GitLab via PUT /projects/{id}/repository/files
- `gitlab_list_branches` — Liste les branches d'un projet GitLab via GET /projects/{id}/repository/branches

## When a call fails, what the message tells you

The same error contract as the Dolibarr and Kanboard connectors, which was
written after both answered `Erreur : ` — nothing else — in production on
08/09/2026 and 24/09/2026. An empty string separates nothing: a refused token,
a missing project, invalid data and a passing network fault all read alike, and
the caller cannot tell whether to replay, fix the call or stop. Five things now
hold.

**No message is ever bare.** An httpx exception carrying no text (`ReadError`,
a naked `ReadTimeout`, `RemoteProtocolError`) is rendered with its type and what
that family means — `Erreur réseau ReadError : connexion coupée par le serveur
avant la réponse.` Any other empty exception keeps at least its class name.

**The whole `httpx.TransportError` family is handled.** Only `ConnectError` and
the two timeouts used to be caught; a `ReadError`, a `PoolTimeout` or a
`RemoteProtocolError` went through with neither replay nor wrapping. A non-JSON
body served with HTTP 200, which is how a proxy error page arrives, is now
reported with an excerpt instead of a parser message, and replayed like a read.

**The API's error code is rendered.** The HTTP status keeps its own message, and
GitLab's own body is quoted rather than dropped — `Erreur 422 : données
invalides. Détail : branch already exists`.

**A write is never replayed once the request has left.** Reads (`GET`, `HEAD`,
`OPTIONS`) are replayed on any transport error, on 5xx and on an unreadable
body. Writes are replayed only when the connection never succeeded
(`ConnectError`, `ConnectTimeout`), which is the only proof GitLab received
nothing. Before this, a `POST /issues` losing its response was replayed twice
and could open the issue three times.

**The error says what became of the write.** `write_state` is `sans_objet` for a
read, `non_appliquee` when the request never reached GitLab or GitLab refused it
before executing it (HTTP 4xx) — replaying is then free of any duplicate risk —
and `inconnu` when the request left and the answer was lost (read timeout, 5xx,
unreadable body): the message then asks you to re-read the object before
replaying. A 401, 403 or 404 writes nothing and keeps its usual message. Writes
already applied earlier in the same tool call are named, so a caller knows what
to re-read rather than guessing which half of the tool ran.

The limit that remains: a write whose response is lost leaves a state the client
cannot decide. The v4 API exposes neither an idempotency key nor a request id to
check against, so `inconnu` means exactly that — re-read the object, do not
replay blind.

## License

[Apache-2.0](../LICENSE) — © 2026 inno³ and contributors.
