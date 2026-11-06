# Chemins vérifiés le 03/11/2026

Poste réinstallé du 28 au 30/10/2026 sous `/home/bjean`, disque de 1 To monté sur `~/Sauvegarde`.
Les chemins dont la cible existe ont été corrigés dans ce document. Ceux qui restent en
`/home/jean/...` désignent des emplacements de l'ancien poste qui n'existent plus (worktrees
retirés, anciens environnements) : ce sont des faits datés, à ne pas suivre.

# Lot du 06/11/2026 : WordPress 1.6.1 et HedgeDoc 1.2.1, gabarits `${user_config.x}` non résolus

Cette section prime sur les suivantes pour les réglages de WordPress et HedgeDoc. Worktree
`/home/bjean/RepoGit/_lots_wt/2026-11-06-gabarits-wp-hedgedoc/mcp-foss-connectors`, branche
`lot/2026-11-06-gabarits-wp-hedgedoc`, partie de `main` `6ef1ed5`. Suivi : KB 262 #8074, liée à
la #8072 (Nextcloud) dont la recette a trouvé le défaut. **Commité sur la branche du lot
seulement : ni fusionné, ni installé, ni poussé.** `main` a reçu `ddd8442` (ce document seul)
pendant le lot.

## Faits vérifiés le 06/11/2026

- Claude Desktop transmet `${user_config.x}` tel quel pour un réglage optionnel sans défaut et
  non renseigné (section Nextcloud ci-dessous).
- WordPress installé, 1.6.0, `src/server.py` identique à `main` (sha256 `87b2f271...`) :
  manifeste installé sans défaut pour `wp_prod_subsites` et `wp_test_subsites`. Journal
  `mcp-server-local.mcpb.inno.wordpress-inno3.log` : 54 lignes « WP subsites JSON invalide,
  ignoré : ${user_config.wp_..._subsites} », dont celles du démarrage de 10:15. Effet : sous-sites
  écartés, donc site unique comme voulu, mais avertissement trompeur à chaque démarrage, sans le
  nom de la variable, indistinct d'une vraie déclaration mal formée.
- HedgeDoc installé, 1.2.0, `src/server.py` identique à `main` (sha256 `d68846f8...`) : le
  manifeste installé ne déclare ni `hedgedoc_api_token` ni `HEDGEDOC_API_TOKEN`. Le défaut y est
  **latent** : il ne touche qu'un bundle construit depuis le manifeste du dépôt.
- Mesuré par les tests rouges sur le code de `main` : avec le jeton en gabarit, HedgeDoc envoie
  `Authorization: Bearer ${user_config.hedgedoc_api_token}` tant qu'aucune session n'est ouverte,
  et tente une connexion avant chaque requête en mode anonyme.
- Les manifestes installés diffèrent du dépôt par le nom (`-inno3`), les libellés et les
  descriptions d'outils : surcouche de construction, connue. Seul le numéro de version a été
  comparé pour numéroter : 1.6.0 et 1.2.0 installées, d'où 1.6.1 et 1.2.1.

## Livré, commit du lot

- Manifestes : `"default": ""` sur `wp_prod_subsites`, `wp_test_subsites`, `hedgedoc_api_token`.
- Serveurs : `_setting()` lit chaque réglage ; un texte qui est entièrement un gabarit vaut non
  renseigné, avec l'avertissement « NOM : gabarit non résolu ignoré ». Une valeur qui ne fait que
  contenir le gabarit est gardée. `WP_TEST_VERIFY_SSL` en gabarit reprend son défaut, vérification
  active.
- `tests/test_manifests.py` : `KNOWN_OPTIONAL_WITHOUT_DEFAULT` vide, plus aucune exception admise.
- Nouveaux : `tests/test_wordpress_config.py`, `tests/test_hedgedoc_config.py`, import dans un
  processus séparé ; les variables jouées sont lues dans le manifeste. `CHANGELOG.md` créés pour
  les deux connecteurs, README complétés (WordPress : `WP_*_SUBSITES` manquaient au tableau).
- Aucun outil, paramètre ni champ de réponse modifié.

## Contrôles du 06/11/2026

- Tests rouges d'abord : 5 échecs, 7 réussis sur les deux nouveaux fichiers ; puis 2 échecs sur
  `test_optional_settings_passed_to_env_have_a_default` (HedgeDoc, WordPress) une fois la dette
  retirée de `test_manifests.py`.
- Final : 2 002 réussis, 4 ignorés, `ruff check` propre, `git diff --check` propre. Suite lancée
  sans garde réseau.
- Préexistant sur `main`, non corrigé : `test_hedgedoc.py::TestHelpers::test_extract_note_id_strips_base_and_anchor`
  échoue si `test_manifests.py` est joué avant lui (son `HEDGEDOC_URL=x` est lu au premier
  import). L'ordre alphabétique de la suite complète ne le déclenche pas.

## Limites et non vérifié

- Aucun bundle construit, aucune installation : le comportement réel ne sera vérifié qu'après
  installation et redémarrage du client.
- Hors périmètre, relevé : le manifeste WordPress met `wp_test_verify_ssl` à `"false"` par défaut,
  ce qui désactive la vérification TLS du serveur de test, alors que le code et la description
  visent l'inverse. À arbitrer par Benjamin, non modifié.

## Prochaines actions

1. Benjamin : accord pour fusionner la branche dans `main` local (conflit attendu sur ce seul
   document, deux sections en tête à garder).
2. Construire et installer les bundles WordPress 1.6.1 et HedgeDoc 1.2.1, avec sauvegarde des
   1.6.0 et 1.2.0, puis redémarrer le client.
3. Recette : environnement des deux processus sans `${user_config`, journal WordPress sans
   « JSON invalide », sous-sites inchangés.

# Recette du lot du 01/11 sur réseau fixe, 06/11/2026 à 17:15, lecture seule : conforme

Cette section clôt la recette des quatre correctifs relevés le 30/10 et prime sur la suivante,
qui décrit les essais non concluants depuis le Wi-Fi d'un train. Poste sur le réseau domestique,
poignée TLS de 0,15 s vers les quatre serveurs. Processus de cette session : Dolibarr **1.8.16**
lancé à 17:11, venue de `lot/2026-11-06-dolibarr-actions-balayage` et non de `main` (1.8.15),
Kanboard 0.19.1 identique à `main`, CalDAV 0.6.4 de `lot/2026-11-03-caldav-relecture`. Durées
lues dans le journal du client.

| Lecture | Résultat | Durée |
|---|---|---|
| `list_actions_open(limit=5)` sans filtre | 0 action réelle, `total_exact=true`, budget de 20 s annoncé | 0,1 s |
| `list_actions_open(assigned_to=5, limit=5)` | 0 action réelle, `total_exact=true` | 0,1 s |
| `check_missing_carnets(since="2026-10-01")` | 4 factures lues, 1 facture de carnets `ok`, 5 carnets sans facture, 2 liens à vérifier | 2 s |
| `caldav_list_events` du 30/10 au 04/11, compact | page 1 : 42 sur 59, `truncated=true` ; page 2 : 17, curseur nul ; 59 sans perte | 1 s chacune |
| `caldav_get_event`, invitation Teams | description entière, environ 1 500 caractères, plus coupée à 300 | 8 s |
| `kanboard_my_overdue(with_columns=True)` | 63 retards, tous résolus, `columns_complete=true`, 10 tableaux, 262 compris | 0,7 s |

- Le budget de 20 000 caractères de la forme compacte coupe la première page à 42 événements :
  la plage compte 59 événements, contre 48 le 01/11. Taille exacte de la page non mesurée ici,
  la borne est celle du code et de ses tests.
- La 1.8.16 écarte côté API les événements sans objet (`percent=-1`) : c'est elle qui rend
  l'appel sans filtre quasi immédiat. Ce correctif n'est pas encore dans `main`.
- Les dépassements de 60 s du matin venaient du lien (Wi-Fi de train) et, pour CalDAV, d'un
  Bluemind dégradé : tout répond sur réseau fixe. Le 262 lu en 54 s dans le train tient ici
  dans le budget avec les neuf autres tableaux.

Reste ouvert, hors de ce lot : décider du défaut de `compact` (faux, six tâches planifiées lisent
les participants) ; fusionner les lots 1.8.16 et CalDAV 0.6.4 relève de leurs sessions.

# Recette du lot du 01/11, 06/11/2026 de 10:30 à 11:46, lecture seule, sur Wi-Fi de train

Cette section ne couvre que les quatre correctifs relevés le 30/10, branche
`lot/2026-11-01-correctifs-connecteurs`, fusionnée depuis. Elle ne remplace pas la section
Dolibarr 1.8.15 qui suit.

Conditions de mesure : poste sur le Wi-Fi d'un train, poignée TLS de 1 à 7 s, y compris vers
`debian.org`. Délai d'un outil dans Claude Code : 60 s, lu dans les journaux
`~/.cache/claude-cli-nodejs/*/mcp-logs-*`. Processus chargés à 10:15 : Dolibarr 1.8.15 et
Kanboard 0.19.1 identiques à `main`, extension 0.9.2, CalDAV 0.6.3 depuis
`lot/2026-11-03-caldav-relecture`.

Établi :

- Tri des actions ouvertes : filtrées par tiers 213, puis par projet 258 avec `assigned_to=5`,
  réponse sans 503, balayage complet, 108 et 114 événements système écartés, 0 action réelle.
- Outils MeetingNotes et carnets : 113 outils exposés. `list_meetingnotes` (357 CR),
  `list_meetingnotes_actions` en retard (389) et `list_support_carnets` (37 carnets) répondent.
- `kanboard_my_overdue(with_columns=True)` : 62 retards, 22 cartes résolues, 40 non résolues,
  projets 262 et 283 hors budget de 20 s, `reason=budget`, `columns_complete=false`. Le
  résultat partiel est annoncé comme prévu.

Non concluant sur ce lien, à rejouer sur un réseau stable :

- `list_actions_open` sans filtre, puis avec `assigned_to=5` seul : délai de 60 s dépassé trois
  fois, appels en série. Le 02/11 les mêmes appels répondaient. Le balayage lit environ 8 700
  événements.
- `check_missing_carnets(since="2026-10-01")` : délai dépassé.
- `caldav_list_events` du 30/10 au 04/11 en compact : délai dépassé deux fois, comme pour la
  session du lot CalDAV à 10:34 et 10:35. Connexion à Bluemind en 34 s selon le journal du
  serveur. Le chemin de liste est celui de `main`, la 0.6.3 ne change que la relecture après
  création. Taille sous 20 000 caractères non mesurée en réel.
- Kanboard : le projet 262 tient-il dans le budget de 20 s sur un lien normal ?

Constat indépendant du réseau : la route `agendaevents` ne porte que des événements
automatiques, les actions de suivi sont dans `dolibarr_list_meetingnotes_actions`.

## Rejeu de 11:32 à 11:46, même lien

Le poste était toujours sur le Wi-Fi du train (poignée TLS de 3 à 10 s vers `deb.debian.org`).
Durées lues dans le journal du client.

- `list_actions_open` sans filtre : délai dépassé à nouveau. Avec `date_from="2026-10-01"`,
  filtre côté serveur (`t.datep:>=`) : réponse en 5 s, balayage complet, 344 événements système
  écartés, 0 action réelle. Le coût vient du parcours de tout l'historique. `assigned_to=5` seul
  n'a pas été rejoué : il parcourt le même ensemble, le filtre s'appliquant après coup.
- `kanboard_my_overdue(with_columns=True)` en 22 s : seul le 262 reste hors budget, 36 cartes
  non résolues, le 283 est résolu. `kanboard_get_board(262)` seul : 54 s.
  `kanboard_list_tasks(262)`, donc `getAllTasks` : plus de 60 s. La piste notée à 10:45, lire
  le 262 par `getAllTasks` plutôt que par `getBoard`, n'apporte rien sur ce lien : abandonnée.
- `check_missing_carnets(since="2026-10-01")` : délai dépassé à nouveau.
- `caldav_list_events` compact : délai dépassé sur le 30/10 au 04/11, puis sur la seule journée
  du 06/11. La forme compacte n'est donc pas en cause. La session du lot CalDAV mesure ce même
  matin une liste d'un jour en 2 min 21 s et note Bluemind dégradé depuis 09:48. Taille compacte
  toujours non mesurée en réel.

Suite : sur un réseau stable et un Bluemind rétabli, rejouer `list_actions_open` sans filtre,
`check_missing_carnets`, `caldav_list_events` compact (taille sous 20 000 caractères) et
`kanboard_my_overdue(with_columns=True)`. Si `list_actions_open` dépasse encore 60 s, borner
par défaut le parcours dans le temps ou filtrer les événements système côté serveur. Si le 262
ne tient pas dans 20 s, le budget seul ne suffira pas : son tableau entier est le coût.

# Reprise du 06/11/2026 : Dolibarr 1.8.15 fusionnée, installée et recettée en réel

Cette section prime sur les suivantes pour Dolibarr et la découverte des tâches d'un projet. `main`
contient le correctif (`2939bb0`) par la fusion `7469de9`. Worktree
`/home/bjean/RepoGit/_lots_wt/2026-11-05-dolibarr-taches-masquees/mcp-foss-connectors`, branche
`lot/2026-11-05-dolibarr-taches-masquees`, partie de `2b67497` (1.8.13, pointe de
`lot/2026-11-05-dolibarr-temps-agregats`). Suivi : KB 262 #8073, liée à la #8070 dont le commentaire
2872 a relevé le défaut. Fusion et installation demandées par Benjamin le 06/11/2026.

Installée le 06/11/2026 à 08:29, chargée à 10:15 après redémarrage du client, recette réelle
conforme. **`main` poussé sur `origin` le 06/11/2026, accord de Benjamin : `2e27d59..90a1cad`, 39
commits relus avant envoi (ni `packaging/`, ni branche `vague/`, ni secret, ni nom de client),
plus `90a1cad`, recette d'une autre session commitée pendant la relecture et relue après coup.**
Crochet `pre-push` passé sur `90a1cad`. `sauvegarde/main` reste à `2e27d59`.
Sauvegarde `.backup-dolibarr-erp-inno3-v2-2026-11-06-v1.8.13` dans
`~/Sauvegarde/Archives/claude-extensions-anciennes-versions/`. Retour :
`bascule-dolibarr-1.8.15.sh retour` dans `_lots_wt/2026-11-05-dolibarr-taches-masquees/dist/`, puis
redémarrage.

## Recette réelle du 06/11/2026, lecture seule

Processus lancés à 10:15, `__version__ = "1.8.15"`, serveur identique à `main`. Identifiants seuls,
ce dépôt étant public.

| Appel | Résultat |
|---|---|
| `list_tasks`, projet 13 | tâche 3, `total=1` exact, `project_route=0`, `task_route=1` |
| `list_time_entries`, projet 13, `task_limit=100` | écriture 4, 1 h, `tasks_total=1`, `aggregates_complete=true`, `reason=complete` |
| `list_tasks`, projet 4, visible | 14 tâches par la seule route projet, `task_route=null` |
| `list_tasks`, projet 225, vide | `project_route=0`, `task_route=0`, `exact=true` |
| `list_tasks`, projets 259 et 272 | 2 et 3 tâches retrouvées, `project_route=0` ; 25,33 h sur une tâche du 272 |
| `list_tasks`, projet 317 | « Project not found », erreur conservée : projet supprimé après le 03/11 |

- `GET tasks?sqlfilters=(t.fk_projet:=:ID)` est accepté par la production, sans 400.
- Le projet 225, vide et certifié, prouve que `users/info` confirme `societe client voir` pour le
  compte de la clef : ce droit n'est plus déduit mais lu. Ce projet n'a pas de tiers, que le filtre
  `search_sale` aurait écarté sans ce droit.
- Les 259 et 272, récents, montrent que le masquage ne se limite pas aux projets anciens.

## Mesure du 06/11/2026 : les 222 projets, lecture seule

`dolibarr_list_tasks(project_id=ID, limit=1, cursor=ID)` sur chaque projet de
`dolibarr_list_projects(status="")` : le curseur fait revenir l'identifiant dans `offset`, ce qui
rattache chaque réponse à son projet sans dépendre de l'ordre. Relevé local, hors dépôt :
`_lots_wt/2026-11-05-dolibarr-taches-masquees/dist/mesure-2026-11-06/`.

| Constat | Projets | Tâches |
|---|---|---|
| Route projet non vide | 105 | 769 |
| Route projet vide : 117, chiffre annoncé le 05/11 | 117 | |
| dont tâches retrouvées par `GET tasks` | 20 | 52 |
| dont vides, certifiés par le droit lu | 97 | 0 |
| Non certifiés | 0 | |

- Les 20 projets : 13, 44, 45, 56, 74, 87, 96, 129, 217, 239, 250, 259, 270, 272, 299, 303, 305, 312,
  313, 314. Douze ont un identifiant supérieur à 200, et des tâches créées en septembre et
  octobre 2026 y sont masquées : le défaut touche le travail courant.
- Temps passé porté par ces 52 tâches (`duration_effective`, somme de `spent_seconds`) : **504,25 h**,
  dont 124 h, 98,75 h et 78 h sur les projets 45, 129 et 74. 17 projets en portent, 3 n'en ont
  aucun. Jusqu'à la 1.8.14, `list_time_entries` par projet les annonçait à zéro, totaux certifiés.
  Chiffre lu sur les tâches, non recompté depuis les écritures.
