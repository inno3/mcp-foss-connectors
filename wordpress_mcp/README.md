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

## Tools (34)

- `wordpress_list_pages` — Liste les pages WordPress
- `wordpress_get_page` — Lit le contenu complet d'une page WordPress (titre, HTML, ACF, métadonnées)
- `wordpress_create_page` — Crée une nouvelle page WordPress
- `wordpress_update_page` — Met à jour une page WordPress existante. Seuls les champs fournis sont modifiés
- `wordpress_delete_page` — Supprime ou met à la corbeille une page WordPress
- `wordpress_list_posts` — Liste les "Posts" natifs WordPress (rest_base="posts")
- `wordpress_get_post` — Lit le contenu complet d'un article WordPress
- `wordpress_update_post` — Met à jour un article WordPress
- `wordpress_create_post` — Crée un article natif WordPress (rest_base="posts")
- `wordpress_delete_post` — Met un article natif à la corbeille, ou le supprime définitivement
- `wordpress_search` — Recherche globale dans WordPress (pages, articles, types custom)
- `wordpress_list_post_types` — Liste les types de contenu du site et, pour chacun, ses taxonomies (avec le `rest_base` à écrire)
- `wordpress_list_custom_posts` — Liste les publications d'un type de contenu personnalisé (CPT)
- `wordpress_update_custom_post` — Met à jour un CPT : champs, ACF et taxonomies du type
- `wordpress_get_custom_post` — Récupère un contenu de type personnalisé (CPT) avec ses champs ACF complets
- `wordpress_create_custom_post` — Crée une publication d'un type personnalisé (CPT), taxonomies comprises
- `wordpress_delete_custom_post` — Supprime ou met à la corbeille une publication d'un type personnalisé (CPT)
- `wordpress_list_revisions` — Liste les révisions d'une publication, de la plus récente à la plus ancienne
- `wordpress_get_revision` — Lit une révision précise, contenu source compris
- `wordpress_restore_revision` — Remet une publication dans l'état d'une de ses révisions
- `wordpress_check_content` — Contrôle l'état d'une publication sans la modifier
- `wordpress_list_media` — Liste la médiathèque WordPress
- `wordpress_upload_media` — Uploade un fichier dans la médiathèque WordPress
- `wordpress_list_acf_field_groups` — Liste les groupes de champs ACF disponibles sur le site
- `wordpress_get_acf_fields` — Récupère tous les champs ACF d'une publication via l'API ACF v3
- `wordpress_list_redirections` — Liste les redirections gérées par le plugin Redirection
- `wordpress_create_redirection` — Crée une redirection 301/302 via le plugin Redirection
- `wordpress_delete_redirection` — Supprime une redirection par son ID
- `wordpress_site_info` — Informations générales sur le site WordPress (nom, URL, version, multisite...)
- `wordpress_list_categories` — Liste les termes de la taxonomie native `category`
- `wordpress_list_tags` — Liste les termes de la taxonomie native `post_tag`
- `wordpress_list_terms` — Liste les termes de n'importe quelle taxonomie, personnalisée comprise
- `wordpress_list_users` — Liste les utilisateurs WordPress
- `wordpress_check_connection` — Teste la connexion et l'authentification à un serveur WordPress

## Notes

