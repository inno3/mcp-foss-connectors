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

## Tools (14)

- `nextcloud_list_files` — Liste les fichiers et dossiers d'un chemin Nextcloud (PROPFIND)
- `nextcloud_get_file_info` — Retourne les métadonnées d'un fichier ou dossier (PROPFIND depth=0)
- `nextcloud_download_file` — Télécharge le contenu d'un fichier texte (≤100 KB) depuis Nextcloud
- `nextcloud_upload_file` — Crée ou remplace un fichier dans Nextcloud avec le contenu fourni
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