- Un appel a rendu « Connection closed » vers 10:33 ; une autre session a eu la même erreur sur
  `list_actions_open` à 10:32:45, et l'autre processus du connecteur journalise des délais de
  connexion vers Dolibarr à 10:32 et 10:33. Poste sur un Wi-Fi de train (section précédente).
  Relancé, l'appel a abouti. Non imputé à la 1.8.15.

## Fusion et installation du 06/11/2026

- Fusion `--no-ff` dans `main` (`7469de9`), `main` ayant reçu Nextcloud 1.5.0 entre-temps. Seul
  conflit : ce document, deux sections ajoutées en tête, gardées toutes deux.
- Suite sur l'arbre fusionné : 1 979 réussis, 4 ignorés, ruff propre. Sous la garde stricte, les 42
  tests de `tests/test_nextcloud_download_stream.py` échouent, sur leur branche comme ici : ils
  passent par un vrai socket local. Garde `pytest_offline_loopback` (boucle locale seule, dans le
  `dist/` du lot) : 0 connexion hors boucle locale. Garde stricte sans ce fichier : 1 937 réussis.
- Bundle `dist/dolibarr-erp-inno3-v2-1.8.15.mcpb`, construit depuis `main` `7469de9` par
  `construire-1.8.15.py --main`, repack du 1.8.13 contrôlé identique à l'installé : 972 entrées,
  seuls `manifest.json` et `src/server.py` diffèrent. Bundle sha256
  `575593967bfd4af2df7d9e950efc1e5e26df1604b52cf92ce500791b826bd04a` ; serveur sha256
  `e83ec6478106901384da8412c7f7c8c7b18a5579e088af189c1f0d68145f65ac`, identique à l'installé.
- `bascule-dolibarr-1.8.15.sh installer` puis `retour` sur copie : identique au témoin hors
  `__pycache__`, seconde installation refusée. Sur l'extension réelle, `installer` seul. Réglages
  inchangés, par empreinte. Installé identique au bundle, entrée par entrée.
- 113 outils, aucun ajouté ni retiré, aucun schéma d'entrée changé. Quatre descriptions changées :
  `delete_project`, `get_project`, `list_tasks`, `list_time_entries`.
- Banc stdio `dist/banc_taches_masquees.py`, faux Dolibarr sur 127.0.0.1, sorties
  `dist/banc-1.8.13-installe.json` et `dist/banc-1.8.15-bascule.json` :

| Appel | 1.8.13 installée | 1.8.15 basculée |
|---|---|---|
| `list_tasks`, projet 13 | 0 tâche, `total_exact=true` | tâche 3, `project_route=0`, `task_route=1` |
| `list_time_entries`, projet 13 | 0 écriture, `aggregates_complete=true` | écriture 4, 1 h, `tasks_total=1` |
| `list_tasks`, projet visible 4 | une route | une route, `task_route=null` |
| `list_tasks`, projet vide 99 | une route, certifié | trois routes, certifié par `users/info` |

## Objectif

`dolibarr_list_tasks(project_id=13)` rendait `total=0`, `total_exact=true` ;
`dolibarr_list_time_entries(project_id=13)` rendait `tasks_total=0`, `aggregates_complete=true`,
`reason=complete`. Or la tâche 3 porte l'écriture 4, 1 h, `project_id=13`. Rendre les tâches du projet,
et ne plus certifier un total qu'un masquage peut fausser.

## Faits vérifiés

- Reproduit en production le 05/11/2026 vers 20:00, 1.8.13 installée, lecture seule : projet 13 à
  `total=0, total_exact=true` ; `task_id=3` rend l'écriture 4 du projet 13. Projet 4 : `assigned_to`
  null sur ses tâches, la route projet ne rendant ni responsable ni créateur.
- Lu dans le cœur 24.0.1 (copie `_lots_wt/audit-depots-2026-10-23/banc-suite-2610/restauration-privee`) :
  - `Projects::getLines` appelle `getLinesArray(DolibarrApiAccess::$user)`, qui passe le compte en
    `$userp` à `Task::getTasksArray`. Celle-ci écarte une ligne si le projet n'est pas public et que
    `getUserRolesForProjectsOrTasks($userp, null, projectid, 0)` est vide, sans exception pour admin
    ni `projet all lire`. Le test porte sur le projet : tout ou rien par projet.
  - `Tasks::index` (`GET tasks`) : `projet lire`, filtre `t.entity`, `sqlfilters` libre, et
    `search_sale` si le compte n'a pas `societe client voir` (projets des tiers dont il est
    commercial ; un projet sans tiers est alors écarté). Compte externe : `p.fk_soc` de son tiers.
    Ce filtre aussi porte sur le projet entier. Les lignes viennent de `Task::fetch`, avec
    `fk_project` et `fk_user_creat` ; le nettoyage ne retire que `fk_projet`.
  - `User::hasRight` : pas de passe-droit administrateur ; un compte externe n'a jamais
    `societe client voir`. `users/info?includepermissions=1` garde `rights` dans sa réponse et exige
    `user self creer`, `user user lire` ou admin.
- `dolibarr_get_project` n'a jamais lu les tâches, malgré sa docstring.

## Décisions prises en cours de lot, à revoir si Benjamin les conteste

- Base : la 1.8.14 n'étant commitée nulle part, la branche part de la 1.8.13. Version **1.8.15**, la
  1.8.14 restant au lot des agrégats. Le plan du commentaire 2872 (route `inno3dashboard`, connecteur
  « 1.8.15 ») passe donc à 1.8.16.
- `GET tasks` n'est lu que si la route projet ne rend rien : un projet visible coûte une requête, comme
  avant, et les deux filtres étant par projet, une tâche visible prouve qu'elles le sont toutes.
- Zéro des deux côtés : certifié seulement si `users/info` confirme `societe client voir` sur un compte
  interne. Droit lu une fois par processus, lecture en échec non retenue. Sans lui, non certifié.
- `GET tasks` rendant un 404 « No task found » est lu comme vide (`_LIST_404_AS_EMPTY`), comme les
  autres listes : des cœurs antérieurs le font, le 24.0.1 non.
- `assigned_to` : une tâche retrouvée garde null, son créateur n'étant pas la personne affectée.
  Le filtre `assigned_to` ne retient rien en production, avant comme après : documenté, non corrigé.
- `dolibarr_delete_project` : `tasks_remaining` du diagnostic 500 passe par la même lecture, null
  si non certifié.

## Livré, commit du lot

Détail dans `dolibarr_mcp/CHANGELOG.md`, section 1.8.15, et `dolibarr_mcp/README.md`, « Tâches
masquées par la route projet, 1.8.15 ». `_discover_project_tasks` et `_task_route_unfiltered` dans
`server.py`, ajouts additifs `task_discovery` et `tasks_warning`, `reason="tasks_unverified"`.
Aucun paramètre ni aucune clé retirés, aucun test existant modifié.

## Contrôles du 05/11/2026

- Tests rouges d'abord : 26 échecs, 7 réussis sur `tests/test_dolibarr_project_tasks_masked.py` avant
  le code ; puis 1 échec sur le cas 404 « No task found » ajouté ensuite, avant son correctif.
- Final : 1 937 réussis, 4 ignorés, sous garde réseau (`PYTEST_PLUGINS=pytest_offline`, greffon copié
  dans `_lots_wt/2026-11-05-dolibarr-taches-masquees/dist/`), 0 tentative réseau. Départ : 1 902
  réussis, 4 ignorés. Ruff propre, `git diff --check` propre.
- Les données de production (libellé, note, références) ne figurent pas dans les tests, seuls les
  identifiants 13, 3 et 4.

## Non vérifié et limites

- Levé le 06/11/2026 par la recette réelle : le filtre de `GET tasks` est accepté et le droit
  `societe client voir` est confirmé par `users/info`. Production en v23, cœur lu en 24.0.1.
- Ampleur : 117 projets sur 222 annoncent zéro tâche, part réellement vide inconnue.
- Coût : un projet masqué coûte une page de `GET tasks` par centaine de tâches, `Task::fetch` par ligne
  côté serveur, plus `users/info` une fois si le projet est vide.

## Fusion avec la 1.8.14

`main` porte désormais la 1.8.15 : le lot des agrégats, encore non commité, devra prendre un numéro
supérieur (1.8.16) et la route du commentaire 2872 le suivant. Arbitrage à confirmer par Benjamin.
Conflits attendus dans `server.py` (`__version__`, `dolibarr_list_time_entries`), le manifeste, le
README, le CHANGELOG et ce document. Dans `dolibarr_list_time_entries`, la branche `if aggregate:` de
la 1.8.14 rend ses totaux avant le calcul de `aggregates_complete` : y calculer `tasks_known` avant
elle, rendre `complete = next_cursor is None and tasks_known`, `tasks_total` comme ici,
`reason = reason or "tasks_unverified"` si `tasks_known` est faux, et ajouter `task_discovery` et
`tasks_warning`. Sans cela, l'agrégat par projet certifierait encore un total masqué. Ajouter le test
correspondant dans `tests/test_dolibarr_time_aggregate.py`.

## Prochaines actions

1. Fait le 06/11/2026 : redémarrage, processus de 10:15.
2. Fait le 06/11/2026, conforme : recette en lecture seule, processus postérieur à 08:29
   (`ps -o lstart`) :
   `dolibarr_list_tasks(project_id=13, limit=1)` doit rendre la tâche 3, `task_discovery` avec
   `project_route=0` ; `dolibarr_list_time_entries(project_id=13, limit=1, task_limit=100)` doit
   rendre l'écriture 4, 1 h, `aggregates_complete=true`. Un projet visible (4) : une seule route lue.
   Un projet vide : `exact` vrai si le droit est confirmé, sinon `tasks_warning`.
3. Sans objet : `GET tasks` accepte le filtre en production.
4. Fait le 06/11/2026 : 20 des 117 projets portent 52 tâches et 504,25 h, les 97 autres sont vides
   et certifiés. Voir « Mesure du 06/11/2026 ».
5. Suite séparée : filtre `assigned_to` de `dolibarr_list_tasks`, à fonder sur les contacts de tâche.
6. Fait le 06/11/2026 : `main` poussé sur `origin`. `sauvegarde` non mis à jour.

# Reprise du 06/11/2026 : Nextcloud 1.5.1 installée et recettée en réel

Cette section prime sur les suivantes pour Nextcloud. `main` contient la 1.5.0 (`9ff6ab2`) et la
1.5.1 (`de84b07`), qui corrige le défaut de réglages trouvé par la recette réelle ci-dessous. Ces
commits sont sur `origin/main` depuis le push de `6ef1ed5`, fait par une autre session le 06/11.

1.5.1 installée le 06/11 à 10:36 et chargée au redémarrage de 21:15. Sauvegarde
`.backup-nextcloud-inno3-2026-11-06-v1.5.0`, retour `bascule-nextcloud-1.5.1.sh retour` dans
`_lots_wt/2026-11-05-nextcloud-download/dist/`, puis redémarrage. Bundle
`dist/nextcloud-inno3-1.5.1.mcpb` : sha256 `6a37723029057677506d8dcfe0624503d098f1862d43526dfb01db1cc5378028` ;
serveur sha256 `e63a87cd53ec91dadfcb87957a36c177804030560226f491a44b562c34e91a78`, identique à l'installé.

## Recette réelle de la 1.5.1, 06/11/2026 vers 21:20

- Environnement des processus : `NEXTCLOUD_DOWNLOAD_DIR` et `NEXTCLOUD_SAVE_DIRS` vides, plus
  aucun `${user_config`, aucun avertissement au journal.
- PDF de 856 255 octets en relatif, en absolu sous `~/Downloads` et en `~/Downloads/...` : écrit sous
  `/home/bjean/Downloads`, `size_checks` à 856 255 trois fois, trois empreintes égales à celle de la 1.4.1.
- `~/Documents` hors racines : refusé, `allowed_roots=["/home/bjean/Downloads"]`, rien écrit.
- Cible existante sans `overwrite` : refusée avant le réseau. Fichiers de recette mis à la corbeille.

Historique de la 1.5.0 conservé ci-dessous.
Worktree `/home/bjean/RepoGit/_lots_wt/2026-11-05-nextcloud-download/mcp-foss-connectors`, branche
`lot/2026-11-05-nextcloud-download`. Suivi : KB 262 #8072, qui porte le chemin exact du fichier
concerné, absent d'ici parce que ce dépôt est public. Commit, fusion et installation demandés par
Benjamin le 05/11/2026.

La 1.5.0 est installée le 05/11 à 18:39 et chargée au redémarrage du 06/11, processus de 10:15. Une
extension de bureau ne se recharge pas depuis une session. Sauvegarde `.backup-nextcloud-inno3-2026-11-05-v1.4.1`
dans `~/Sauvegarde/Archives/claude-extensions-anciennes-versions/`. Retour :
`bascule-nextcloud-1.5.0.sh retour` dans `_lots_wt/2026-11-05-nextcloud-download/dist/`, puis
redémarrage.

## Recette réelle de la 1.5.0, 06/11/2026 vers 10:30

- Copie saine du PDF de 512 219 octets : entière, `size_checks` à 512 219 trois fois, empreinte
  égale à la copie synchronisée.
- Copie défaillante : `incomplete`, 32 768 octets sur 512 219, `resumes=6`. Le GET sans `Range`
  s'arrête au même octet : **défaut de cette copie côté serveur, établi**. Le connecteur ne peut
  pas faire mieux que l'erreur explicite.
- PDF de 856 255 octets vers un dossier désigné par `/` final : nom distant repris, empreinte égale
  à celle obtenue en 1.4.1. Plage `offset=1000`, `length=100000` : octets identiques au fichier entier.
- `Readme.md` de 1 775 octets dans le contexte : `size_checks` à 1 775 trois fois.
- **Défaut trouvé** : Claude Desktop transmet `${user_config.x}` tel quel quand un réglage optionnel
  sans valeur par défaut n'est pas renseigné. Le manifeste 1.5.0 avait perdu le `"default": ""` du
  dossier de téléchargement : la racine devenait `/home/bjean/${user_config.nextcloud_download_dir}`.
  Les écritures de la recette y sont parties, dossier retiré ensuite. `NEXTCLOUD_SAVE_DIRS`, filtré,
  n'a rien fait. Même gabarit vu dans l'extension WordPress (`WP_PROD_SUBSITES`, `WP_TEST_SUBSITES`).
- 1.5.1 : `"default": ""` sur les quatre réglages optionnels, dossier relatif ou gabarit ignoré avec
  avertissement, plafond non numérique ramené au défaut. Test de manifeste sur les huit connecteurs,
  dette connue nommée pour HedgeDoc et WordPress.

## Objectif

`nextcloud_download_file` : lecture en flux, reprise par `Range` après une `RemoteProtocolError`,
taille finale comparée au `Content-Length` et au `getcontentlength`, erreur explicite si le fichier
est incomplet, `save_path` ouvert aux dossiers autorisés. Critère : un PDF de 512 Ko arrive entier.

## Constaté en réel le 05/11/2026, en 1.4.1 installée, en lecture seule

- Le PDF de 512 219 octets d'un dossier interne échoue à chaque essai : `206`, puis
  `RemoteProtocolError (received 32768 bytes, expected 512219)`. Même message dans le journal du
  connecteur les 02, 04 et 05/11.
- Plages sur cette copie : `0-32767` rendue ; `32767-32768` rend 1 octet sur 2 ; départ à 32768,
  40960, 131072 et 508123 : 0 octet, à chaque requête.
- Deux autres copies du même PDF, ailleurs sur le serveur, même taille : téléchargées entières.
  Un autre PDF de 856 255 octets aussi, plage à 32768 comprise.

**Conséquence : sur cette copie, la reprise par `Range` ne peut pas aboutir.** Hypothèse, non
vérifiée : défaut de stockage de cette copie côté serveur. Un GET sans `Range` n'a pas pu être
essayé : la 1.4.1 envoie toujours `Range`, et l'essai direct demanderait le mot de passe
d'application, volontairement non lu.

## Décisions prises en cours de lot, à revoir si Benjamin les conteste

- **Repli sans `Range`, ajouté à la demande initiale** : c'est la seule voie qui reste pour la copie
  ci-dessus. Tenté après trois requêtes sans progrès, seulement si le serveur a déjà répondu, et si
  les octets à sauter restent sous `NEXTCLOUD_MAX_SAVE_BYTES`.
- `save_path` existait déjà : il est élargi, le schéma d'entrée ne change pas.
- Dossiers autorisés : nouveau réglage explicite `NEXTCLOUD_SAVE_DIRS`, vide par défaut. Le dossier
  d'envoi n'y entre pas d'office : pouvoir lire un dossier ne donne pas le droit d'y écrire.
- Plafond de 25 Mo conservé. Il ne borne plus la mémoire, seulement le disque.
- Une requête qui apporte des octets n'attend pas et ne compte pas comme un échec. Borne : 64 reprises.
- `_write_local_file` remplacée par `_PartFile`. Ses tests sont portés. Le test qui figeait le refus
  de tout chemin absolu vise désormais un chemin hors racines, puisque la demande change ce point.

## Contrôles du 05/11/2026

- Tests rouges d'abord : 27 échecs, 9 réussis sur `tests/test_nextcloud_download_stream.py` avant le
  code. Ce fichier passe par un vrai socket local : c'est h11 qui lève l'exception.
- Final : 1 944 réussis, 4 ignorés, ruff propre, `git diff --check` propre.
- Banc stdio `dist/banc_telechargement.py` du dossier du lot, sous le python système avec le
  `src/lib` installé, contre un faux Nextcloud local, sans réglage lu. Sorties : `dist/banc-*.json`.
  Rejoué sur le bundle extrait puis sur l'installé : mêmes résultats.