- Reads default to `context=edit` so the **raw Gutenberg markup**
  (`<!-- wp:* -->`) is preserved; pass `context=view` for rendered HTML.
  Never rewrite a post from `content_html` (the API's `rendered`): WordPress
  has already stripped the block delimiters there, so writing it back turns
  the post into classic content, which re-enables `wpautop` and injects empty
  paragraphs. The `wordpress_update_*` tools detect that case and return a
  `warning` field alongside the update.
- Revisions are the recovery path: `wordpress_list_revisions` flags which
  revisions still carried block delimiters, `wordpress_restore_revision`
  writes one back. The replaced content itself becomes a revision, so the
  operation is reversible the same way.
- `wordpress_check_content` is a read-only lint (empty paragraphs, missing
  block delimiters, images without alt text, duplicate `h1`). Run it after
  any content write.
- **Custom taxonomies.** The REST API silently drops any field a post type
  does not declare, so writing `tags` on a type without `post_tag` used to
  return a success with no effect. `wordpress_create_custom_post` and
  `wordpress_update_custom_post` now validate taxonomies against the target
  type *before* writing, take any taxonomy through
  `taxonomies='{"blog-type": [3], "global_tags": [368]}'`, and report the terms
  actually recorded (`taxonomies`, `taxonomies_ignored`, `taxonomies_warning`).
  Discovery: `wordpress_list_post_types` lists each type's taxonomies with the
  `rest_base` to write, and `wordpress_list_terms` lists the terms of any of
  them. Terms are addressed by ID only — no name resolution, no term creation —
  and only taxonomies registered with `show_in_rest` are reachable at all.
- Multisite: tools accept a `subsite` argument (e.g. `fr`/`en`) where relevant.
- ACF and the *Redirection* plugin are optional; their tools degrade gracefully
  when the plugin is absent.

## When a call fails, what the message tells you (1.6.0)

The same error contract as the Dolibarr and Kanboard connectors, which was
written after both answered `Erreur : ` — nothing else — in production on
08/09/2026 and 24/09/2026. An empty string separates nothing: a refused
application password, a missing post, an invalid parameter and a passing network
fault all read alike, and the caller cannot tell whether to replay, fix the call
or stop. Five things now hold.

**No message is ever bare.** An httpx exception carrying no text (`ReadError`,
a naked `ReadTimeout`, `RemoteProtocolError`) is rendered with its type and what
that family means — `Erreur réseau ReadError : connexion coupée par le serveur
avant la réponse.` Any other empty exception keeps at least its class name.

**The whole `httpx.TransportError` family is handled.** Only `ConnectError` and
`ReadTimeout` used to be caught — not even `WriteTimeout`; a `ReadError`, a
`PoolTimeout` or a `RemoteProtocolError` went through with neither replay nor
wrapping.

**The REST error code is rendered next to the message.** WordPress's `code`
(`rest_invalid_param`, `rest_cannot_edit`, `rest_post_invalid_id`) says precisely
what is being refused, where the message often just paraphrases it; it used to be
dropped in favour of `message` alone.

**A write is never replayed once the request has left.** Publishing twice is
visible to the public, and only a re-read finds it. Reads (`GET`, `HEAD`,
`OPTIONS`) are replayed on any transport error and on 5xx. Writes are replayed
only when the connection never succeeded (`ConnectError`, `ConnectTimeout`),
which is the only proof WordPress received nothing.

**The error says what became of the write.** `write_state` is `sans_objet` for a
read, `non_appliquee` when the request never reached WordPress or WordPress
refused it before executing it (HTTP 4xx) — replaying is then free of any
duplicate risk — and `inconnu` when the request left and the answer was lost
(read timeout, 5xx): the message then asks you to re-read the object before
replaying. A 401, 403 or 404 writes nothing and keeps its usual message. Writes
already applied earlier in the same tool call are named by their route:
`wordpress_upload_media` posts the file and then sets its title and alt text, so
when the second call fails the message says the media is already in the library.
The upload itself now goes through the shared request path, which is what makes
that possible.

Non-JSON bodies on a 2xx response are still returned as text, unchanged:
`wordpress_check_content` reads rendered HTML that way.

The limit that remains: a write whose response is lost leaves a state the client
cannot decide. The REST API exposes neither an idempotency key nor a request id
to check against, so `inconnu` means exactly that — re-read the object, do not
replay blind. Three read-only paths still call httpx directly and so carry no
write state: the two ACF v3 routes and the XML-RPC version probe. They are
reads, and `_format_error` still names whatever they raise.

## License

[Apache-2.0](../LICENSE) — © 2026 inno³ and contributors.
