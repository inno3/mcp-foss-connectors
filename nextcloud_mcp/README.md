# Nextcloud connector (`nextcloud-mcp`)

MCP server for [Nextcloud](https://nextcloud.com/) via WebDAV (files) and the OCS API (shares). List/upload/download/move/copy/delete files, manage shares and search.

Part of [**mcp-foss-connectors**](../README.md). Configured entirely through
environment variables — no host, login or secret is baked into the code.

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

- `nextcloud_list_files` — Liste les fichiers et dossiers d'un chemin Nextcloud (PROPFIND)
- `nextcloud_get_file_info` — Retourne les métadonnées d'un fichier ou dossier (PROPFIND depth=0)
- `nextcloud_download_file` — Télécharge un fichier : contenu texte dans le contexte (≤100 KB), ou `save_path` vers le dossier de téléchargement local (binaires et gros fichiers) ; lecture par plage `offset`/`length`
- `nextcloud_upload_file` — Crée ou remplace un fichier : `content` (texte UTF-8), `content_base64` (binaire : PDF, ODT, XLSX, DOCX…) ou `local_path` (fichier local sous `NEXTCLOUD_UPLOAD_DIR`) ; plafond 25 Mo, `overwrite` explicite, dépôt relu par PROPFIND
- `nextcloud_create_folder` — Crée un dossier dans Nextcloud (MKCOL)
- `nextcloud_delete` — Supprime un fichier ou un dossier dans Nextcloud (DELETE)
- `nextcloud_move` — Déplace ou renomme un fichier/dossier dans Nextcloud (MOVE)
- `nextcloud_copy` — Copie un fichier ou dossier dans Nextcloud (COPY)
- `nextcloud_list_shares` — Liste les partages Nextcloud, avec filtre optionnel par chemin
- `nextcloud_create_share` — Crée un partage Nextcloud
- `nextcloud_delete_share` — Supprime un partage Nextcloud par son identifiant
- `nextcloud_search` — Recherche des fichiers dans Nextcloud par nom ou contenu
- `nextcloud_status` — Retourne les informations de statut et la version du serveur Nextcloud
- `nextcloud_user_info` — Retourne les informations de l'utilisateur Nextcloud connecté

## License

[Apache-2.0](../LICENSE) — © 2026 inno³ and contributors.