- Bundle `dist/nextcloud-inno3-1.5.0.mcpb`, construit depuis `main` `9ff6ab2` : sha256
  `605cc5f5979d5e1a3773e89cbed918b42618f19d98b290f5e2a5a8f88afd362c` ; serveur sha256
  `0c6f43b39e6a9c2e122958cb600f6c875c379a37aa248528dc4261cb73c3ed08`, identique à l'installé.
  960 entrées, seuls `manifest.json` et `src/server.py` diffèrent. Le manifeste installé garde son
  nom et sa commande, et gagne la version et la clef optionnelle `nextcloud_save_dirs`.
- 14 outils, aucun schéma d'entrée changé, seule la description de `nextcloud_download_file` change.
- `bascule-nextcloud-1.5.0.sh installer` puis `retour`, sur copie : identique au témoin, et second
  `installer` refusé. `installer` lancé ensuite sur l'extension réelle, `retour` non. Date et taille
  du fichier de réglages inchangées, contenu non lu.

| Scénario | 1.4.1 installée | 1.5.0 |
|---|---|---|
| Coupure unique à 32768 | entier, 2 GET | entier, 2 GET, `resumes=1` |
| Coupure tous les 32 Ko | erreur réseau, 3 GET | entier, 16 GET, 0,25 s |
| Plage cassée, GET simple correct | erreur réseau, 3 GET | entier, 5 GET, 3,1 s, `range_fallback` |
| Défaut réel, coupure à 32768 partout | erreur réseau, 3 GET | `incomplete`, 7 GET, 6,1 s, aucun fichier |
| Absolu sous `NEXTCLOUD_SAVE_DIRS` | refusé | écrit, identique |
| Absolu hors racines | refusé | refusé sans requête, `allowed_roots` |

## Non vérifié et limites

- La comparaison porte sur la taille et l'ETag, pas sur le contenu.
- 64 reprises à 32 Ko couvrent environ 2 Mo. Le délai d'un appel d'outil côté client n'a pas été mesuré.
- `mcp-inno3/nextcloud_mcp` (1.3.0) reste une ancienne lignée, non modifiée.

## Prochaines actions

1. Benjamin : choisir les dossiers de `nextcloud_save_dirs`, puis redémarrer. Sans cela, seul
   `~/Downloads` reste autorisé.
2. Benjamin : faire examiner ou remplacer la copie défaillante, deux copies saines existent.
3. WordPress 1.6.1 et HedgeDoc 1.2.1 : correctif des réglages en gabarit fusionné dans `main`
   le 06/11/2026, voir la section en tête de ce document, KB 262 #8074.

# Reprise du 03/11/2026 : Dolibarr 1.8.13 fusionnée, installée et recettée en réel le 06/11/2026

Cette section prime sur les suivantes pour Dolibarr, hormis les chemins ci-dessus. `main` contient le correctif
(`ff60747`). Worktree `/home/bjean/RepoGit/_lots_wt/2026-11-02-dolibarr-projet-prov/mcp-foss-connectors`,
branche `lot/2026-11-02-dolibarr-projet-prov`. Suivi : KB 262 #8060. Vérifications, correction, commit,
fusion et installation demandés par Benjamin le 03/11/2026.

Installée le 03/11/2026 à 09:49. **Périmé depuis :** la 1.8.15, construite sur ce correctif, est
l'installé depuis le 06/11/2026 ; son retour arrière est celui de la section du 06/11, pas
`bascule-dolibarr-1.8.13.sh`, qui refuserait d'ailleurs une version autre que 1.8.12. Ce dépôt public
reste non poussé.

## Recette réelle du 06/11/2026, en 1.8.15, sur le projet de test 324

Processus chargés à 10:15, `__version__ = "1.8.15"`, serveur identique à `main` et porteur du code de
la 1.8.13. Le 317 ayant été supprimé, un projet de test sans tiers a été créé en brouillon.

| Appel | Résultat |
|---|---|
| `list_projects(status="0,1,2")` | refus explicite, `status=""` proposé |
| `update_project(999999, status=9)` | refus avant écriture, pas de 404 : aucun appel parti |
| `update_project(999999, opp_status="PROSPEC")` | refus avant écriture, même preuve |
| `create_project(status=0, opp_status="qualification")` | 324, `PJ2611-0229`, sans avertissement ; relu `opp_status="2"`, `opp_percent="20.00"`, `usage_opportunity=1` |
| `update_project(324, opp_status="prospect")` | sans `update_warning` ; relu `"1"`, `"0.00"`, `1` |
| `update_project(324, status=1)` | sans `update_warning` ; relu `status="1"`, là où la 1.8.12 ne changeait rien |

- Effets de bord : le passage à `status=1` par PUT a créé un dossier Nextcloud vide sous `/Interne`
  (NextBarr, `PROJECT_MODIFY`), comme prévu par la source. Aucun tableau Kanboard.
- Constaté aussi : le 316, renommé `PJ2610-0223` par Benjamin, a vu son dossier Nextcloud suivre le
  renommage. Il porte désormais `opp_status="2"`, `opp_percent="20.00"`, `usage_opportunity=1`.
- Le dossier Nextcloud du 317 a survécu à la suppression du projet, comme prévu.
- Non couvert en réel : `update_warning` et `enrich_warning` sur une réponse qui ne confirme pas,
  `status=2`, `won`. Banc et tests seulement.

## Constaté en réel le 03/11/2026, en 1.8.12, sur le projet de test 317

- `opp_status="prospect"`, valeur documentée : succès annoncé, statut passé de `1` à `null`.
  Remis à `1` ensuite, `date_end` du 317 laissée au 31/12/2026 par le test des dates.
- `status=0` : succès annoncé, `updated_fields` cite `fk_statut`, statut relu toujours `1`.
- `status="0,1,2"` sur la liste : `total=0` annoncé exact, alors qu'un projet répondait à la
  recherche ; `status="1"` et `status=""` le trouvent.
- Brouillon : projet 318, `PJ2611-0224`, `status=0`, sans avertissement, sans dossier ni tableau.
- `date_end` en texte `AAAA-MM-JJ` : écrite et relue correctement.
- Le projet 317 a créé un dossier Nextcloud vide sous `/Interne` (NextBarr, `PROJECT_VALIDATE`),
  aucun tableau Kanboard. NextBarr ne traite pas `PROJECT_DELETE` : le dossier survivra au
  projet. Le dossier du projet 316 porte `PROV` dans son nom.

## Lu, non rejoué en réel

- Sans `usage_opportunity`, la carte du projet masque l'opportunité ; le POST minimal le laisse
  à 0. `opp_percent` n'est pas déduit du statut et sert au montant pondéré du tableau de bord.
- Sauvegarde de production de mars 2026, cette table et six réglages seulement : dictionnaire
  identique à celui du cœur. Possiblement modifié depuis, aucune route d'API ne l'expose.
- `KANBOARDLINK_AUTO_CREATE=1` et sans statut déclencheur configuré : un statut à 100 % crée un
  tableau Kanboard. **`won` en 1.8.13 le fera, ne pas l'essayer sur un projet de test.**
- `PROJECT_MODIFY` déplace le dossier NextBarr si son chemin attendu change : le renommage de la
  référence du 316 devrait renommer son dossier, et `status=2` l'envoyer aux archives.

## Livré

Détail dans `dolibarr_mcp/CHANGELOG.md`, section 1.8.13. `opp_status` traduit (codes, alias,
identifiants), `opp_percent` et `usage_opportunity=1` envoyés avec lui, valeur inconnue refusée
avant écriture. `status` de `dolibarr_update_project` écrit par le bon champ, valeurs 0, 1, 2.
`update_warning` et `enrich_warning` si la réponse du PUT ne confirme pas. Filtre de statut de la
liste contrôlé. `opp_percent` et `usage_opportunity` en lecture. Diagnostic « renommage cassé »
retiré de `dolibarr_delete_project`. `enrich_warning` passe par `_format_error`.

Test historique adapté : `test_full_payload_3_steps` figeait `PROSPEC`, valeur inconnue du
dictionnaire que Dolibarr efface. Aucun test supprimé.

## Contrôles du 03/11/2026

- Tests rouges d'abord : 67 échecs sur `tests/test_dolibarr_project_opportunity.py` avant le code.
- 1 902 réussis, 4 ignorés, aucune tentative réseau, ruff propre, `git diff --check` propre.
- 113 outils, aucun schéma d'entrée changé, cinq descriptions changées, deux assistants ajoutés.
- Banc stdio `dist/banc_opportunite.py` sur 1.8.12 et 1.8.13 : sorties dans `dist/banc-opp-*.json`.
  Validation refusée et relecture en échec donnent bien leurs avertissements sur les deux.
- `dist/bascule-dolibarr-1.8.13.sh installer` puis `retour`, sur copie : identique au témoin, et
  second `installer` refusé. `installer` lancé ensuite sur l'extension réelle, `retour` non.
- Bundle `dist/dolibarr-erp-inno3-v2-1.8.13.mcpb`, construit depuis `main` `ff60747` : sha256
  `a159919cd473d6c5d4e78867d2f84f3230f683a0686f24201aecc77969960a3a` ; serveur sha256
  `2133771bb24d0fc6c0483cdbf111ffe7acf7af50ca3e70b519c55bec63f180fc`, identique à l'installé.
  Seuls `manifest.json` et `src/server.py` diffèrent. Banc rejoué sur ce bundle, réglages intacts.

## Limites

- Clôture par `status=2` : ni `PROJECT_CLOSE`, ni date, ni auteur de clôture, faute de route d'API.
- Des projets écrits par les versions antérieures peuvent porter un statut sans `opp_percent` ni
  `usage_opportunity`. Non mesuré, la lecture n'existe qu'à partir de la 1.8.13.
- `dolibarr_list_proposals` porte le même filtre de statut sans contrôle. Non touché, non testé.
- Événements d'agenda des tests : probables d'après les réglages de mars, non lisibles par le
  connecteur.

## Suite ordonnée

1. Benjamin : supprimer le projet de test 324 dans Dolibarr, puis les dossiers Nextcloud
   `/Interne/[TEST connecteur Dolibarr 1.8.12] à supprimer PJ2611-0223` et
   `/Interne/[TEST connecteur Dolibarr 1.8.15] à supprimer PJ2611-0229`.
2. Benjamin : décider le push de ce dépôt public.
3. Facultatif : mesurer les projets qui portent un statut d'opportunité sans `opp_percent` ni
   `usage_opportunity`, écrits par les versions antérieures.

Fait depuis le 03/11 : 317 et 318 supprimés, référence du 316 renommée, dossier du 316 renommé avec.

# Reprise du 02/11/2026 : Dolibarr 1.8.12 fusionné, installé et recetté, ce dépôt public non poussé

Cette section prime sur les suivantes ; la suivante décrit la 1.8.11, dont la recette reste
valable. `main` contient ce lot (`18f9456`, réunion `aceb99b`). Worktree
`/home/bjean/RepoGit/_lots_wt/2026-11-02-dolibarr-projet-prov/mcp-foss-connectors`, branche
`lot/2026-11-02-dolibarr-projet-prov`. Suivi : KB 262 #8060. Portage de `ref="auto"`, fusion et
installation demandés par Benjamin le 02/11/2026.

Installé à 20:59, chargé à 21:05 après redémarrage du client. **Ce dépôt public reste non
poussé. Le projet de test 317 reste à supprimer dans Dolibarr.**

## Faits vérifiés

- Projet 316 relu en production : `status=1`, `ref=PROV`. La validation est donc bien appelée.
- Copie locale du cœur 24.0.1 (`_lots_wt/audit-depots-2026-10-23/banc-suite-2610/restauration-privee`) :
  `Project::setValid()` fait seulement `UPDATE fk_statut = 1`, sans toucher à `ref`. Elle
  **ne renomme rien**, pas même `(PROVxxx)`, contrairement au commentaire de `ee2a12f`.
- `api_projects::post()` numérote via `PROJECT_ADDON`, dès l'INSERT, si `ref` vaut `auto`.
- `uk_projet_ref (ref, entity)` est unique : « PROV-1 » désigne ref `PROV`, entité 1.
- `dolibarr_get_project(ref="PROV")` rend le projet 316, qui occupe donc cette référence.
- `ee2a12f` (mcp-inno3, 23/06/2026) avait corrigé la cause avec `ref="auto"` ; le test réel
  du 12/07 sur KB 6890 a donné `PJ2607-0206`. Le dépôt public n'en gardait aucune trace.
  Régression probable à la bascule de la source vers ce dépôt en août : **hypothèse**.

## Livré dans ce lot

- Étape 1 de `dolibarr_create_project` : `ref="auto"` au lieu de `"PROV"`. Les trois étapes
  restent, pour le trigger `PROJECT_VALIDATE`.
- `_read_back_project` relit le projet dans tous les cas, brouillon et validation en échec
  compris. `ref` et `status` rendus sont ceux relus ; `ref` vaut `null` s'il n'est pas confirmé.
  Le statut attendu ne sert que si le statut relu est illisible.
- `validate_warning` (`status=1`) et `refetch_warning` (`status=0`), additifs : référence relue
  à `PROV` ou `auto`, relecture impossible ou sans référence, statut inattendu. Plus de
  `except: pass`. `validation_warning` reste pour la validation en échec ; texte corrigé.
- `dolibarr_get_project` : `opp_status` et `opp_amount`, bruts, toujours présents.

Trois assertions existantes changent, à la demande : `tests/test_dolibarr.py` attendait `PROV`
à l'étape 1, en brouillon et après validation refusée. Elles attendent `auto` et la référence
relue, et les deux derniers cas simulent la relecture. Aucun test supprimé ou ignoré.

| Contrôle | Résultat |
| --- | --- |
| Base `main` | 1 786 réussis, 4 ignorés |
| Premier correctif, avant modification du serveur | 15 échecs, 8 réussites |
| Portage de `ref="auto"`, avant modification du serveur | 24 échecs, 18 réussites |
| Mêmes tests après correctif | 42 réussites |
| Suite complète sous garde réseau | **1 823 réussis, 4 ignorés**, aucune tentative réseau |
| Manifestes | 26 réussis, seule la version change |
| Ruff, `git diff --check` | propres |

Comparaison AST avec `main` : `_read_back_project` ajouté, `dolibarr_create_project` et
`dolibarr_get_project` modifiés, aucune signature changée. SHA-256 du serveur :
`545e9eb75ebac73b6997d2303ca036ef0d92e56bf8bcdf509b0b130a9cfd8454`.

## Livraison

`/home/bjean/RepoGit/_lots_wt/2026-11-02-dolibarr-projet-prov/dist/`, hors dépôt :
`dolibarr-erp-inno3-v2-1.8.12.mcpb` (sha256 `7886d1cd7410c6e9...22899d688`),
`construire-1.8.12.py`, `bascule-dolibarr-1.8.12.sh`, `banc_projets.py`, `comparer_schemas.py`
et les relevés `banc-1.8.11-avant.json`, `banc-1.8.12-installe.json`.

- Repack du bundle 1.8.11, contrôlé identique à l'installé : 972 entrées, seuls `manifest.json`
  (version) et `src/server.py` diffèrent. Extension 0.9.2 et son unique `dist-info` inchangés.
- `src/server.py` installé identique à `main`. Réglages inchangés, par empreinte. Sauvegarde :
  `~/Sauvegarde/Archives/claude-extensions-anciennes-versions/.backup-dolibarr-erp-inno3-v2-2026-11-02-v1.8.11`.
- Retour : `bascule-dolibarr-1.8.12.sh retour`, puis redémarrage. Répété sur une copie : arbre
  identique au témoin hors `__pycache__`, seconde installation refusée.
- Sous `/usr/bin/python3` avec `src/lib` : 113 outils uniques, aucun ajouté ou retiré, aucun
  schéma d'entrée changé, descriptions de `dolibarr_create_project` et `dolibarr_get_project` seules.

Banc local : connecteur lancé comme par le client, sur stdio, contre un faux Dolibarr sur
127.0.0.1 qui reprend le comportement lu dans la source du cœur.

| Enchaînement | 1.8.11 | 1.8.12 installé |
| --- | --- | --- |
| Création complète | `ref=PROV`, `status=1`, aucun avertissement | `PJ2611-0042`, `status=1` |
| Création suivante | Erreur 500, `Duplicate entry 'PROV-1'` | `PJ2611-0043`, `status=1` |
| Brouillon | Même erreur 500 | `PJ2611-0044`, `status=0` |
| `ref` au POST | `PROV` | `auto` |

## Recette réelle de la 1.8.12, 02/11/2026 vers 21:07, par le connecteur

Sur demande de Benjamin, un projet de test en production, sans tiers. Le projet 316 portait
encore `PROV` pendant ce test.

- `dolibarr_create_project` avec payload complet : `project_id=317`, `ref=PJ2611-0223`,
  `status=1`, aucun avertissement. Le doublon sur `PROV` ne bloque donc plus la création.
- Relecture par id et par référence, mêmes valeurs : `status="1"`, `opp_status="1"`,
  `opp_amount="1.00000000"`, `budget="1.00000000"`, description et note publique présentes.
- Projet 316 sans opportunité : `opp_status=null`, `opp_amount=null`. Il n'a pas été modifié.
- `opp_status` et `opp_amount` sortent en chaînes : l'identifiant et un montant à 8 décimales.
- Effets éventuels des triggers `PROJECT_CREATE` et `PROJECT_VALIDATE` non observés.

## Limites et non vérifié

- La recette réelle couvre une création validée. Brouillon, validation refusée et relecture en
  échec ne sont vus que par les tests, et le brouillon aussi par le banc.
