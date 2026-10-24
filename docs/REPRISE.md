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

- [Rapport du lot](/home/jean/ssd_data/jean/RepoGit/_lots_wt/_rapports-2026-10-22/kanboard-relecture.md) :
  preuves rouge puis vert, commandes, exemples avant/après et risques résiduels.
- Sources : `kanboard_mcp/{server.py,manifest.json,README.md,CHANGELOG.md}` ;
  contrats : `tests/test_kanboard_verified_write.py`, transport, manifestes et
  signatures. `AGENTS.md` et `CLAUDE.md` restent identiques et pointent ici.
- Référence historique lue seulement : `/home/jean/RepoGit/mcp-inno3`, commit
  **`7f8ecf3`** du 17/08/2026 et ses cinq tests d'état. Aucun helper historique
  recopié ; les helpers du dépôt canonique sont réutilisés.
- Le checkout principal `/home/jean/ssd_data/jean/RepoGit/inno3/mcp-foss-connectors`
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
  [reprise de mcp-inno3](/home/jean/ssd_data/jean/RepoGit/mcp-inno3/docs/REPRISE.md).
- **Vérifié sur disque pendant H** : Dolibarr est désormais en **1.8.6** ;
  les cinq fichiers source installés sont identiques à ceux de `main`.
  Ce constat complète le relevé de 10h36, sans prouver un nouveau rechargement.
  L'extension métier embarquée reste en **0.8.6**, voir la
  [reprise des extensions](/home/jean/ssd_data/jean/RepoGit/inno3-mcp-extensions/docs/REPRISE.md).
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
  `/home/jean/ssd_data/jean/RepoGit/_lots_wt/_venv-mcp/bin/python`.
- **Chantier ouvert** : builddoc reste hors `main`, branche
  `fix/dolibarr-list-documents-meetingnotes`, commit `4445d44`, worktree
  `/home/jean/ssd_data/jean/RepoGit/mcp-wt-builddoc/mcp-foss-connectors`.
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
canonique `/home/jean/ssd_data/jean/RepoGit/inno3/mcp-foss-connectors` est sur
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

Venv existant : `/home/jean/ssd_data/jean/RepoGit/_lots_wt/_venv-mcp`.
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
PYTHONPATH=. /home/jean/ssd_data/jean/RepoGit/_lots_wt/_venv-mcp/bin/python -m pytest tests -q -p no:cacheprovider
/home/jean/ssd_data/jean/RepoGit/_lots_wt/_venv-mcp/bin/python -m ruff check --no-cache .
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

[Rapport MC](/home/jean/ssd_data/jean/RepoGit/_lots_wt/_execution-2026-10-17/MC.rapport.md).
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
[IM.rapport.md](/home/jean/ssd_data/jean/RepoGit/_lots_wt/_execution-2026-10-17/IM.rapport.md).
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
- Rapport commun : [S.rapport.md](/home/jean/ssd_data/jean/RepoGit/_lots_wt/_rapports-2026-10-17/S.rapport.md).

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

Venv existant : `/home/jean/ssd_data/jean/RepoGit/_lots_wt/_venv-mcp`.
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
- Venv partagé : `/home/jean/ssd_data/jean/RepoGit/_lots_wt/_venv-mcp`.
- Sans réseau, installation, changement de branche, tag ni push ; travail seul.
- Les trois branches à archiver indiquées dans le lot K existent toujours.
- Source de vérité : ce document ; historique du lot K conservé ci-dessous.
  Les JSON `validation/kanboard-0172-comparison.json` et
  `validation/kanboard-0172-known-limits.json` décrivent **`86eecc5`**, pas les
  corrections K2. Leurs empreintes et limites ne décrivent plus le HEAD courant.
- Rapport K2 :
  `/home/jean/ssd_data/jean/RepoGit/_lots_wt/_rapports-2026-10-17/K2.rapport.md`.
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
`/home/jean/ssd_data/jean/RepoGit/mcp-wt-builddoc` n'a été ni consulté ni modifié.

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
`/home/jean/ssd_data/jean/RepoGit/_lots_wt/_venv-mcp` :

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
  `/home/jean/ssd_data/jean/RepoGit/_lots_wt/_rapports-2026-10-17/K.rapport.md`.

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
`/home/jean/ssd_data/jean/RepoGit/mcp-wt-builddoc` ne doit pas être touché.


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
PYTHONPATH=. /home/jean/ssd_data/jean/RepoGit/_lots_wt/_venv-mcp/bin/python -m pytest tests -q -p no:cacheprovider
/home/jean/ssd_data/jean/RepoGit/_lots_wt/_venv-mcp/bin/python -m ruff check --no-cache .
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

Le rapport transversal est [L.rapport.md](/home/jean/ssd_data/jean/RepoGit/_lots_wt/_rapports-2026-10-17/L.rapport.md).
Il contient le classement de chaque commit source, les commandes de recette,
les branches à archiver et la construction depuis `git archive`, non exécutée.

1. Relire et intégrer les deux branches du lot ensemble.
2. **À arbitrer par Benjamin** : préparer les dépendances de construction hors
   ligne, construire depuis les commits et programmer une recette isolée.
3. Après validation, archiver les branches historiques indiquées dans le rapport
   (Claude Code s'en charge). Aucune branche ni aucun tag supprimé par ce lot.
