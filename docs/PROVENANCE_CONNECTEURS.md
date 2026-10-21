# Provenance canonique des connecteurs

État contrôlé hors ligne le 17/10/2026, lot MC. Ce document est la référence
commune aux trois dépôts. Les fichiers installés sont des copies de livraison,
jamais une branche de développement. Leur égalité avec un source ne prouve pas
que le processus actuellement chargé utilise ce source.

Actualisation du 19/10/2026 : consulter [REPRISE.md](REPRISE.md) pour l'état
courant de `main`, du poste et des worktrees. Les bases, comptages et copies
installées ci-dessous sont la photographie du 17/10 ; les chemins MC sont
désormais absents. L'extension métier de `main` compte maintenant 33 outils
en 0.8.7, tandis que sa copie installée reste en 0.8.6 avec 32 outils.

## Dépôts qui font foi

| Repère | Dépôt canonique | Rôle |
| --- | --- | --- |
| F | `/home/jean/ssd_data/jean/RepoGit/inno3/mcp-foss-connectors` | Les huit connecteurs publics ci-dessous |
| E | `/home/jean/ssd_data/jean/RepoGit/inno3-mcp-extensions` | Outils métier Dolibarr, chargés par entry-point |
| M | `/home/jean/ssd_data/jean/RepoGit/mcp-inno3` | CalDAV, CardDAV, IMAP Bluemind, IMAP Fastmail, Forgejo, Hermine, Mastodon, WordPress Commons |

Bases MC vérifiées : F `11d691f`, E `aa9ede6`, M `6e20a6d`. Les trois
worktrees `MC-*` de `_lots_wt` utilisent `lot/2026-10-17-outillage`.
Le checkout canonique E est encore sur `wip/meetingnotes-diffusion-par-principe`
à `a139253` : son emplacement ne suffit pas à identifier la branche à livrer.

## Comparaison des huit copies communes

Comptages AST des outils réellement déclarés, pas des anciens tableaux README.
Les huit fichiers `server.py` divergent. Toutes les copies et leurs tests dans
M restent conservés ; ils sont exclus de sa distribution Python et du repack.

| Connecteur | M | F | Apports propres et décision |
| --- | ---: | ---: | --- |
| Dolibarr | 63 | 78 + 32 E | Les 22 noms propres à M existent tous dans E ; le résumé métier dispose de `dolibarr_get_revenue_summary_inno3`. Utiliser F avec E, jamais le monolithe M. |
| GitLab | 18 | 18 | Aucun outil propre ni signature propre à M ; F porte le contrat de transport. |
| HedgeDoc | 8 | 8 | Aucun outil propre ni signature propre à M ; F porte le contrat de transport et le journal des écritures. |
| Kanboard | 53 | 53 | F contient les lots K/K2 et la gestion d'auteur sans identité primary. Les signatures de cinq outils diffèrent ; la recette doit conserver les usages d'identité. |
| Matrix | 10 | 22 | F ajoute notamment fils, réactions, fichiers et suivi de lecture ; aucun nom propre à M. |
| n8n | 17 | 22 | L'activation POST avec objet vide, utile dans M, est désormais intégrée dans F par MB (`03b4e10`, merge `f72e12d`, numérotation `11d691f`). Aucun portage supplémentaire ici. |
| Nextcloud | 14 | 14 | Aucun outil propre ni signature propre à M ; F porte les correctifs du lot L et le contrat de transport. |
| WordPress | 26 | 34 | F ajoute publications, révisions, taxonomies et contrat de transport. WordPress Commons reste un dérivé distinct dans M, conservé par prudence. |

Les variables lues par `os.getenv`/`os.environ.get` dans M sont toutes présentes
dans F. F ajoute les identifiants Web facultatifs Dolibarr et les sous-sites
WordPress. Cela ne prouve pas une équivalence fonctionnelle exhaustive : les
signatures et les contrats publics évoluent. Aucune particularité privée n'est
effacée. Le maintien séparé de WordPress Commons et sa migration éventuelle
restent **à arbitrer par Benjamin** (B10).