- `ref="auto"` est constaté sur l'instance inno³, dont la version n'est pas relue ici, et lu dans
  le cœur 24.0.1. Le README annonce Dolibarr 16+ : une instance qui garderait `auto` est signalée.
- Changement visible : un brouillon consomme un numéro dès sa création.
- Refus d'une nouvelle création tant que 316 porte `PROV`, sous 1.8.10 et 1.8.11 : déduit de
  l'index unique, non rejoué sur la production.
- `create_project` et `update_project` documentent `opp_status` en texte (prospect...), mais
  `Project::update()` met `fk_opp_status` à NULL si la valeur n'est pas numérique : perte
  silente probable, lue dans la source, hors périmètre.

## Suite ordonnée

1. **Benjamin** : supprimer le projet de test 317 (`PJ2611-0223`) dans Dolibarr.
2. **Benjamin** : renommer la référence du projet 316 dans Dolibarr. Elle est fausse, mais ne
   bloque plus les créations depuis le chargement de la 1.8.12.
3. Décider le push de ce dépôt public, déjà ouvert plus bas, avec ce lot en plus.
4. À décider : `opp_status` en texte, voir les limites ci-dessus.

---

# Reprise du 02/11/2026 : Dolibarr 1.8.11 installé et recetté, ce dépôt public non poussé

Cette section prime sur les suivantes. `main` réunit le lot du 01/11, le lot du 02/11 et les
cinq commits de `vague/2026-11-01-c03` (Kanboard 0.19.1, textes C01, filtre des événements
système). **Ces cinq commits sont repris un à un, pas fusionnés** : `packaging/remise-l01/`
nomme les outils de l'extension privée et ne doit pas entrer dans l'historique de ce dépôt
public. Il reste lisible dans la branche locale `vague/2026-11-01-l01`. Ne pas pousser les
branches `vague/`, ni utiliser `git push --all` ou `--mirror`.

Contrôlé : l'arbre de `main` égale celui d'une fusion complète, à `packaging/` près. Kanboard,
HedgeDoc, Nextcloud et WordPress ont un `server.py` identique à l'installé. Dolibarr 1.8.11
ajoute le filtre C03 et les textes à la 1.8.10 précédente. Fichiers installés le 02/11/2026,
sauvegarde `.backup-dolibarr-erp-inno3-v2-2026-11-02-v1.8.10`. Chargé au prochain redémarrage
du client. Sur les données
actuelles, le filtre rendra 0 action par défaut, 8 654 événements automatiques étant écartés.

Repère avant fusion : tag local `repere/avant-fusion-2026-11-02`. Aucune branche supprimée.
Le worktree d'intégration est sous
`/home/bjean/RepoGit/_lots_wt/2026-11-02-dolibarr-actions/integration/`.

## Recette réelle de la 1.8.11, 02/11/2026 à 20:13, lecture seule par le connecteur

Processus chargés à 20:12, après l'installation de 15:36.

- `dolibarr_list_actions_open(limit=3)` : 0 action, `excluded_system=8667`, balayage complet.
  Avec `assigned_to=5` : 0 action, 5 947 exclus. Ce 0 vaut pour cette route : il ne prouve pas
  l'absence d'engagement dans les autres modules.
- `include_system=True` rend ces événements, `total=5947` pour `assigned_to=5`, tri continu à
  `cursor=100`. Août 2022 avec `date_from` et `date_to` : 7 événements. Date invalide refusée.
- Un premier comptage donnait 5 946 exclus et 5 947 au total : refait, les deux valent 5 947.
  Un événement a été créé entre les deux appels, la route n'offre pas d'instantané.
- `list_meetingnotes_actions` garde `deadline_raw`, `overdue_as_of` et sa note de pagination.
  `list_meetingnotes`, participants et documents répondent. Aucun outil d'écriture appelé.

## Suite

1. Retour si besoin : `bascule-dolibarr-1.8.11.sh retour`, puis redémarrage. Bundle et
   script, répété sur une copie, dans
   `/home/bjean/RepoGit/_lots_wt/2026-11-02-dolibarr-actions/dist/` : sha256 `02701eb5...a84a9e`,
   construit depuis `e7d15d1` et `0941172`, 113 outils, seul `dolibarr_list_actions_open` change
   de schéma (`include_system`, `date_from`, `date_to`). Recette : `limit=3` doit annoncer
   `excluded_system`, et `manifest.json` 1.8.11. Non joué contre l'instance.
   L'archive `C03-actions-ouvertes/artefacts/dolibarr-1.8.10-py314.mcpb` ne doit pas servir.
2. Décider le push de ce dépôt public, après relecture de ce qu'il recevrait. `main` de
   `inno3-mcp-extensions` et de `mcp-inno3` sont poussés le 02/11/2026. Leurs crochets `pre-push`
   n'avaient pas vérifié ce qui partait, suites rejouées à la main. Crochets des trois dépôts
   réparés le 02/11/2026 : ils testent le commit poussé dans un clone temporaire et refusent un
   push non vérifiable. Fichiers locaux, non suivis : à reposer si un clone est recréé.
3. Tests réels avec clé, hors session.

---

# Reprise vérifiée le 02/11/2026 : relevé Dolibarr, bundle 1.8.10 installé et recetté

Cette section prime sur les suivantes. Branche `lot/2026-11-02-dolibarr-actions`,
worktrees sous `/home/bjean/RepoGit/_lots_wt/2026-11-02-dolibarr-actions/`, base
`lot/2026-11-01-correctifs-connecteurs`, qui est l'état installé. Commits : `2592318`
ici, `d6d9ba0` dans l'extension. **Rien n'est poussé ni fusionné.** Installé sur place le
02/11/2026 à 10:01 avec l'accord de Benjamin, sauvegarde
`.backup-dolibarr-erp-inno3-v2-2026-11-02-v1.8.9` dans
`~/Sauvegarde/Archives/claude-extensions-anciennes-versions/`. Les fichiers installés sont
identiques au bundle vérifié. Client redémarré, processus chargés à 11:01.

## Avant toute fusion : la vague du 01/11

Vérifié le 02/11/2026 à 12:30. `_lots_wt/vague-2026-11-01/` porte des branches dans ses
propres dépôts git (`L01-connecteurs/travaux/coeur.git`, `metier.git`, `prive.git`,
`L02-imap/travaux/references/.git`, `C03-actions-ouvertes/travaux/coeur.git`). Un
`for-each-ref` dans les dépôts canoniques ne les voit pas. Elles portent le code installé
de Kanboard 0.19.1, CalDAV 0.6.1, IMAP 0.10.2 et Fastmail 0.5.2.

**Deux « Dolibarr 1.8.10 » différents existent.** Celui-ci est installé (extension 0.9.1).
Celui de C03 (`929ba85`, archive `C03-actions-ouvertes/artefacts/dolibarr-1.8.10-py314.mcpb`)
filtre les événements système mais embarque l'extension 0.9.0 : l'installer retirerait
deadline brute et plafond de page. Ne pas l'installer.

Fusion simulée dans des copies jetables, aucun dépôt canonique touché :

| Dépôt | Méthode | Conflits | Suite | Écart avec l'installé |
|---|---|---|---|---|
| `mcp-foss-connectors` | commits de `vague/2026-11-01-c03` repris un à un | `docs/REPRISE.md`, `dolibarr_mcp/CHANGELOG.md` | 1 786 réussis, 4 ignorés | Kanboard, HedgeDoc, Nextcloud, WordPress identiques ; Dolibarr : C03 et textes en plus |
| `inno3-mcp-extensions` | fusion de ce lot et de `vague/2026-11-01-l01` | `docs/REPRISE.md` | 522 réussis | une ligne de docstring |
| `mcp-inno3` | avance rapide sur L01, puis `cf9b401`, `30a8fa0`, `f5f7be3` repris un à un | `docs/REPRISE.md`, `pyproject.toml`, `uv.lock` | 1 451 réussis | IMAP, Fastmail, CalDAV, CardDAV, Forgejo, Hermine, Mastodon identiques |

À respecter : ne pas fusionner L02 directement, il ramènerait les copies de connecteurs
retirées le 22/10 (`c310cd7`) et des commits antérieurs au retrait des trailers ; la
différence de 451 tests avec L02 seul vient de ces copies. Prendre pour `pyproject.toml` et
`uv.lock` la version de L01 avec un numéro au-dessus (0.1.34). Numéroter 1.8.11 et 0.9.2.
Laisser `packaging/remise-l01/` hors du dépôt public : ce manifeste nomme 23 outils de
l'extension privée. Passer le test de tri réel en `include_system=True`, sinon il tourne à
vide. Importer d'abord les branches de la vague dans les dépôts canoniques, et ne supprimer
aucune branche.

## Recette réelle du 02/11/2026, 11:56, lecture seule par le connecteur

- `dolibarr_list_meetingnotes_actions(limit=300, status=0)` arrive en ligne : 58 actions sur
  1 369, `limit_effective=80`, `page_size_capped=true`, `next_cursor=58`, `next_page` nul,
  `deadline_raw` présent, `overdue_as_of=2026-11-02`, `overdue_timezone=Europe/Paris`. Sans
  le correctif, cet appel était refusé par le client.
- Reprise à `cursor=58` : actions suivantes, date d'échéance continue. Dernière page de
  `overdue_only=true` à `cursor=380` : 1 action, 381 au total, `next_cursor` nul, pas de note.
- Retards contrôlés à la main sur trois échéances : 228, 137 et 2 jours, justes au 02/11/2026.
- `dolibarr_list_actions_open` : sans filtre et `assigned_to=5`, sans 503, dates croissantes,
  continues à `cursor=100`.
- `list_meetingnotes`, `get_meetingnote` avec `decisions_list`, `list_meetingnote_participants`,
  `list_meetingnote_documents` répondent. Les outils d'écriture MeetingNotes sont exposés avec
  leur schéma, aucun n'a été appelé.

## Faits vérifiés

- Le relevé des 30/10, 01/11 et 02/11 visait des processus antérieurs au redémarrage du
  client du 02/11 à 08:17 : le dernier 503 du journal date de 07:50. Les fichiers 1.8.9
  et 0.9.0 étaient sur disque depuis le 01/11 à 17:26. Depuis le redémarrage, plus de 503
  et 113 outils exposés.
- « Dolibarr ERP inno³ » est le libellé de `local.mcpb.inno.dolibarr-erp-inno3-v2`. Un
  seul répertoire, deux processus lancés sur le même `src/server.py`.
- `mcp-inno3/dxt/dolibarr/manifest.json` (0.9.0) est un manifeste historique et
  `mcp-inno3/dolibarr_mcp` n'a pas produit le bundle : empreintes différentes. Le 0.9.0
  de l'extension est une coïncidence de numéro.
- Tri : l'alias `t` est juste, c'est la colonne qui ne l'était pas. La route fait
  `FROM llx_actioncomm AS t` et trie par défaut sur `t.id`, lu dans la copie locale du
  cœur 24.0.1. Le tri `t.datep,t.id` de `34fde26` répond et reste croissant à `cursor=100`.
- Plafond mesuré dans Claude Code : 66 actions réelles acceptées, 80 refusées (50 171
  caractères annoncés), 120 (76 287) et 300 (196 465) aussi.
- `days_overdue` n'est plus à 0 : l'extension le recalcule depuis le lot du 27/09. Vu en
  production : 228 jours pour une échéance au 19/03/2026.
- Lectures réelles par le connecteur de session, donc en 1.8.9 / 0.9.0 :
  `list_actions_open` (avec et sans `assigned_to`), `list_meetingnotes`, `get_meetingnote`
  avec `decisions_list`, `list_meetingnote_participants`, `list_meetingnote_documents`,
  `list_meetingnotes_actions`.
- Le code 0.9.1 : suite de l'extension 516 réussis, cœur 1 733 réussis et 4 ignorés, ruff
  propre. Mode réel des tests joué sur un banc local fictif, aucune écriture reçue. Rejeu
  sans réseau de 300 actions réelles : pages de 53 à 58 actions, 35 420 caractères au plus,
  300 actions uniques couvertes.

## Bundle

`/home/bjean/RepoGit/_lots_wt/2026-11-02-dolibarr-actions/dist/`, hors dépôt :
`dolibarr-erp-inno3-v2-1.8.10.mcpb` (sha256 `2023d225...7ea76d`), le 1.8.9 de retour et
`bascule-dolibarr.sh`, répété sur une copie. Repack d'une copie de l'installé : seuls
changent la version du manifeste, `__version__`, l'extension et son unique `dist-info`.
Poignée de main sous `/usr/bin/python3` : 1.8.10, 113 outils, schémas d'entrée identiques,
seule la description de `dolibarr_list_meetingnotes_actions` diffère.

## Non vérifié, limites

- Les fichiers de tests réels n'ont pas été joués contre l'instance, faute de clé hors du
  connecteur : leur mode réel n'a tourné que sur un banc local. La recette ci-dessus couvre
  le même code par le connecteur.
- Version de production non lue : `cloudron exec` a été refusé par les permissions de la
  session, sans tentative par une autre voie. 23.0.3 selon le relevé, 24.0.1 selon la mémoire.
- Le plafond vient de Claude Code. Celui de Cowork et de Claude Desktop n'est pas mesuré.
- `limit` passe de 500 à 80 : tout appelant qui lit une seule page doit suivre `next_cursor`.
- `dolibarr_list_actions_open` reste bruité : `percent<>100` inclut les événements
  automatiques à -1, soit 8 654 lignes dont les plus anciennes datent de 2022, et 5 934
  pour `assigned_to=5`. Le plafond de balayage est à 10 000 lignes. Non modifié.

## Suite ordonnée

1. Fusion dans `main` des lots du 01/11 et du 02/11, à décider. L'installé devance `main` :
   une reconstruction depuis `main` ferait régresser le connecteur.
2. Tests réels, clé à fournir hors session : `DOLIBARR_LIVE_READONLY=1` sur
   `tests/test_dolibarr_live_readonly.py` ici et `tests/test_lecture_outils_meetingnotes.py`
   dans l'extension.
3. Retour si besoin : `bascule-dolibarr.sh retour`, puis redémarrage de Claude Desktop.
# Reprise C03 : actions ouvertes, 02/11/2026

Cette section prime sur les états historiques. Source de vérité du lot :
[reprise C03](../../../REPRISE.md), avec rapport, décisions et preuves.
Worktree : `C03-actions-ouvertes/travaux/mcp-foss-connectors`, branche
`vague/2026-11-01-c03`, base `b4fc0485da1a60554fa0bf5ca86ac9cdb9844354`.
Le dépôt local `../coeur.git` partage les objets des références L01.

Version préparée : Dolibarr 1.8.10. Actions ouvertes hors `systemauto` et
progression -1 par défaut, option `include_system`, dates strictes côté API,
compteur après assignation et avant pagination, exactitude liée au balayage.
Le retour est 1.8.9 corrigé par C01. La couche métier reste 0.9.0.

Les tests, lectures réelles et empreintes sont consignés au fil de l'eau dans
le dossier du lot. Aucun déploiement, redémarrage, push ni merge autorisé.
L01 et C01 restent intacts. Prochaine étape : consulter le rapport du lot et
les preuves finales avant toute décision d'installation.

---

# Reprise C01 : correction des remises L01, 02/11/2026

Cette section prime sur les états historiques ci-dessous. Source de vérité du
chantier : [reprise C01](../../../../C01-remises-connecteurs/REPRISE.md), avec le rapport, les empreintes et les preuves.
Branche : `vague/2026-11-01-l01`, base avant correction : `52e068f`.
Worktree : `/home/bjean/RepoGit/_lots_wt/vague-2026-11-01/L01-connecteurs/travaux/mcp-foss-connectors`.

Objectif : reformuler les mentions et ponctuations interdites des remises
Dolibarr et Kanboard, sans modification fonctionnelle, sans installation.
Les serveurs et manifestes de remise sont versionnés ici. Une seconde passe
sémantique reformule aussi les descriptions de compte vers « compte secondaire »
et la description du plafond de listes vers « API ». Les valeurs publiques
de sélection de compte restent identiques. Les manifestes
`packaging/remise-l01/*/manifest.json` reprennent la configuration des archives
historiques avec leurs seuls textes descriptifs corrigés. Les manifestes
ordinaires du dépôt sont également corrigés quand concernés.

L'alias historique Kanboard est reconstruit depuis ses points de code pour
conserver exactement sa valeur et le message d'erreur existant, sans l'écrire
littéralement dans les sources. Il reste présent en mémoire à l'exécution.
Les arbres exécutables normalisés sont équivalents ; 159 comparaisons de retours
et d'erreurs couvrent alias, casse, espaces, défauts et entrées inconnues.
Suite complète : 1 747 réussites, trois exclusions attendues, zéro tentative réseau.
Les exclusions portent sur deux recettes distantes et un test historique
d'écriture. Le dépôt privé L01 passe 1 289 tests ; le métier passe 488 tests.

Les retours reposent sur `4d6d989` dans le worktree C01 `travaux/l01-retour`,
branche `vague/2026-11-01-c01-retour-l01`. Les proposées restent dans le worktree
L01 `travaux/mcp-foss-connectors`, branche `vague/2026-11-01-l01`.
Le constructeur C01 `scripts/l01/reconstruire.py` exige un arbre propre,
l'égalité des sources avec HEAD et la conservation des dépendances tierces.
Le métier interne corrigé vient du worktree L01 `travaux/inno3-mcp-extensions`.

