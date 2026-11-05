# Historique Nextcloud

## 1.5.0 - 2026-11-05

- `nextcloud_download_file` lit en flux. Après une coupure, il reprend par une plage
  à l'octet atteint, sans attendre tant que les requêtes apportent des octets, puis
  tente un GET sans `Range` si plus rien n'avance.
- Un fichier lu en entier est comparé au `Content-Length`, au `Content-Range` et au
  `getcontentlength` WebDAV (`size_checks`, `size_verified`). Un fichier incomplet
  rend une erreur avec `incomplete`, `bytes_received`, `expected_size`, `last_error`.
  Rien n'est écrit, et un fichier existant reste intact.
- `Accept-Encoding: identity` : plages et tailles portent sur le fichier lui-même.
- `save_path` accepte un chemin absolu ou en `~` sous la racine de téléchargement et
  sous les dossiers de `NEXTCLOUD_SAVE_DIRS` (réglage `nextcloud_save_dirs`). Un
  dossier reprend le nom du fichier distant. Les refus rendent `allowed_roots`.
- La cible est jugée avant le réseau. Un fichier au-delà du plafond est refusé dès
  les en-têtes. Un `Range` ignoré ne rend plus le début du fichier pour un `offset`.
- Champs ajoutés seulement : aucun champ existant renommé, schéma d'entrée inchangé.

## 1.4.1 - 2026-10-17

- Reprise de 183a2a0 : les refus de lecture d'un fichier local rendent la racine
  résolue (`upload_root`) et la racine configurée (`upload_root_configured`).
- La description précise que le chemin est local au poste exécutant le connecteur.
- Les protections de chemins et les règles de transport de main sont conservées.