## Copies installées sur disque

Racine lue uniquement : `~/.config/Claude/Claude Extensions/`.
Les identités ci-dessous sont celles des manifestes, sans recopier de réglage
ni de secret. Égalité octet par octet des sources et empreintes Git contrôlées.

| Identité du manifeste | Version installée | Provenance du source | Blob abrégé |
| --- | --- | --- | --- |
| dolibarr-erp-inno3-v2 | 1.8.4 | F, identique à la base MC ; extension métier identique à E | `034a7572d1de` |
| kanboard-inno3-v7 | 0.17.2 | F, identique à la base MC | `a49bfaa321ce` |
| nextcloud-inno3 | 1.4.1 | F, identique à la base MC | `e6ede19b8063` |
| hedgedoc-inno3 | 1.1.3 | M, copie ancienne ; F fait foi en 1.2.0 | `6da51074fef1` |
| wordpress-inno3 | 1.5.2 | F au commit `449a1a7`, différent de F courant 1.6.0 | `15c5e0e157a3` |
| imap-bluemind | 0.9.6 | M, identique à la base MC | `82ca0c1d45a8` |
| imap-fastmail | 0.4.7 | M, identique à la base MC | `d60db8875765` |
| caldav-bluemind | 0.5.4 | M, identique à la base MC | `3bdc31937b87` |
| carddav-bluemind | 1.0.5 | M, identique à la base MC | `e530de85a79e` |
| forgejo-inno3 | 1.0.3 | M, identique à la base MC | `e445219bad9f` |
| hermine-inno3 | 1.2.1 | M, identique à la base MC | `124639fb9b4e` |
| mastodon-multi | 1.0.4 | M, identique à la base MC | `e5193237ee02` |

Aucune extension GitLab, Matrix, n8n ou WordPress Commons trouvée dans les
sous-dossiers actifs de cette racine. Les sauvegardes cachées ne sont pas des
installations actives. Aucune mise à jour applicative effectuée par MC.

## Garde de construction

- F : `scripts/build_mcpb.py` n'accepte que le paquet découvert dans son propre
  dépôt pour le connecteur demandé ; rejet avant toute invocation de pip.
- M : `scripts/mcpb_metadata.py`, partagé par les deux scripts de repack,
  vérifie le nom du projet, le package et les préfixes d'outils. Un serveur
  public doit provenir de F. Renommer une copie privée ne suffit pas.
- Les sources et manifestes sous `dxt/`, `archives/`, `build/` et `dist/` sont
  refusés. Une ancienne archive MCPB reste utilisable comme BASE de dépendances
  et de configuration, jamais comme nouveau source ni manifeste de référence.
- Les outils du manifeste final doivent correspondre exactement au source,
  sans doublon. La version demandée doit concorder avec le manifeste voisin
  et avec `SERVER_VERSION` lorsqu'il existe. Aucune résolution réseau.
- `--tools-from` remplace seulement `tools` et `description`. `name`, `server`,
  `user_config` et dépendances de BASE restent inchangés. Pour un bundle avec
  extension métier, ne pas remplacer une liste combinée par le seul cœur :
  une liste de 110 outils face à 78 outils cœur est refusée, faute de preuve
  des 32 outils supplémentaires par ce repack monofichier.

La garde évite le choix accidentel des copies privées conservées. Elle ne
certifie ni la fraîcheur d'un checkout public arbitraire ni l'état distant.
Vérifier la branche et le commit retenus avant une livraison ; une migration
de configuration ou de dépendances exige sa propre recette.

Preuves et commandes du lot :
[MC.rapport.md](/home/jean/ssd_data/jean/RepoGit/_lots_wt/_execution-2026-10-17/MC.rapport.md).