État : sources préparées et testées. Archives, fumées et copies fictives sont
suivies dans le rapport C01. Aucune installation ni vérification après
application ; les procédures de décision L01 restent inchangées.
Limite : la copie sur place préserve les descriptions du manifeste installé,
même lorsque le manifeste de remise porte les reformulations.
Prochaine action : vérifier les empreintes C01 et suivre ses décisions avant
toute application. Aucun push, fusion, redémarrage ou recette distante autorisé.

---

# Reprise L01 : contrôle du cœur, 01/11/2026

Cette section prime sur les états historiques ci-dessous. Le dossier canonique
est [le lot L01](../../../REPRISE.md), avec ses [preuves](../../../preuves/coeur-revue.md).
Worktree : `travaux/mcp-foss-connectors`, branche `vague/2026-11-01-l01`,
base `4d6d989`. Le dépôt de références du lot est `travaux/coeur.git` ; il
réutilise les objets du dépôt initial sans en modifier les références.

Objectif : relire les correctifs antérieurs Dolibarr 1.8.9 et Kanboard 0.19.0,
les vérifier hors ligne et préparer leur recette sans installation.

- Dolibarr 1.8.9 conservé : `agendaevents` est départagé sur `t.id`, avec
  `t.datep` en premier ; porteur, co-assignés et repli sur auteur sont couverts.
  Audit des treize valeurs littérales `sortfield` et du défaut de balayage.
- La recette réelle contrôle désormais chaque code HTTP avant les
  normalisations du transport. Tout code autre que 200 échoue ; seules les
  méthodes GET sont autorisées par sa garde. Contre-épreuves hors ligne ajoutées.
- Kanboard 0.19.1 préparé : swimlane manquante recherchée même si la colonne
  existe, complétude portant sur les deux, tableau malformé isolé du reste des
  résultats, compteur limité aux appels commencés avant la fin du budget.
- Suite initiale : 1 733 réussis, 3 ignorés ; finale : 1 747 réussis, 3 ignorés.
  Zéro tentative réseau dans les deux cas ; contrôle de style conforme.
  Les trois sauts sont les deux recettes Dolibarr en lecture et un test
  historique d’intégration Dolibarr en écriture, exclu du périmètre autorisé.
- Aucune installation, aucun redémarrage, aucun appel de production dans cette
  relecture. La recette distante et les paquets relèvent de la consolidation L01.

Limites : le budget de 20 secondes borne la résolution des tableaux, pas la
recherche initiale des retards ni les résolutions historiques de noms. Les
réponses restent des lectures à un instant donné. Le contrôle statique des tris
ne remplace pas la recette sur le schéma effectivement servi.

Prochaine étape : construire depuis ces sources, vérifier le démarrage puis
exécuter la recette distante en lecture avec statuts strictement contrôlés.

---

# Reprise vérifiée le 01/11/2026 : correctifs connecteurs relevés le 30/10

Cette section prime sur les suivantes. Elle couvre trois dépôts, branche
`lot/2026-11-01-correctifs-connecteurs`, worktrees sous
`/home/bjean/RepoGit/_lots_wt/2026-11-01-correctifs-connecteurs/`. Bases : `2e27d59`
ici, `d2cca0b` pour l'extension, `6c6eaf3` pour mcp-inno3. Commits : `34fde26`
(Dolibarr 1.8.9) et `9abe8d0` (Kanboard 0.19.0) ici, `e2153a5` (test de fumée) dans
l'extension, `55b928e` (CalDAV 0.6.0) dans mcp-inno3. **Rien n'est fusionné ni
poussé. L'installé est donc en avance sur `main` des trois dépôts.**

## Faits vérifiés

- Comparaison des 12 extensions installées : 10 identiques à `main` au démarrage du
  lot. Kanboard installé en 0.17.3 pour 0.18.0 dans `main`. Extension métier en 0.8.8
  pour 0.9.0. La réinstallation du poste a repris des copies antérieures, malgré
  l'état décrit le 24/10 ci-dessous.
- Actions ouvertes : le bundle était identique à `main`, le défaut était dans le
  code. `_scan_list` ajoutait `t.rowid` à tout tri, or `llx_actioncomm` a pour clé
  `id`. Reproduit en production le 01/11. Les routes projets, factures, factures
  fournisseur, devis, tiers, contacts, tickets, utilisateurs et produits répondent en
  production ; `bankaccounts` n'est vérifiée que sur le source amont.
- Second défaut, masqué par le 503 : les lignes portent les propriétés d'`ActionComm`
  (`userownerid`, `socid`, `percentage`, `datef`). Établi sur le source amont 23.0 et
  24.0, pas encore observé sur l'instance.
- Outils MeetingNotes et carnets : même serveur, pas de serveur séparé. Le bundle
  contenait deux `dist-info` de l'extension. `entry_points()` ne garde que le premier
  lu, dans l'ordre de `os.listdir` ; celui de la 0.8.8, réduit à `METADATA`, masquait
  le point d'entrée de la 0.8.6. Démarrage à 78 outils au lieu de 113, sans erreur.
- Volume CalDAV réel, 48 événements, 56 267 caractères : participants 17 438, URL
  9 357, descriptions 6 575. `_parse_component` coupait déjà toute description à 300
  caractères, y compris pour `caldav_get_event`, et la recherche ne lisait que ce
  début. Forme compacte sur ces données : 18 253 caractères, 19 058 dans le pire cas
  de descriptions pleines.

## Décisions

- `compact` reste **faux par défaut**, contrairement à la première préférence du
  relevé : `cycle-reunion-preparation`, `brief-quotidien`, `demarrage-journee`,
  `session-start-coordination`, `revue-hebdo-direction` et `smart-time-logging` lisent
  les participants dans cette réponse. Les passer d'abord en `compact=True` ou par
  `caldav_get_event`, puis basculer le défaut.
- `assigned_to` retient porteur et co-assignés, puis l'auteur si aucun assigné, et
  reste appliqué après le balayage : le filtre serveur `user_ids` ignore les
  co-assignés.
- Pas de trailer d'outil dans les messages de commit (retirés le 23/10/2026).

## Installation et contrôles

Installé sur place selon RELEASE 4bis, manifeste installé conservé hors `tools` et
`version`, sauvegardes `.backup-*-2026-11-01-v*` dans
`~/Sauvegarde/Archives/claude-extensions-anciennes-versions/`. Bundles repackés depuis
l'installé, donc py314 : `dist/dolibarr-erp-inno3-v2.mcpb`, `dist/kanboard-inno3-v7.mcpb`
ici, `archives/caldav-bluemind-0.6.0.mcpb` et `caldav-bluemind.mcpb` dans mcp-inno3.
Poignée de main sous `/usr/bin/python3` : Dolibarr 1.8.9 à 113 outils, Kanboard 53,
CalDAV 9. Tests écrits avant correction, vus rouges. Suites finales : cœur 1 733
réussis, 3 ignorés ; mcp-inno3 1 287 réussis ; extension 482 réussis. Cœur et mcp-inno3
rejoués sous garde réseau, aucune tentative. Ruff propre sauf 32 signalements de
`caldav_mcp/server.py`, identiques sur `main`.

## Limites, non vérifié

- **Les processus chargés restent les anciens jusqu'au redémarrage du client.** Aucun
  correctif n'est encore observé sur un service réel.
- Coût du balayage des actions non mesuré : `percent<>100` inclut les événements
  automatiques à -1, et la route relit chaque ligne côté serveur.
- Version de production non lue : 23.0.3 selon le relevé, 24.0.1 selon la mémoire.
- `caldav_search_events` n'a pas de forme compacte. `my_status` est absent pour un
  alias de l'adresse du compte. Budget Kanboard fixe, 20 secondes.

## Suite ordonnée

1. **Benjamin** : redémarrer Claude Desktop.
2. Recette en lecture seule : `dolibarr_list_actions_open(limit=5)` puis avec
   `assigned_to=5`, en relevant durée et `scan_truncated` ; `dolibarr_list_meetingnotes`,
   `dolibarr_get_meetingnote`, `dolibarr_list_meetingnotes_actions`,
   `dolibarr_list_support_carnets`, `dolibarr_check_missing_carnets` ;
   `caldav_list_events` du 30/10 au 04/11 avec `compact=True`, moins de 20 000
   caractères ; `caldav_get_event` sur une longue invitation ;
   `kanboard_my_overdue(with_columns=True)`. Hors session : `DOLIBARR_LIVE_READONLY=1`
   active `tests/test_dolibarr_live_readonly.py`.
3. **Benjamin** : trancher le défaut de `compact`, fusionner et pousser les trois
   branches, puis actualiser la table installée de la provenance.
4. Créer la carte Kanboard de suivi : aucune écriture dans ce lot, par consigne.

---

# Reprise vérifiée le 24/10/2026

Main et origin/main sont alignés à la base 1e8c14f après fetch. Le lot
Kanboard 0.18.0 est intégré et publié. Les sources installées Dolibarr 1.8.8,
Kanboard 0.18.0, Nextcloud 1.4.1, HedgeDoc 1.2.0 et WordPress 1.6.0 sont
identiques octet par octet aux sources de main. L'extension métier associée
est en 0.9.0 et correspond au main de son propre dépôt. Aucun redémarrage
applicatif : les versions effectivement chargées ne sont pas attestées ici.

Suite complète rejouée sous garde réseau : 1 705 réussites, un test
d'intégration réelle ignoré, zéro tentative réseau. Contrôle de style conforme.
La vérification native des modules Dolibarr et la recette des services sont
documentées séparément dans le dossier d'audit ; ces tests hors ligne ne s'y
substituent pas.

La branche historique fix/dolibarr-list-documents-meetingnotes n'est pas un
correctif manquant : le report 8293d17, puis la numérotation 576f5bd, sont
dans main. Ses tests sont identiques hors numéro de version en documentation.
La branche et son worktree restent préservés. Pas de fusion de l'ancien
historique ni de suppression de copie de travail pour ce seul écart Git.

Preuves : ../../_lots_wt/audit-depots-2026-10-23/preuves/prolongation-2410/.
Cette section prime sur les états historiques ci-dessous.

# Reprise historique : Kanboard 0.18.0, 22/10/2026

Objectif du lot : porter les relectures après déplacement, fermeture et
réouverture dans le dépôt canonique. Cette section fait foi pour le lot courant ;
les sections suivantes conservent les constats historiques et leurs limites.

## État vérifié et décisions

- Worktree : `/home/jean/ssd_data/jean/RepoGit/_lots_wt/2026-10-22-kanboard-relecture`.
  Branche : `lot/2026-10-22-kanboard-relecture`, partie de `main` à **`184012d`**,
  arbre initial propre. Commit fonctionnel : **`c663013`**.
- Kanboard **0.18.0** dans le manifeste et `SERVER_VERSION`. Le paquet reste en
  **0.2.0** ; aucun autre connecteur n'est modifié. Les 53 outils et toutes leurs
  signatures sont conservés. `main` reste à `184012d` ; aucun merge ni push.
- `kanboard_move_task`, `kanboard_close_task` et `kanboard_open_task` utilisent
  les helpers existants `_verify_task_state` et `_verified_payload`, inchangés.
  Après réponse positive de l'API, un `getTask` relit avec le même `as_user`.
  Le déplacement compare `column_id` et `swimlane_id`, la fermeture `is_active=0`,
  la réouverture `is_active=1`.
- Contrat additif : `success` reste la réponse de l'API ; `verified` porte la
  confirmation de l'état. `verification_method`, `post_state` si disponible,
  `mismatch` et `warning` si nécessaire suivent le contrat de création.
  Une relecture impossible donne `verified=false` et un avertissement, sans
  perdre les clés historiques ni rejouer l'écriture. Les refus et erreurs
  d'écriture gardent leur contrat ; le transport reste inchangé.
- Les **5 cas historiques laissés hors alignement par K/K2 sont portés** dans
  `tests/test_kanboard_verified_write.py`, sans retirer aucune assertion.
  Les 12 anciens tests de ce fichier sont inchangés. Douze cas complémentaires
  couvrent les erreurs de relecture sur les trois outils, les erreurs et refus
  d'écriture sans rejeu, les swimlanes et la réouverture sans effet.
- Choix conservateur : `kanboard_assign_task` reste hors périmètre et inchangé.
  La création, la garde d'identité et le transport ne changent pas non plus.

## Vérifications du lot

| Contrôle | Résultat |
| --- | --- |
| Suite complète initiale | **1 688 réussis, 1 ignoré**, 6,54 s |
| Tests de relecture ajoutés avant correctif | **11 échecs, 18 réussis**, 0,24 s |
| Même fichier après correctif | **29 réussis**, 0,13 s |
| Manifestes et signatures | **27 réussis**, 1,33 s |
| Suite complète finale | **1 705 réussis, 1 ignoré**, 6,50 s |
| Suite complète sous garde réseau | **1 705 réussis, 1 ignoré**, 6,07 s, aucune tentative réseau |
| Ruff sur tout le dépôt | **0 diagnostic** |
| `git diff --check` | **0 erreur** |

Les cinq cas demandés et les trois cas de relecture impossible étaient rouges
sur `KeyError: 'verified'`, puis verts. Les refus et erreurs d'écriture étaient
déjà verts avant correction et le restent. Le seul test ignoré est l'intégration
Dolibarr réelle préexistante. Aucun test n'est supprimé, désactivé ou affaibli.
La comparaison AST confirme que seules les trois fonctions visées changent
hors docstrings ; aucun helper ajouté ou modifié, aucune fonction supprimée.
Le manifeste ne change que de version. SHA-256 du serveur final :
`b5e1e38cc821a9e1e293662e0dcd25f513445efe05ecf89e115d900f6509bfd4`.

## Références, limites et prochaines actions

- [Rapport du lot](/home/bjean/RepoGit/_lots_wt/_rapports-2026-10-22/kanboard-relecture.md) :
  preuves rouge puis vert, commandes, exemples avant/après et risques résiduels.
- Sources : `kanboard_mcp/{server.py,manifest.json,README.md,CHANGELOG.md}` ;
  contrats : `tests/test_kanboard_verified_write.py`, transport, manifestes et
  signatures. `AGENTS.md` et `CLAUDE.md` restent identiques et pointent ici.
- Référence historique lue seulement : `/home/bjean/RepoGit/mcp-inno3`, commit
  **`7f8ecf3`** du 17/08/2026 et ses cinq tests d'état. Aucun helper historique
  recopié ; les helpers du dépôt canonique sont réutilisés.
- Le checkout principal `/home/bjean/RepoGit/inno3/mcp-foss-connectors`
  reste sur `main`. Le worktree builddoc, branche
  `fix/dolibarr-list-documents-meetingnotes` à `4445d44`, est toujours enregistré ;
  son contenu n'a pas été consulté ni modifié. Les anciens chemins K/K2 sont
  historiques et ne doivent pas servir de consignes de checkout.
- Version installée **0.17.3** selon le constat de mission, non inspectée dans
  ce lot. Aucune installation, construction de paquet, recette réelle ni
  publication exécutée. Les tests utilisent des réponses synthétiques.
- La relecture n'est pas transactionnelle et ne prouve pas une transition :
  une tâche déjà dans l'état attendu est vérifiée. La position dans la colonne
  n'est pas comparée. Un `success=true` doit se lire avec `verified`.

1. Relire le commit fonctionnel et les preuves, puis intégrer la branche dans
   `main` sur demande explicite.
2. Décider séparément d'une recette réelle et de la livraison 0.18.0.
3. L'attribution de l'auteur sans identité primary prouvée reste à confirmer
   sur service réel, comme dans K2. Une extension aux assignations est une
   évolution distincte ; aucun arbitrage ne bloque le présent portage.

---

# Reprise historique : lot H du 19/10/2026, complétée le 21/10

Objectif du lot H : aligner la reprise sur Git et distinguer source, installation
et processus chargé. Les états ci-dessous sont ceux de ces dates ; la section
en tête donne l'état du lot courant.

- **Vérifié dans Git** : `main` = `edb5a36`, Dolibarr **1.8.6**, paquet
  **0.2.0**. La référence locale `origin/main` est identique, sans contrôle
  distant. `77105e3` (1.8.5, condition du tiers reprise sur facture/devis) et
  `edb5a36` (1.8.6, condition configurée à la création du tiers) sont intégrés.
  Leur intégration et l'attribution de ces numéros sont terminées.
- **Constat fourni du poste, 19/10 à 10h36** : extensions installées et
  rechargées, Dolibarr **1.8.5**, Kanboard **0.17.2**, HedgeDoc **1.2.0**,
  Nextcloud **1.4.1**, WordPress **1.6.0**. Le relevé complet est dans la
  [reprise de mcp-inno3](/home/bjean/RepoGit/mcp-inno3/docs/REPRISE.md).
- **Vérifié sur disque pendant H** : Dolibarr est désormais en **1.8.6** ;
  les cinq fichiers source installés sont identiques à ceux de `main`.
  Ce constat complète le relevé de 10h36, sans prouver un nouveau rechargement.
  L'extension métier embarquée reste en **0.8.6**, voir la
  [reprise des extensions](/home/bjean/RepoGit/inno3-mcp-extensions/docs/REPRISE.md).
