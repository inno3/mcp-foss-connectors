## Diagnostic des chemins locaux (1.4.1, non installe)

`local_path` est relatif a NEXTCLOUD_UPLOAD_DIR sur le poste executant le
connecteur, et non au conteneur de la session appelante. Les erreurs rendent
`upload_root` resolu et `upload_root_configured` tel que configure. Les deux
peuvent differer lorsque Downloads est un lien symbolique.
Les protections contre les chemins absolus, .. et les liens sortants restent
actives. Le test du fichier absent n'effectue aucune requete Nextcloud.

# Nextcloud connector (`nextcloud-mcp`)

MCP server for [Nextcloud](https://nextcloud.com/) via WebDAV (files) and the OCS API (shares). List/upload/download/move/copy/delete files, manage shares and search.

Part of [**mcp-foss-connectors**](../README.md). Configured entirely through
environment variables - no host, login or secret is baked into the code.

## Environment variables

| Variable | Required | Description |
|---|---|---|
| `NEXTCLOUD_URL` | yes | Base URL of the Nextcloud instance, e.g. `https://cloud.example.com`. |
| `NEXTCLOUD_USER` | yes | Nextcloud user login. |
| `NEXTCLOUD_APP_PASSWORD` | yes | An *app password* for that user (Settings → Security → Devices & sessions). |
| `NEXTCLOUD_DOWNLOAD_DIR` | no | Only directory `nextcloud_download_file` may write to (`save_path` is always relative to it). Default `~/Downloads`. |
| `NEXTCLOUD_MAX_SAVE_BYTES` | no | Per-file cap for local writes. Default `26214400` (25 MB). |
| `NEXTCLOUD_UPLOAD_DIR` | no | Only directory `nextcloud_upload_file` may read from (`local_path` is always relative to it). Default: same as `NEXTCLOUD_DOWNLOAD_DIR`. |
| `NEXTCLOUD_MAX_UPLOAD_BYTES` | no | Per-file cap for uploads, whatever the source. Default `26214400` (25 MB). |

## Tools (14)

- `nextcloud_list_files` - Liste les fichiers et dossiers d'un chemin Nextcloud (PROPFIND)
- `nextcloud_get_file_info` - Retourne les métadonnées d'un fichier ou dossier (PROPFIND depth=0)
- `nextcloud_download_file` - Télécharge un fichier : contenu texte dans le contexte (≤100 KB), ou `save_path` vers le dossier de téléchargement local (binaires et gros fichiers) ; lecture par plage `offset`/`length`
- `nextcloud_upload_file` - Crée ou remplace un fichier : `content` (texte UTF-8), `content_base64` (binaire : PDF, ODT, XLSX, DOCX…) ou `local_path` (fichier local sous `NEXTCLOUD_UPLOAD_DIR`) ; plafond 25 Mo, `overwrite` explicite, `sha256` déclaré contrôlé avant dépôt, dépôt relu par PROPFIND
- `nextcloud_create_folder` - Crée un dossier dans Nextcloud (MKCOL)
- `nextcloud_delete` - Supprime un fichier ou un dossier dans Nextcloud (DELETE)
- `nextcloud_move` - Déplace ou renomme un fichier/dossier dans Nextcloud (MOVE)
- `nextcloud_copy` - Copie un fichier ou dossier dans Nextcloud (COPY)
- `nextcloud_list_shares` - Liste les partages Nextcloud, avec filtre optionnel par chemin
- `nextcloud_create_share` - Crée un partage Nextcloud
- `nextcloud_delete_share` - Supprime un partage Nextcloud par son identifiant
- `nextcloud_search` - Recherche des fichiers dans Nextcloud par nom ou contenu
- `nextcloud_status` - Retourne les informations de statut et la version du serveur Nextcloud
- `nextcloud_user_info` - Retourne les informations de l'utilisateur Nextcloud connecté

## When a call fails, what the message tells you (1.4.0)

The same error contract as the Dolibarr and Kanboard connectors, which was
written after both answered `Erreur : ` - nothing else - in production on
08/09/2026 and 24/09/2026. An empty string separates nothing: a revoked
application password, a missing folder, a quota refusal and a passing network
fault all read alike, and the caller cannot tell whether to replay, fix the call
or stop. Five things now hold.

**No message is ever bare.** An httpx exception carrying no text (`ReadError`,
a naked `ReadTimeout`, `RemoteProtocolError`) is rendered with its type and what
that family means. Any other empty exception keeps at least its class name.

**The whole `httpx.TransportError` family is handled.** Only `ConnectError` and
`ReadTimeout` used to be caught - not even `WriteTimeout`, which is the one an
interrupted upload produces.

**The error code is rendered**, including sabre/DAV's `<s:message>` as of 1.3.0,
which stays: a WebDAV error body is XML, and truncating it at 200 characters cut
the useful sentence in half.

**A write is never replayed once the request has left - and WebDAV's verbs are
not all of the same kind.** `PROPFIND` is a read despite carrying a request
body, and is replayed like a `GET`; making it fragile would buy nothing.
`MOVE`, `DELETE` and `MKCOL` replayed after a lost response answer 404 or 405 on
an operation that did in fact succeed, which reads as a failure. `POST` to the
sharing API replayed creates a *second* public link with its own token, while
the first stays live and unmentioned. So those are replayed only when the
connection never succeeded (`ConnectError`, `ConnectTimeout`).

This changes what `nextcloud_upload_file` does on a lost response: the read
timeout now surfaces with `write_state: inconnu` instead of being replayed into
a 412. The 412 branch stays - it is what a caller's own replay still hits, and
it still says "the target exists", not "nothing was uploaded".

**The error says what became of the write.** `write_state` is `sans_objet` for a
read, `non_appliquee` when the request never reached Nextcloud or Nextcloud
refused it before executing it, and `inconnu` when the request left and the
answer was lost. 401, 403 and 404 keep their exact message; so does 412, for the
opposite reason - it may well mean an earlier attempt *did* write. Writes already
applied earlier in the same tool call are named by path: uploading a file and
then sharing it is two writes, and when the second fails the message says the
file is already there.

The limit that remains: a write whose response is lost leaves a state the client
cannot decide. Neither WebDAV nor OCS exposes an idempotency key or a request id
to check against, so `inconnu` means exactly that - re-read the resource with
`nextcloud_get_file_info` or `nextcloud_list_shares`, do not replay blind.

## License

[Apache-2.0](../LICENSE) - © 2026 inno³ and contributors.
