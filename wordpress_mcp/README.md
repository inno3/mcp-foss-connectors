# WordPress connector (`wordpress-mcp`)

MCP server for the [WordPress](https://wordpress.org/) REST API (+ [ACF](https://www.advancedcustomfields.com/)). Pages, posts, custom post types, media, categories, users and Redirection-plugin rules. Supports two named environments (`prod`/`test`) and multisite.

Part of [**mcp-foss-connectors**](../README.md). Configured entirely through
environment variables — no host, login or secret is baked into the code.

## Environment variables

| Variable | Required | Description |
|---|---|---|
| `WP_PROD_URL` | yes | Base URL of the production site, e.g. `https://example.com`. |
| `WP_PROD_USER` | yes | WordPress user login for the production site. |
| `WP_PROD_APP_PASSWORD` | yes | WordPress *Application Password* for that user (Users → Profile → Application Passwords). |
| `WP_TEST_URL` | no | Base URL of an optional second (staging) site, reachable via `server="test"`. |
| `WP_TEST_USER` | no | Application-Password user for the test site. |
| `WP_TEST_APP_PASSWORD` | no | Application Password for the test site. |
| `WP_TEST_SERVER_AUTH_USER` | no | HTTP Basic-Auth user, if the staging site sits behind a reverse-proxy auth. |
| `WP_TEST_SERVER_AUTH_PASS` | no | HTTP Basic-Auth password for the staging proxy. |
| `WP_TEST_VERIFY_SSL` | no | Set to `0` to skip TLS verification on the test site (self-signed certs). Default verifies. |

## Tools (26)

- `wordpress_list_pages` — Liste les pages WordPress
- `wordpress_get_page` — Lit le contenu complet d'une page WordPress (titre, HTML, ACF, métadonnées)
- `wordpress_create_page` — Crée une nouvelle page WordPress
- `wordpress_update_page` — Met à jour une page WordPress existante. Seuls les champs fournis sont modifiés
- `wordpress_delete_page` — Supprime ou met à la corbeille une page WordPress
- `wordpress_list_posts` — Liste les "Posts" natifs WordPress (rest_base="posts")
- `wordpress_get_post` — Lit le contenu complet d'un article WordPress
- `wordpress_update_post` — Met à jour un article WordPress
- `wordpress_search` — Recherche globale dans WordPress (pages, articles, types custom)
- `wordpress_list_post_types` — Liste tous les types de contenu disponibles sur le site (pages, posts, CPT...)
- `wordpress_list_custom_posts` — Liste les publications d'un type de contenu personnalisé (CPT)
- `wordpress_update_custom_post` — Met à jour un contenu de type personnalisé (CPT) avec ses champs ACF
- `wordpress_get_custom_post` — Récupère un contenu de type personnalisé (CPT) avec ses champs ACF complets
- `wordpress_create_custom_post` — Crée une nouvelle publication d'un type personnalisé (CPT)
- `wordpress_delete_custom_post` — Supprime ou met à la corbeille une publication d'un type personnalisé (CPT)
- `wordpress_list_media` — Liste la médiathèque WordPress
- `wordpress_upload_media` — Uploade un fichier dans la médiathèque WordPress
- `wordpress_list_acf_field_groups` — Liste les groupes de champs ACF disponibles sur le site
- `wordpress_get_acf_fields` — Récupère tous les champs ACF d'une publication via l'API ACF v3
- `wordpress_list_redirections` — Liste les redirections gérées par le plugin Redirection
- `wordpress_create_redirection` — Crée une redirection 301/302 via le plugin Redirection
- `wordpress_delete_redirection` — Supprime une redirection par son ID
- `wordpress_site_info` — Informations générales sur le site WordPress (nom, URL, version, multisite...)
- `wordpress_list_categories` — Liste les catégories WordPress
- `wordpress_list_users` — Liste les utilisateurs WordPress
- `wordpress_check_connection` — Teste la connexion et l'authentification à un serveur WordPress

## Notes

- Reads default to `context=edit` so the **raw Gutenberg markup**
  (`<!-- wp:* -->`) is preserved; pass `context=view` for rendered HTML.
- Multisite: tools accept a `subsite` argument (e.g. `fr`/`en`) where relevant.
- ACF and the *Redirection* plugin are optional; their tools degrade gracefully
  when the plugin is absent.

## License

[Apache-2.0](../LICENSE) — © 2026 inno³ and contributors.