- **Kanboard, mis à jour le 21/10/2026** : la 0.17.2 est intégrée à `main`
  (lot K `86eecc5`, lot K2 jusqu'à `c3d2c1a`), poussée, et installée le 17/10
  à 17h42 avec un `server.py` identique à `main`. Recette réelle du 21/10 sur
  le projet 262, processus chargé en 0.17.2 : somme des `task_count` du tableau
  égale aux 466 cartes actives, aucune carte fermée rendue, aucune colonne
  `status_filter_incomplete`, titre de la carte 7970 décodé, dates présentes,
  `modified_before` opérant. Les trois branches K à archiver n'existent plus.
  La **0.17.3** (`b0f14e8`, documentation seule : un commentaire ne fait pas
  avancer `date_modified`) est intégrée à `main`, poussée et installée le
  21/10 ; elle sera chargée au prochain redémarrage du client.
- **Dolibarr 1.8.8, mis à jour le 21/10/2026** (`be7304d`, poussé) : dans
  `dolibarr_list_time_entries`, `date` est désormais le jour GMT de `datehour`
  et le filtre porte sur ce jour. Avant, `date` était lu dans le fuseau du
  poste : vu de Paris, une écriture à partir de 22:00 GMT changeait de jour
  et le filtre la manquait (écriture 3936 du 25/09). Impact mesuré avant
  correction : nul, les 102 écritures Ledger sont avant 22:00 GMT. Installé le
  21/10 (`server.py` seul, `serverInfo` 1.8.8, 113 outils, sauvegarde
  `.backup-dolibarr-erp-inno3-v2-2026-10-21-v1.8.7`) ; chargé au prochain
  redémarrage du client. Hors périmètre : les autres dates restent en heure
  locale via `_ts_to_date` (un jour trop tôt à l'ouest de l'UTC).
  **Piège de test** : `server.py` porte `__version__`, que
  `test_protocol_initialize_and_list_tools_in_memory` compare au manifeste ;
  ce test est sauté hors du venv du hook. Vérifier avec
  `/home/bjean/RepoGit/_lots_wt/_venv-mcp/bin/python`.
- **Chantier ouvert** : builddoc reste hors `main`, branche
  `fix/dolibarr-list-documents-meetingnotes`, commit `4445d44`, worktree
  `/home/bjean/RepoGit/mcp-wt-builddoc/mcp-foss-connectors`.
  B12/B26 et la coordination avec le builddoc métier restent à attribuer
  et arbitrer par Benjamin. Les anciens worktrees conditions et MC sont absents.

Suite ordonnée :

1. **Benjamin** : confirmer le rechargement de Dolibarr 1.8.6 et décider du
   réglage `DOLIBARR_THIRDPARTY_PAYMENT_TERM` puis de la recette réelle.
2. **Benjamin** : organiser la reprise builddoc avec le dépôt d'extensions.
3. **Benjamin** : redémarrer le client pour charger Kanboard 0.17.3 et
   Dolibarr 1.8.8 ; recette réelle de la 1.8.8 = une écriture à 23:59 GMT,
   filtrée sur son jour, puis supprimée. Puis
   trancher les deux points Kanboard ouverts depuis K2 : vérifier sur le service
   réel l'attribution de l'auteur sans identité primary prouvée, et décider des
   relectures après déplacement, fermeture et réouverture.

Contrôles H : ascendance des commits, versions source/manifeste/README,
worktrees, chemins et comparaison des fichiers source installés. Le diff AST avec
1.8.5 confirme les deux seules fonctions modifiées décrites ci-dessous ;
les 78 outils et la configuration du manifeste sont conservés. Suites de
tests non rejouées : leurs résultats restent ceux des correctifs historiques.
Hypothèse non retenue : fichier installé identique ne signifie pas processus
rechargé. Aucune recette ni vérification distante pendant H.

Lot H : branche `docs/reprise-2026-10-19`, base `edb5a36`, worktree
`wt_docs/mcp-foss-connectors` repéré par `git worktree list`. Le checkout
canonique `/home/bjean/RepoGit/inno3/mcp-foss-connectors` est sur
`main`. Références : [README](../README.md),
[provenance](PROVENANCE_CONNECTEURS.md), sources et tests cités ci-dessous.
`AGENTS.md` et `CLAUDE.md` restent courts, identiques et sans version figée.
Contrôle documentaire H : liens ajoutés présents, typographie conforme et
`git diff --check` propre ; seuls des fichiers Markdown sont modifiés.

---

# Reprise historique : condition de règlement par défaut des tiers, 19/10/2026

Correctif livré par `edb5a36`, intégré dans `main`. Les mesures ci-dessous
décrivent ce correctif et n'ont pas été rejouées pendant le lot H.

## Objectif, décisions et état courant

- Branche d'origine : `fix/dolibarr-tiers-condition-defaut`, base
  `77105e3` (Dolibarr 1.8.5), arbre initial déclaré propre. Le chantier utilisait
  le worktree conditions indiqué dans la section suivante. Cette branche et
  ce worktree ne sont plus présents au contrôle H ; le correctif est dans `main`.
- Dolibarr passe de **1.8.5 à 1.8.6**, source, manifeste et README synchronisés.
  Le numéro **1.8.6** est intégré. Aucun autre connecteur ne change.
- `dolibarr_create_thirdparty` accepte `cond_reglement_id`, entier strictement
  positif facultatif, avec la même validation stricte que la mise à jour du tiers,
  y compris via MCP. Il prime sur `DOLIBARR_THIRDPARTY_PAYMENT_TERM`.
  Omis ou `null`, il active le réglage d'installation, vide par défaut et lu
  à chaque appel : code insensible à la casse ou identifiant entier positif.
- Seuls les clients et prospects (`client` 1, 2 ou 3) reçoivent la condition.
  Un fournisseur seul reçoit `source=non_applicable`, même avec le paramètre.
  Un réglage invalide ou un code non résolu laisse créer sans condition avec
  l'erreur. Un identifiant numérique reste envoyé sans dictionnaire lisible.
  Le helper `_creation_payment_terms()` et le cache `_payment_terms()` sont
  réutilisés ; aucune constante globale Dolibarr n'intervient.
- L'anti-doublon est conservé et précède toute lecture du dictionnaire.
  Sans paramètre ni réglage, seuls les champs de réponse s'enrichissent :
  le POST et le nombre d'appels restent ceux de la 1.8.5.
- La condition est envoyée dans le POST, puis le tiers est relu une fois si
  une condition a été envoyée. `payment_terms_applied` rend l'identifiant, le
  code, le libellé, `source` (`parametre`, `configuration`, `aucune` ou
  `non_applicable`), `verified` et `error`. La comparaison porte sur la valeur
  brute relue. `verified=null` signifie aucune condition envoyée ou relecture
  impossible, `false` signale un écart. Ces erreurs préservent le succès de
  création et l'identifiant du tiers ; aucun POST n'est rejoué.
- Choix documentaire : variable dans le README et la docstring. Le manifeste
  lie actuellement les variables configurables à `user_config` ; il reste
  inchangé hors version, sans nouveau champ `user_config` ni défaut ajouté.
- Travail seul, sans réseau, instance réelle, bundle, installation ni push.
  Aucun autre worktree consulté ou modifié, notamment le chantier documentaire
  `mcp-wt-builddoc` déjà référencé ci-dessous. Son état reste non revérifié.

## Vérifications effectuées

| Vérification | Résultat mesuré |
| --- | --- |
| Suite complète initiale | **1 611 réussis, 1 ignoré**, 8,84 s |
| Nouveaux tests + schéma MCP avant modification du serveur | **62 échecs, 1 réussite**, 1,93 s |
| Créations, conditions, écriture tiers, exports et manifestes après correctif | **296 réussis**, 3,13 s |
| Suite complète finale | **1 673 réussis, 1 ignoré**, 8,90 s |
| Ruff, dépôt complet | Initial et final : **0 diagnostic** |
| `git diff --check` | Initial et final : **0 erreur** |

Les **62 nouveaux cas** ont été écrits avant le serveur : 61 étaient rouges,
le garde-fou anti-doublon sans nouveau paramètre était déjà vert. Le 62e échec
concerne le test existant du schéma MCP, étendu à la création du tiers.
Les tests comptent les appels simulés, couvrent la configuration à l'appel,
les priorités, les erreurs cumulées et la comparaison brute après création.
Les trois commandes de vérification de la section 1.8.5 ci-dessous sont
réutilisées depuis la racine avec le même venv existant.
Le seul test ignoré reste l'intégration Dolibarr réelle préexistante ; aucun
test n'a été supprimé ou désactivé.

Comparaison AST avec `77105e3`, hors docstrings : seules
`_creation_payment_terms()` et `dolibarr_create_thirdparty()` changent, aucune
fonction ajoutée ou supprimée. Les **78** entrées du manifeste et sa
configuration restent identiques, seule la version change. Aucun tiret
typographique interdit ajouté. Les fichiers d'entrée de reprise sont identiques.

## Références, limites et suite ordonnée

Sources : `dolibarr_mcp/{server.py,manifest.json,README.md,CHANGELOG.md}`.
Nouveaux contrats : `tests/test_dolibarr_thirdparty_payment_terms_creation.py`.
Schéma MCP : `tests/test_dolibarr_exports.py`. Régressions : les tests de
conditions et de création de la 1.8.5, de mise à jour du tiers et de manifestes.
`AGENTS.md` et `CLAUDE.md` restent courts, identiques et pointent ici.

La preuve repose sur des réponses synthétiques, sans recette réelle.
La relecture n'est pas transactionnelle : un autre acteur peut modifier le
tiers entre le POST et le GET. Le dictionnaire reste mémorisé, échecs compris,
pendant la session ; modifier la variable ne vide pas ce cache. Redémarrer
pour relire le dictionnaire après modification des conditions ou des droits.
Les états et vérifications des sections historiques décrivent leurs commits.

1. Intégration réalisée dans `main` par `edb5a36`, en 1.8.6.
2. Configurer le réglage d'installation et décider séparément d'une recette
   réelle et d'une livraison. Ces opérations ne sont pas exécutées ici.

---

# Reprise historique : conditions de règlement à la création, 19/10/2026

Livraison 1.8.5 intégrée dans `main` par `77105e3`, avant la 1.8.6.
Les branches et états ci-dessous décrivent cette livraison antérieure.

## Objectif, décisions et état courant

- Worktree : `/home/jean/ssd_data/jean/RepoGit/mcp-wt-conditions/mcp-foss-connectors`.
- Branche : `fix/dolibarr-conditions-reglement-creation`, base vérifiée
  `origin/main` à `651f534`, arbre initial propre. Livraison : commit portant
  cette section sur cette branche, avec le correctif et ses tests.
- Dolibarr passe de **1.8.4 à 1.8.5** dans le source et le manifeste.
  Le numéro pourra être réattribué à la fusion si un autre chantier utilise
  1.8.5 avant intégration. Les autres connecteurs et le paquet restent inchangés.
- Les créations de facture et de devis reprennent la condition du tiers.
  La création de facture depuis un devis reprend d'abord celle du devis,
  sinon celle du tiers. Un `cond_reglement_id` explicite est prioritaire ;
  omis ou `null`, il active cette reprise. La validation stricte de la 1.8.4
  est conservée : entier positif, sans booléen, chaîne ni décimal, aussi via MCP.
- Chaque création rend `payment_terms_applied` : identifiant envoyé, code,
  libellé, provenance (`parametre`, `devis`, `tiers`, `aucune`) et erreur.
  Un tiers sans condition ou inaccessible laisse la création partir sans
  `cond_reglement_id`. Les erreurs de lecture restent visibles. Le dictionnaire
  utilise le cache existant, y compris lorsque son accès échoue.
- `due_date` explicite reste transmis dans `date_lim_reglement` et prioritaire
  sur la condition héritée ou forcée. Aucun changement du mode de règlement.
  L'indicateur à +1 jour rappelle « À réception » (`RECEP`, `nbjour = 1`) :
  cette heuristique ne prouve pas une absence de condition ni un retard réel.
- Aucun autre worktree consulté ou modifié. Le chantier distinct
  `mcp-wt-builddoc`, branche `fix/dolibarr-list-documents-meetingnotes`, garde
  son rôle documentaire propre ; son état n'a pas été revérifié ici.
  Aucun changement de branche, réseau, bundle, installation ni push.

## Vérifications effectuées

Venv existant : `/home/bjean/RepoGit/_lots_wt/_venv-mcp`.
Les tests de création utilisent un transport simulé compté. Les anciennes
fixtures de validité des devis simulent désormais aussi la lecture du tiers.

| Vérification | Résultat mesuré |
| --- | --- |
| Suite complète initiale | **1 535 réussis, 1 ignoré**, 5,98 s |
| Tests écrits avant le correctif, création + conditions + exports | **81 échecs, 99 réussis**, 1,97 s |
| Suites ciblées après correctif, avec tiers, historique Dolibarr et manifestes | **592 réussis, 1 ignoré**, 3,30 s |
| Suite complète finale | **1 611 réussis, 1 ignoré**, 6,49 s |
| Ruff, dépôt complet | **0 diagnostic** |
| `git diff --check` | **0 erreur** |

Les 76 nouveaux cas de création ont tous été vus rouges avant modification
du serveur. Les cinq autres échecs attendus portaient sur la note à +1 jour
et le schéma MCP. Le seul test ignoré reste l'intégration Dolibarr réelle
préexistante ; aucun test supprimé ou désactivé.

Comparaison AST avec `651f534`, hors docstrings : seules les trois fonctions
de création changent, avec un helper commun ajouté. Les **78** entrées du
manifeste sont conservées. Aucun tiret typographique interdit ajouté.

Commandes exécutées depuis la racine :

```sh
PYTHONPATH=. /home/bjean/RepoGit/_lots_wt/_venv-mcp/bin/python -m pytest tests -q -p no:cacheprovider
/home/bjean/RepoGit/_lots_wt/_venv-mcp/bin/python -m ruff check --no-cache .
git diff --check
```

## Références, limites et suite ordonnée

Sources : `dolibarr_mcp/{server.py,manifest.json,README.md,CHANGELOG.md}`.
Contrats : `tests/test_dolibarr_payment_terms_creation.py`,
`tests/test_dolibarr_payment_terms.py`, `tests/test_dolibarr_exports.py`,
`tests/test_dolibarr.py` et `tests/test_dolibarr_thirdparty_write.py`.
Les fichiers d'entrée `AGENTS.md` et `CLAUDE.md` restent inchangés.

Les tests prouvent les valeurs transmises et le traitement de réponses
synthétiques. `payment_terms_applied` décrit la condition envoyée ; aucune
recette réelle ni nouvelle vérification de persistance n'a été effectuée.
La priorité de l'échéance repose sur le champ `date_lim_reglement`, déjà
identifié dans le code comme l'argument `forceduedate` de la création Dolibarr.
Le dictionnaire reste mémorisé pendant la session ; après modification du
paramétrage ou des droits, redémarrer pour le relire.

1. Intégration réalisée dans `main` par `77105e3`, en 1.8.5.
2. Décider séparément d'une recette sur service réel et d'une livraison.
   Ces opérations n'ont pas été exécutées dans ce chantier.

---

# Reprise historique : lot MC, outillage, 17/10/2026

Trace du lot MC intégré par `2ffac44` et `651f534`. Seule la section en tête
fait foi pour l'état courant ; les commandes et demandes de merge ci-dessous
décrivent le chantier du 17/10.

## État vérifié

- Worktree : `/home/jean/ssd_data/jean/RepoGit/_lots_wt/MC-mcp-foss-connectors`,
  branche `lot/2026-10-17-outillage`, base `11d691f`, initialement propre.
- `main` local : `11d691f`. K/K2, L et S sont intégrés. MB l'est aussi :
  `03b4e10`, merge `f72e12d`, numérotation n8n `11d691f` en **1.3.1**.
  L'ancien blocage de permission décrit plus bas est donc périmé.
- MC `2ffac44` verrouille le constructeur sur les paquets de ce dépôt, liens
  symboliques extérieurs compris, et documente la
  [provenance canonique](PROVENANCE_CONNECTEURS.md). Ce document fait foi pour
  les huit connecteurs publics, leurs copies privées et l'installation sur disque.
- Versions conservées : paquet **0.2.0**, Dolibarr **1.8.4**, Kanboard **0.17.2**,
  n8n **1.3.1**. Aucun numéro changé par MC. La copie Dolibarr installée est
  identique au source courant ; HedgeDoc installé vient encore de M et WordPress
  installé est antérieur au contrat de transport public. Aucun processus interrogé.
- Le worktree `mcp-wt-builddoc/mcp-foss-connectors`, branche
  `fix/dolibarr-list-documents-meetingnotes`, reste enregistré à `4445d44`.
  L'intégration builddoc et sa collision de numéro avec S relèvent de B12/B26,
  pas d'une nouvelle numérotation dans MC. Les anciens K/L/S/MB ne sont plus enregistrés.

## Preuves et limites

Suite initiale : **1 532 réussis, 1 ignoré**. Finale : **1 535 réussis,
1 ignoré**, **0 tentative réseau**. L'ignoré est l'intégration Dolibarr réelle
préexistante. Ruff sur les deux fichiers Python touchés : **0 diagnostic**.
Le constructeur n'a été exécuté que sur des fixtures avec pip simulé ; aucun
bundle de livraison ni dépendance n'a été installé.

[Rapport MC](/home/bjean/RepoGit/_lots_wt/_execution-2026-10-17/MC.rapport.md).
Les worktrees associés sont `../MC-mcp-inno3` (repack, manifestes, distribution)
et `../MC-inno3-mcp-extensions` (reprise documentaire), même branche MC.

## Suite ordonnée

1. Orchestrateur : intégrer MC et consolider les numéros au merge.
2. Traiter B12/B26 avec le worktree builddoc conservé ; ne pas reprendre les
   anciennes commandes K/L/S comme des consignes de checkout actuelles.
3. Livraison HedgeDoc/WordPress, maintien WordPress Commons, recette et publication :
   **à arbitrer par Benjamin**. Aucune installation applicative ni réseau par MC.

---

# Historique daté, non prescriptif

# Reprise du dépôt mcp-foss-connectors

## 21/10/2026 : Kanboard 0.17.3, ce que mesure `date_modified`

### Objectif et état

La docstring de `modified_before` promettait « aucun signal depuis quatorze
jours ». Kanboard n'avance pas `date_modification` sur un commentaire : constaté
sur l'instance (cartes 7970 et 8012), puis confirmé dans la source, qui exclut
aussi les sous-tâches, les liens et le réordonnancement dans une colonne. La
date avance sur une édition de champ, un changement de colonne ou de swimlane,
une fermeture ou une réouverture. Correction documentaire seule : docstrings de
`kanboard_list_tasks` et `kanboard_get_board`, README, CHANGELOG, version
0.17.3. Ni comportement ni signature ne changent.

- Commit `b0f14e8` sur `main` par avance rapide depuis
  `fix/kanboard-0.17.3-date-modification`, branche supprimée. Poussé le
  21/10/2026 avec ce document ; le hook pre-push a sauté ses tests faute
  d'interpréteur pytest, la suite complète avait été passée juste avant.
- Installation en place le 21/10/2026 selon `docs/RELEASE.md` 4bis. Sauvegarde :
  `~/.config/Claude/Claude Extensions/.backup-kanboard-inno3-v7-2026-10-21-v0.17.2`.
  `src/server.py` remplacé, seule `version` modifiée dans le manifeste installé.
  Les `tools` installés sont conservés : mêmes 53 noms que le dépôt, seules les
  descriptions diffèrent, comme à l'installation du 17/10. `src/lib` inchangé.

### Vérifications

- Cinq tests de docstring, rouges avant la correction, verts après. Suite
  complète : 1 681 réussis, 1 ignoré ; ruff propre.
- `scripts/check_mcp_stdio.py` sur la copie installée, sous `/usr/bin/python3`
  (3.14.4) avec son `src/lib` : 53 outils uniques, `kanboard_list_tasks` et
  `kanboard_get_board` présents. Le script sort pourtant en échec sur ce
  connecteur : il refuse toute sortie d'erreur, et le serveur journalise ses
  requêtes en INFO. Même résultat sur la sauvegarde 0.17.2. `serverInfo.version`
  rend la version de la bibliothèque mcp (1.29.1), pas celle du connecteur.

### Limites et suite

- Le processus chargé reste la 0.17.2 jusqu'au redémarrage du client, sans
  différence de comportement attendue.
- Un critère de dormance qui compte les commentaires demande un appel
  `kanboard_list_comments` par carte candidate. Non implémenté, à décider selon
  le besoin réel.

## 17/10/2026 : préparation de l'intégration MB, n8n 1.3.1

Le correctif `03b4e10` utilise POST avec un objet JSON vide pour activer et
désactiver les workflows. Les 19 régressions couvrent aussi le transport,
les réponses perdues et le journal des écritures déjà acceptées. Les sources
ont été vérifiées sur `lot/2026-10-17-n8n-activation`, base `37f73a5`.

Version corrective préparée : **1.3.1**, après maximum du manifeste dépôt
**1.3.0** et de l'installation locale, où aucun manifeste n8n n'a été trouvé.
Le paquet global reste en 0.2.0. Aucune installation applicative ni publication.

Vérifications IM sur la branche MB : **1 532 réussis, 1 ignoré préexistant**
(intégration Dolibarr réelle) ; manifeste/outils **26 réussis** ; Ruff sur les
deux fichiers Python touchés **0 diagnostic**. Avant correction : **18 échecs,
1 réussite** sur les nouveaux tests n8n. Aucun accès réseau tenté.

Le merge et la numérotation sur `main` ont été bloqués pendant le lot IM par
la permission en lecture seule du répertoire `.git` ; ces résultats ne sont
pas une validation du `main` intégré. Réexécuter la suite après application
et consigner le commit final. Détails dans
[IM.rapport.md](/home/bjean/RepoGit/_lots_wt/_execution-2026-10-17/IM.rapport.md).
Recette réelle et diffusion : **à arbitrer par Benjamin**.

## 17/10/2026 : lot S, conditions de règlement du tiers en 1.8.4

### Objectif, décisions et état courant

Ajouter `cond_reglement_id` à `dolibarr_update_thirdparty`, avec validation
avant toute requête et relecture après écriture. Décision appliquée en version
**1.8.4**, source, manifeste, README et CHANGELOG synchronisés.

- Worktree : `/home/jean/ssd_data/jean/RepoGit/_lots_wt/S-mcp-foss-connectors`.
- Branche existante conservée : `lot/2026-10-17-tiers-reglement`.
- Départ vérifié : `cc67fc6`, arbre propre. Commit fonctionnel : `422f0d2`.
- Extension associée : `../S-inno3-mcp-extensions`, branche
  `lot/2026-10-17-carnets-commande`, départ `e388488`, version livrée **0.8.6**.
  Sa reprise décrit la preuve par commande. Commit fonctionnel : `acb8cc8`.
- Les versions installées 1.8.3 et 0.8.5 proviennent de la mission ; aucune
  installation inspectée ni modifiée. Travail seul, hors ligne, sans push ni tag.
- Rapport commun : [S.rapport.md](/home/bjean/RepoGit/_lots_wt/_rapports-2026-10-17/S.rapport.md).

Cette section décrit les worktrees du lot S. Les sections plus anciennes sont
historiques ; leurs chemins et assertions de poste n'ont pas été revalidés.
`AGENTS.md` et `CLAUDE.md` restent identiques et pointent vers ce document.

### Contrat livré et références

`cond_reglement_id` est facultatif (`None` conserve l'existant) et exige un
entier strictement positif. Booléens, chaînes et décimaux sont refusés aussi
par le schéma MCP. Les paramètres antérieurs gardent leur signature et leur
comportement ; le traitement enrichi s'applique lorsque la condition est fournie.
Le PUT `thirdparties/{id}` est suivi d'un GET du même tiers, même après erreur.

- `write_state` : `not_attempted`, `accepted`, `rejected` ou `unknown`.
- `write_may_have_succeeded` conserve la possibilité d'une écriture lorsque
  la réponse se perd ou que le serveur échoue. Aucun PUT n'est rejoué.
- `readback` rend les champs relus, la condition normalisée, son code et son
  libellé, ainsi que `cond_reglement_id_raw`. `verified` exige l'identifiant brut
  demandé ; une déduction par le code du dictionnaire ne suffit pas.
- `success` exige un PUT accepté et la condition confirmée. Après réponse
  perdue, la valeur peut être relue avec `verified=true` et `success=false`.
- Refus, écart et relecture impossible restent explicites. Un dictionnaire
  inaccessible ne masque pas la valeur relue. Le détail du tiers exposait déjà
  condition et libellé ; son contrat est vérifié par l'aller-retour.

Sources : `dolibarr_mcp/server.py`, `dolibarr_mcp/manifest.json`,
`dolibarr_mcp/README.md`, `dolibarr_mcp/CHANGELOG.md`.
Tests nouveaux : `tests/test_dolibarr_thirdparty_write.py` (**28 cas**).
Lecture antérieure : `tests/test_dolibarr_payment_terms.py`.
Les tirets cadratins des fichiers modifiés ont été remplacés par des deux-points,
y compris dans les textes historiques, conformément à la règle rédactionnelle.

### Vérifications exécutées

Venv existant : `/home/bjean/RepoGit/_lots_wt/_venv-mcp`.
Commande de base depuis la racine :
`PYTHONPATH=. <venv>/bin/python -m pytest tests -q -p no:cacheprovider`.

| Vérification | Résultat mesuré |
| --- | --- |
| Suite complète initiale | 1 485 réussis, 1 ignoré, 6,06 s |
| Écriture du tiers et conditions, première version des nouveaux tests | 104 réussis, 0,80 s |
| Suite complète après correction et tests complémentaires | 1 513 réussis, 1 ignoré, 5,90 s |
| Suite finale avec garde réseau et motifs des ignorés | **1 513 réussis, 1 ignoré, 6,02 s ; 0 tentative réseau** |
| Ruff sur tout le dépôt (`check --no-cache .`) | 0 diagnostic |
| `git diff --check` | Aucune erreur d'espacement |

La garde utilise `sys.addaudithook` avant l'import de pytest et refuse
`socket.connect`, `socket.getaddrinfo` et `socket.sendto`. Elle lance les mêmes
arguments que la commande de base, avec `-rs`. Le seul test ignoré est
l'intégration Dolibarr préexistante qui exige une instance réelle et y écrit.
Aucun test rouge initial dans le cœur, aucun test supprimé ou désactivé.

Comparaison AST exécutée avec `cc67fc6`, après normalisation des seuls tirets :
**78 outils**, une seule fonction et signature modifiées,
`dolibarr_update_thirdparty`, aucune fonction ajoutée ou supprimée. Manifeste
**1.8.4**, **78 outils**. Les 77 autres signatures restent identiques.

### Limites, arbitrages et prochaines actions

Les tests prouvent le transport et le traitement de réponses synthétiques,
pas la persistance sur l'instance réelle. L'existence de l'identifiant dans le
dictionnaire et les droits restent à valider par le serveur. Le PUT et le GET
ne sont pas transactionnels ; un autre acteur peut changer le tiers entre eux.
`verified` porte sur la condition, pas sur tous les autres champs transmis.

1. Relire et intégrer les deux branches du lot S en tenant compte du rapport commun.
2. Recette sur service réel, construction, installation et publication :
   **à arbitrer par Benjamin**, non exécutées ici.
3. Après intégration, faire archiver par l'orchestration les branches
   `lot/2026-10-17-tiers-reglement` et `lot/2026-10-17-carnets-commande`.
   Aucune branche supprimée dans ce lot.

---

Deux lots du 17/10/2026 sont fusionnés dans `main` : Kanboard 0.17.2 (K, K2)
et connecteur Dolibarr 1.8.3 / Nextcloud 1.4.1 (L). Leurs sections suivent.

## 17/10/2026 : lot K2 : fiabiliser l'identité Kanboard 0.17.2

### Objectif et état de reprise

Corriger le diagnostic primary sans changer de version, compléter les contrats
d'auteur des commentaires, supprimer l'injection d'un auteur égal à zéro et
garder les fichiers d'entrée limités au renvoi vers ce document.

- Worktree : `/home/jean/ssd_data/jean/RepoGit/_lots_wt/K-mcp-foss-connectors`.
- Branche existante vérifiée : `lot/2026-10-17-kanboard-0.17.2` ; départ K2
  **`86eecc5`**, arbre initial propre. Aucun autre worktree consulté.
- État fonctionnel livré en **`c3d2c1a`**, après **`5b95f82`** (identité) et
  **`1b5b706`** (contrats des commentaires). Le commit documentaire qui porte
  cette section termine le lot K2. Source et manifeste restent en **0.17.2**.
- Venv partagé : `/home/bjean/RepoGit/_lots_wt/_venv-mcp`.
- Sans réseau, installation, changement de branche, tag ni push ; travail seul.
- Les trois branches à archiver indiquées dans le lot K existent toujours.
- Source de vérité : ce document ; historique du lot K conservé ci-dessous.
  Les JSON `validation/kanboard-0172-comparison.json` et
  `validation/kanboard-0172-known-limits.json` décrivent **`86eecc5`**, pas les
  corrections K2. Leurs empreintes et limites ne décrivent plus le HEAD courant.
- Rapport K2 :
  `/home/bjean/RepoGit/_lots_wt/_rapports-2026-10-17/K2.rapport.md`.
  Le rapport K reste le relevé historique de l'alignement précédent.

### Avancée K2 et vérifications

La suite initiale complète donne **1 352 réussis, 1 ignoré**, en **5,54 s** ;
aucun test rouge préexistant. Ruff initial propre sur `kanboard_mcp/`,
`tests/test_kanboard_agent_identity.py` et `tests/test_kanboard_compat_016.py`.

Point 1, commit **`5b95f82`** : le diagnostic conserve le `verified` réel de la
garde. Un login différent renvoyé par `getMe` est refusé pour primary aussi,
y compris pendant sa sonde avant une écriture agent. Une sonde primary
indisponible reste tolérée ;
l'agent doit toujours établir sa propre identité, sans repli. Les neuf nouveaux
cas HTTP synthétiques donnent avant correction **5 échecs, 4 réussites,
39 désélectionnés**, en **0,13 s**, **0 tentative réseau**.
Après correction, les suites identité, compatibilité 0.16 et régression publique
donnent **63 réussis**, en **0,24 s**, **0 tentative réseau**. Un passage
intermédiaire donnait **1 échec, 62 réussites** : le libellé de refus agent
attendu par un contrat historique avait changé ; le marqueur `identite
incoherente` est conservé dans le message corrigé. Ruff et `git diff --check`
sont propres sur le périmètre modifié.

Point 2, commit **`1b5b706`** : les tests historiques comportaient déjà une
assertion minimale pour chacun des champs `author_read_error` et
`author_mismatch` sur les commentaires.
Ces assertions sont renforcées : succès maintenu, auteur relu ou inconnu,
preuve absente, cause conservée, aucun champ contradictoire et aucun rejeu de
création. Quatre cas supplémentaires couvrent un commentaire absent, un auteur
absent, un auteur illisible et un booléen. Aucun changement serveur nécessaire.
La suite de compatibilité donne **13 réussis**, en **0,07 s**, avec **0 tentative
réseau** ; Ruff et `git diff --check` propres.

Point 3, commit **`c3d2c1a`** : l'injection de zéro persistait après le point 1.
Les 24 nouveaux cas donnent avant correction **20 échecs, 4 réussites,
48 désélectionnés**, en
**0,30 s**, **0 tentative réseau**. Sans identité primary prouvée, `kb_call`
omet désormais `creator_id` / `user_id`, même si un appelant fournit un auteur ;
il n'utilise pas le cache de résolution. `kanboard_add_comment` laisse cette
attribution à `kb_call` et ne compare plus l'auteur relu au zéro d'une identité
absente. Un auteur relu à zéro reste non vérifié. Les identités prouvées primary
et agent continuent à imposer leur ID, sans muter les paramètres de l'appelant.
Après correction, identité, compatibilité, régression publique et transport
donnent **141 réussis**, en **0,36 s**, **0 tentative réseau**.
La suite complète donne **1 389 réussis, 1 ignoré**, en **5,65 s** (`-rs`) ;
le seul ignoré reste l'intégration Dolibarr qui exige un service réel.
`ruff check --no-cache . --output-format concise` est propre sur tout le dépôt.

Point 4 : `AGENTS.md` et `CLAUDE.md` sont identiques et ne gardent que le renvoi
à cette reprise. Leurs paragraphes propres au lot K sont retirés. Le
[CHANGELOG du connecteur](../kanboard_mcp/CHANGELOG.md), créé dans K2 car aucun
fichier dédié n'existait, et son README décrivent les corrections.

Validation finale sous garde réseau :
`PYTHONPATH=. <venv>/bin/python scripts/test_kanboard_crosscheck_offline.py tests`
→ **1 389 réussis, 1 ignoré**, en **5,35 s**, **0 tentative réseau**.
Les 37 cas supplémentaires et les assertions renforcées passent dans la collecte
ordinaire. Ruff est propre sur tout le dépôt ; `git diff --check` et les contrôles
de diff indexé sont propres. Aucun test n'a été ignoré ou supprimé pour ce lot.

Comparaison AST exécutée avec `86eecc5` : **4 fonctions modifiées**, aucune
ajoutée ni supprimée (`_ensure_write_identity`, `_ensure_account_identity`,
`kb_call`, `kanboard_add_comment`). Versions source et manifeste **0.17.2**
vérifiées. SHA-256 du serveur final :
`0598618a025183b20f7075705b4d4db0525998a65934b4d5bc4b73f48f9f1777`.

### Limites, arbitrages et prochaines actions

Les deux limites primary du lot K sont corrigées dans le dépôt ; les relevés
JSON historiques restent datés de `86eecc5`. Aucune vérification de l'extension
installée, recette réelle, construction de bundle, installation ou publication
n'a été exécutée dans K2. L'attribution effective par un serveur réel lorsqu'on
omet l'auteur reste **à confirmer**. Les tests prouvent les paramètres émis
et le traitement de réponses synthétiques, pas le comportement du service.

1. Relire les quatre commits K2 et intégrer la branche au dépôt canonique.
2. Recette, installation et publication 0.17.2 **à arbitrer par Benjamin**, dont
   le cas primary sans `getMe` avec attribution de l'auteur par Kanboard.
3. Extension des relectures après déplacement, fermeture et réouverture :
   **à arbitrer par Benjamin** ; elle reste hors du périmètre K2, comme dans K.
4. Claude Code pourra archiver, après intégration, `fix/kanboard-identite-agent`,
   `lot/2026-10-10-kanboard-identite` et
   `fix/kanboard-0.17.0-board-status-entites-dates`. Aucune branche supprimée ici.

État au 21/10/2026 : 1 et 4 sont faits (intégration dans `main`, branches
supprimées) ; 2 l'est pour l'installation et la recette du tableau, pas pour le
cas primary sans `getMe` ; 3 reste ouvert. Voir la reprise courante.

Ne pas reprendre les consignes de poste périmées des branches historiques ; les
autres worktrees restent des chantiers distincts. Le répertoire interdit
`/home/bjean/RepoGit/mcp-wt-builddoc` n'a été ni consulté ni modifié.

## 17/10/2026 : lot K : réunir Kanboard en 0.17.2

**Historique au commit `86eecc5` ; la section K2 ci-dessus fait foi pour la suite.**

### Objectif et décisions

Reprendre dans le dépôt canonique le comportement de la 0.17.1 installée, puis
les apports Kanboard de `adc1df0` : statut du tableau, entités HTML et dates.
Conserver les 53 outils, les paramètres existants et les champs historiques
d'auteur. Les paramètres facultatifs `status` du tableau et `modified_before`
de la liste sont les deux extensions explicites de signature en 0.17.2.

Le source installé est la référence de comportement : garde agent sans repli
en écriture, repli conservé en lecture, primary tolérant un `getMe` indisponible.
Le manifeste canonique conserve son nom générique et ses réglages sans données
d'instance. Aucun autre connecteur n'est à reprendre depuis les branches anciennes.

### État courant

- Worktree autorisé : `/home/jean/ssd_data/jean/RepoGit/_lots_wt/K-mcp-foss-connectors`.
- Branche : `lot/2026-10-17-kanboard-0.17.2`, base `82fcbed`.
- Alignement 0.17.1 committé en **`c0f006e`** : source installé repris intégralement, classe
  `KanboardIdentityError` comprise ; `_identity_refusal` appelle directement
  cette classe au lieu du mécanisme conditionnel de la surcouche, sans différence
  de comportement. Suite complète : **1 285 réussis, 1 ignoré** (5,44 s).
- 0.17.2 réalisée : les seuls apports Kanboard de `adc1df0` sont reportés,
  avec décodage du cache de noms alimenté par la garde d'écriture. Aucun autre
  connecteur modifié. Le commit suivant `c0f006e` porte ce report et ses preuves.
- État final validé : **1 352 tests réussis, 1 ignoré**, Ruff propre ; aucun
  appel réseau, aucune installation et aucun push. L'extension reste en 0.17.1.
- Référence installée lue uniquement :
  `~/.config/Claude/Claude Extensions/local.mcpb.inno.kanboard-inno3-v7/src/server.py`.
  SHA-256 : `50fa8b1f03855ff8efc91e681dfdb617fa2b81b73d7fec808fe3fc16d6a9a597`.
- `mcp-inno3` est une source secondaire en lecture seule. Son checkout courant
  n'est pas la référence : lire les objets Git de `main` (`46e0c61` constaté),
  notamment `218842c`, `ceac080`, `16f2a23`, `57999f0`, le début de
  `docs/REPRISE.md` et les contrats `docs/validation/kanboard-compat-20261014/`.
- Les anciens documents de branche décrivent des installations antérieures ;
  leurs assertions sur l'état du poste et leurs chemins de venv sont périmés.

### Vérifications exécutées

Depuis la racine, avec le venv partagé
`/home/bjean/RepoGit/_lots_wt/_venv-mcp` :

- État initial : `PYTHONPATH=. <venv>/bin/python -m pytest tests -q -p no:cacheprovider`
  → **1 220 réussis, 1 ignoré**, en 5,08 s. Aucun test rouge préexistant.
- État initial : `<venv>/bin/python -m ruff check --no-cache kanboard_mcp/`
  → **All checks passed**.
- Avant modification du serveur, les 37 tests d'identité importés donnent
  **36 échecs, 1 réussite** ; les 9 contrats 0.16 donnent **9 erreurs de setup**
  (cache d'identité absent). Le lanceur à garde réseau constate **0 tentative réseau**.
- Empreinte et signatures extraites du fichier installé : **53 outils**.
- Après alignement 0.17.1 : **1 285 réussis, 1 ignoré** pour la suite complète.
  **114 contrats** (identité, compatibilité, seconde lignée, création, transport) rejoués
  sur le fichier réellement installé : tous réussis, **0 tentative réseau**.
- Comparaison AST à l'installé : aucune fonction ajoutée ni supprimée ; seul
  `_identity_refusal` diffère (appel direct de la même exception). Manifestes :
  **53 noms identiques**, versions 0.17.1. Test des 53 signatures réussi.
- Ruff propre sur le connecteur et tous les nouveaux tests/lanceur ;
  `git diff --check` propre.
- Avant le report de `adc1df0`, ses tests donnent **63 échecs, 135 réussites**
  dans `tests/test_kanboard.py`. Après report, les **198 cas** de ce fichier
  passent. Deux tests ajoutés pour le cache de noms après écriture échouent
  avant la correction d'intégration puis passent (primary et agent).
- Validation finale : commande pytest ci-dessus avec `-rs` → **1 352 réussis,
  1 ignoré**, en **5,74 s**. Le seul ignoré est le test d'intégration Dolibarr
  préexistant qui exige une instance réelle et y écrit (`tests/test_dolibarr.py`).
- `PYTHONPATH=. <venv>/bin/python scripts/test_kanboard_crosscheck_offline.py tests`
  → **1 352 réussis, 1 ignoré**, en **5,17 s**, **0 tentative réseau**.
- `PYTHONPATH=. <venv>/bin/python -m pytest tests/test_manifests.py tests/test_kanboard_signatures.py -q -p no:cacheprovider`
  → **27 réussis**, en **1,33 s**.
- Comparaison des schémas réellement enregistrés par FastMCP avec l'installé :
  **53 outils, 51 schémas identiques**, deux paramètres facultatifs ajoutés,
  aucun ancien paramètre changé. **0 tentative réseau**.

### Diff fonctionnel final avec l'installé

L'inventaire AST et les empreintes sont dans
[`validation/kanboard-0172-comparison.json`](validation/kanboard-0172-comparison.json).
Quatre helpers ajoutés, aucune fonction supprimée, 34 fonctions modifiées
hors docstrings (dont `_identity_refusal`, équivalent fonctionnellement).

- Version annoncée : 0.17.2. Le manifeste canonique reste générique.
- Tableau : `status=1` par défaut, `0` pour les fermées, `-1` pour tout ; les
  comptes et la troncature portent sur le périmètre filtré. Une carte sans statut
  reste visible, avec `status_filter_incomplete` sur sa colonne.
- Tableau et résumés de tâches : dates de création/modification ; état en
  entier 0/1 ou null. Les résumés alimentent listes, recherche, dashboard et
  retards. Le champ `is_active` du détail `get_task` reste brut comme l'installé.
- Liste des tâches : `modified_before` facultatif, borne stricte dans le fuseau
  local, appliquée avant pagination ; cartes indatables écartées et comptées.
- Textes de lecture concernés : entités HTML décodées une fois avant troncature,
  y compris titres, commentaires, libellés, personnes, activité, structure et
  liens. Les champs textuels convertis passent par `str(value or "")`, donc
  une valeur absente devient une chaîne vide. Les valeurs envoyées aux écritures,
  comparaisons de login et listes d'ambiguïté restent brutes.
- Noms d'affichage après écriture : même décodage unique à l'entrée du cache,
  sans modifier l'identité brute vérifiée par `getMe`.
- `_identity_refusal` lève directement `KanboardIdentityError` au lieu de la
  rechercher dans `globals()` : même type, même message, même `write_state`.
  Garde agent, transport, journaux et champs historiques d'auteur conservés.

### Fichiers de référence et suite du travail

- `tests/test_kanboard_agent_identity.py` : 37 cas de `mcp-inno3/main`, plus
  2 cas d'intégration du décodage des noms après écriture (39 au total).
- `tests/test_kanboard_compat_016.py` : 9 contrats historiques, désormais collectés.
- `tests/test_kanboard_weekend_regression.py` : protection publique issue de `55e6278`.
- `tests/test_kanboard_verified_write.py` : 12 cas de relecture de création et
  des helpers présents dans l'installé. Les 5 cas supplémentaires de `mcp-inno3`
  exigeant une relecture après déplacement/fermeture/réouverture échouent sur
  l'installé (champ `verified` absent) : comportement hors alignement, non porté.
  Ce constat ne concerne pas la suite initiale du dépôt, verte.
  **Portage réalisé le 22/10/2026** sur `lot/2026-10-22-kanboard-relecture`,
  commit **`c663013`**, en **0.18.0** : les 5 cas sont verts,
  avec leurs assertions d'origine conservées. Voir la reprise courante.
- `tests/fixtures/kanboard_0171_signatures.json` : signatures extraites de l'installé.
- `scripts/test_kanboard_crosscheck_offline.py` : lanceur avec garde réseau.
- Rapport final :
  `/home/bjean/RepoGit/_lots_wt/_rapports-2026-10-17/K.rapport.md`.

Prochaines actions, dans l'ordre : relire les deux commits et les limites
ci-dessous ; intégrer cette branche dans le dépôt canonique ; décider d'une
recette et d'une installation séparées ; archiver les trois branches anciennes.

### Limites et branches à archiver après intégration

Aucune recette sur service réel ni installation exécutée dans ce lot.
La publication et l'installation de la 0.17.2 restent **à arbitrer par Benjamin**.

Deux limites de la 0.17.1 sont volontairement conservées pour respecter
l'alignement demandé ; une sonde avec réponses synthétiques les reproduit sur
les deux sources, sans tentative réseau. Résultats dans
[`validation/kanboard-0172-known-limits.json`](validation/kanboard-0172-known-limits.json) :

- si `getMe` primary est muet, `_ensure_account_identity` rend encore
  `verified=true`, `user_id=0` et une note disant que l'identité n'est pas vérifiée ;
- dans ce cas, `kb_call` injecte encore `creator_id=0` / `user_id=0` à la
  création. La note de `mcp-inno3` affirmant qu'aucun zéro n'est injecté ne
  décrit donc pas exactement l'installé. La réponse réelle de Kanboard à ces
  paramètres n'a pas été testée. Correction éventuelle **à arbitrer par Benjamin**.

La relecture après déplacement/fermeture/réouverture, présente dans les tests
du source autonome `mcp-inno3` mais absente de l'extension, restait
**à arbitrer par Benjamin au terme de K**. Les 5 cas n'appartenaient pas à cet
alignement. Ils sont désormais portés en **0.18.0** sur la branche du lot du
22/10/2026, commit **`c663013`** (voir la reprise courante).

Claude Code se chargera ensuite de l'archivage de ces branches ; elles ne sont
pas supprimées ici :

- `fix/kanboard-identite-agent` : garde d'identité 0.16.
- `lot/2026-10-10-kanboard-identite` : contre-vérification de cette garde.
- `fix/kanboard-0.17.0-board-status-entites-dates` : correctifs de lecture `adc1df0`.

Les autres worktrees du dépôt sont des chantiers distincts. En particulier,
`/home/bjean/RepoGit/mcp-wt-builddoc` ne doit pas être touché.


## 17/10/2026 : lot L terminé, carnets et échéances

### Objectif, branche et état vérifié

Réunir les deux lignées parallèles du lot A sans remplacer la base actuelle.
Worktree : `/home/jean/ssd_data/jean/RepoGit/_lots_wt/L-mcp-foss-connectors`.
Branche : `lot/2026-10-17-carnets`, partie de `main` à `82fcbed`.
Dernier commit fonctionnel : `310d6f6`. Dolibarr **1.8.3**, Nextcloud **1.4.1**,
paquet commun inchangé **0.2.0**. Les commits documentaires suivants ne changent
pas les sources testées ; reprendre depuis le HEAD de la branche du lot.

Extension associée : `../L-inno3-mcp-extensions`, même branche, base `dd31491`,
version **0.8.4**, dernier commit fonctionnel `4cd4512`. Son `docs/REPRISE.md`
détaille le contrôle des carnets et la préservation de MeetingNotes 0.8.1.
Les versions installées 1.8.0 et 0.8.1 sont des données de la mission,
pas des mesures de cette session : aucune installation inspectée ou modifiée.

### Décisions et commits

| Commit | Version | Contenu |
| --- | --- | --- |
| `64f64bd` | Dolibarr 1.8.1 | Conditions de règlement de `852659f`, échéance commune de `073a2a6`, tests de `35b643a`, contrôle de version MCP de `76a307b` |
| `892f4f9` | Dolibarr 1.8.2 | Temps bornés et paginés de `a62fd5a`, résultats conservés après erreur ou expiration |
| `92b9d59` | Nextcloud 1.4.1 | Racines d'envoi résolue et configurée de `183a2a0`, protections de main conservées |
| `310d6f6` | Dolibarr 1.8.3 | Métadonnées d'export de main conservées dans les temps, curseur v2 avec décalage cumulé |

La reprise de base `03b1710` est déjà dans `main` : AST de Dolibarr et Nextcloud
identiques après normalisation des seuls tirets typographiques. Aucun remplacement
complet depuis l'ancienne branche : les autres connecteurs et leurs tests restent
ceux de main. README, CHANGELOG et manifeste sont synchronisés par connecteur.

L'indicateur `date_due_is_default` reste une heuristique (facture + un jour), à
vérifier avant une relance. Les alias d'échéance ont la même priorité en liste,
en détail et en projection, y compris quand le premier est explicitement nul.
Les conditions du tiers ne remplacent pas celles de la facture. Les erreurs de
lecture complémentaires restent signalées sans rendre la facture illisible.

### Vérifications réellement exécutées

Depuis la racine du worktree, avec le venv partagé existant :

```sh
PYTHONPATH=. /home/bjean/RepoGit/_lots_wt/_venv-mcp/bin/python -m pytest tests -q -p no:cacheprovider
/home/bjean/RepoGit/_lots_wt/_venv-mcp/bin/python -m ruff check --no-cache .
git diff --check
```

- Départ : **1 220 réussis, 1 ignoré**, 5,18 s.
- Échéances, avant le helper commun : **1 échec, 1 réussite** ; projection
  fournisseur perdant `date_echeance`. Après intégration 1.8.1 : suite complète
  **1 307 réussis, 1 ignoré**, 5,35 s.
- Temps, tests Dolibarr, exports et manifestes : **416 réussis, 1 ignoré**, 2,96 s.
- Nextcloud, transport et manifestes : **189 réussis**, 1,67 s.
- Après les reprises 1.8.2 / 1.4.1 : **1 315 réussis, 1 ignoré**, 5,60 s.
- Revue de compatibilité temps : **1 échec reproduit** sur `offset` absent.
  Métadonnées restaurées dans 1.8.3.
- Final 1.8.3 : **1 316 réussis, 1 ignoré**, 5,30 s. Le test ignoré est
  `test_integration_assign_log_reread_5400_seconds`, qui exige une instance
  réelle et écrit dedans ; il était déjà ignoré au départ.
- Ruff sur tout le cœur : **0 diagnostic**. Espacement : aucune erreur.
- Contrôle statique du constructeur : **78 outils cœur + 32 extension = 110**,
  sans doublon d'extension ; **14 outils Nextcloud**. Aucun bundle construit.
- Extension avec ce cœur : **300 réussis, 2 échecs d'environnement connus**,
  2,41 s ; voir sa reprise. Les tests MCP en mémoire couvrent les 110 outils.

Échecs intermédiaires corrigés, conservés dans le rapport : attente de version
source 1.9.0 (**489 réussis, 1 ignoré, 1 échec**), ordre d'import du test repris
(**415 réussis, 1 ignoré, 1 échec**), puis lecture « complète » du nouveau test
limitée par défaut à 20 au lieu de 21 lignes (**58 réussis, 1 échec**).

### Limites, hypothèses et arbitrages

- Aucun appel réseau réel, construction, déploiement ou test du module serveur.
  Les constats historiques du lot A ne valent pas recette actuelle.
- Temps : 20 tâches et 20 secondes par défaut, 100 tâches et 25 secondes au plus.
  Les données doivent rester stables pendant l'export. Repartir sans curseur
  depuis 1.8.0 ou le prototype 1.8.2. Le curseur v2 transporte `offset` cumulé.
- Les agrégats des temps couvrent les tâches lues dans l'appel, avec la partie
  non rendue d'une tâche tronquée. Additionner les écritures exportées, pas les
  agrégats de pages. `scan_truncated` signale ce périmètre partiel même en dernière
  page ; `truncated` signifie qu'il reste une suite à lire.
- Le dictionnaire des conditions est mémorisé, échecs compris, pendant la session.
  La compatibilité du service réel et l'interprétation métier des échéances
  restent **à arbitrer par Benjamin** avant utilisation pour les relances.

### Références, autres branches et suite ordonnée

Références : `dolibarr_mcp/{server.py,README.md,CHANGELOG.md,manifest.json}`,
`nextcloud_mcp/`, tests paiement/échéances/temps/exports/transport,
`tests/test_manifests.py` et `scripts/build_mcpb.py`.

Sources consultées par Git uniquement : `fix/dolibarr-carnets-echeances`
(`852659f`, worktree historique `mcp-wt-carnets/mcp-foss-connectors`) et
`lot/2026-10-10-dolibarr-nextcloud` (`76a307b`, `_lots_wt/a-mcp-foss-connectors`).
Leur rôle est celui de sources historiques à archiver après intégration ; leurs
worktrees n'ont pas été modifiés. Aucun fichier de `mcp-wt-builddoc` n'a été lu.

Le rapport transversal est [L.rapport.md](/home/bjean/RepoGit/_lots_wt/_rapports-2026-10-17/L.rapport.md).
Il contient le classement de chaque commit source, les commandes de recette,
les branches à archiver et la construction depuis `git archive`, non exécutée.

1. Relire et intégrer les deux branches du lot ensemble.
2. **À arbitrer par Benjamin** : préparer les dépendances de construction hors
   ligne, construire depuis les commits et programmer une recette isolée.
3. Après validation, archiver les branches historiques indiquées dans le rapport
   (Claude Code s'en charge). Aucune branche ni aucun tag supprimé par ce lot.
