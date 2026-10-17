# Reprise du dépôt mcp-foss-connectors

Deux lots du 17/10/2026 sont fusionnés dans `main` : Kanboard 0.17.2 (K, K2)
et connecteur Dolibarr 1.8.3 / Nextcloud 1.4.1 (L). Leurs sections suivent.

## 17/10/2026 — lot K2 : fiabiliser l'identité Kanboard 0.17.2

### Objectif et état de reprise

Corriger le diagnostic primary sans changer de version, compléter les contrats
d'auteur des commentaires, supprimer l'injection d'un auteur égal à zéro et
garder les fichiers d'entrée limités au renvoi vers ce document.

- Worktree : `/home/jean/ssd_data/jean/RepoGit/_codex_wt/K-mcp-foss-connectors`.
- Branche existante vérifiée : `codex/2026-10-17-kanboard-0.17.2` ; départ K2
  **`86eecc5`**, arbre initial propre. Aucun autre worktree consulté.
- État fonctionnel livré en **`c3d2c1a`**, après **`5b95f82`** (identité) et
  **`1b5b706`** (contrats des commentaires). Le commit documentaire qui porte
  cette section termine le lot K2. Source et manifeste restent en **0.17.2**.
- Venv partagé : `/home/jean/ssd_data/jean/RepoGit/_codex_wt/_venv-mcp`.
- Sans réseau, installation, changement de branche, tag ni push ; travail seul.
- Les trois branches à archiver indiquées dans le lot K existent toujours.
- Source de vérité : ce document ; historique du lot K conservé ci-dessous.
  Les JSON `validation/kanboard-0172-comparison.json` et
  `validation/kanboard-0172-known-limits.json` décrivent **`86eecc5`**, pas les
  corrections K2. Leurs empreintes et limites ne décrivent plus le HEAD courant.
- Rapport K2 :
  `/home/jean/ssd_data/jean/RepoGit/_codex_wt/_rapports-2026-10-17/K2.rapport.md`.
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
   `codex/2026-10-10-kanboard-identite` et
   `fix/kanboard-0.17.0-board-status-entites-dates`. Aucune branche supprimée ici.

Ne pas reprendre les consignes de poste périmées des branches historiques ; les
autres worktrees restent des chantiers distincts. Le répertoire interdit
`/home/jean/ssd_data/jean/RepoGit/mcp-wt-builddoc` n'a été ni consulté ni modifié.

## 17/10/2026 — lot K : réunir Kanboard en 0.17.2

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

- Worktree autorisé : `/home/jean/ssd_data/jean/RepoGit/_codex_wt/K-mcp-foss-connectors`.
- Branche : `codex/2026-10-17-kanboard-0.17.2`, base `82fcbed`.
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
`/home/jean/ssd_data/jean/RepoGit/_codex_wt/_venv-mcp` :

- État initial : `PYTHONPATH=. <venv>/bin/python -m pytest tests -q -p no:cacheprovider`
  → **1 220 réussis, 1 ignoré**, en 5,08 s. Aucun test rouge préexistant.
- État initial : `<venv>/bin/python -m ruff check --no-cache kanboard_mcp/`
  → **All checks passed**.
- Avant modification du serveur, les 37 tests d'identité importés donnent
  **36 échecs, 1 réussite** ; les 9 contrats 0.16 donnent **9 erreurs de setup**
  (cache d'identité absent). Le lanceur à garde réseau constate **0 tentative réseau**.
- Empreinte et signatures extraites du fichier installé : **53 outils**.
- Après alignement 0.17.1 : **1 285 réussis, 1 ignoré** pour la suite complète.
  **114 contrats** (identité, compatibilité, Codex, création, transport) rejoués
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
- `tests/fixtures/kanboard_0171_signatures.json` : signatures extraites de l'installé.
- `scripts/test_kanboard_crosscheck_offline.py` : lanceur avec garde réseau.
- Rapport final :
  `/home/jean/ssd_data/jean/RepoGit/_codex_wt/_rapports-2026-10-17/K.rapport.md`.

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
du source autonome `mcp-inno3` mais absente de l'extension, reste aussi
**à arbitrer par Benjamin**. Les 5 cas correspondants n'ont pas été maquillés
en tests verts : ils ne font pas partie de l'alignement importé (voir plus haut).

Claude Code se chargera ensuite de l'archivage de ces branches ; elles ne sont
pas supprimées ici :

- `fix/kanboard-identite-agent` : garde d'identité 0.16.
- `codex/2026-10-10-kanboard-identite` : contre-vérification de cette garde.
- `fix/kanboard-0.17.0-board-status-entites-dates` : correctifs de lecture `adc1df0`.

Les autres worktrees du dépôt sont des chantiers distincts. En particulier,
`/home/jean/ssd_data/jean/RepoGit/mcp-wt-builddoc` ne doit pas être touché.


## 17/10/2026 — lot L terminé, carnets et échéances

### Objectif, branche et état vérifié

Réunir les lignées Claude et Codex du lot A sans remplacer la base actuelle.
Worktree : `/home/jean/ssd_data/jean/RepoGit/_codex_wt/L-mcp-foss-connectors`.
Branche : `codex/2026-10-17-carnets`, partie de `main` à `82fcbed`.
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
PYTHONPATH=. /home/jean/ssd_data/jean/RepoGit/_codex_wt/_venv-mcp/bin/python -m pytest tests -q -p no:cacheprovider
/home/jean/ssd_data/jean/RepoGit/_codex_wt/_venv-mcp/bin/python -m ruff check --no-cache .
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
`codex/2026-10-10-dolibarr-nextcloud` (`76a307b`, `_codex_wt/a-mcp-foss-connectors`).
Leur rôle est celui de sources historiques à archiver après intégration ; leurs
worktrees n'ont pas été modifiés. Aucun fichier de `mcp-wt-builddoc` n'a été lu.

Le rapport transversal est [L.rapport.md](../../_rapports-2026-10-17/L.rapport.md).
Il contient le classement de chaque commit source, les commandes de recette,
les branches à archiver et la construction depuis `git archive`, non exécutée.

1. Relire et intégrer les deux branches du lot ensemble.
2. **À arbitrer par Benjamin** : préparer les dépendances de construction hors
   ligne, construire depuis les commits et programmer une recette isolée.
3. Après validation, archiver les branches historiques indiquées dans le rapport
   (Claude Code s'en charge). Aucune branche ni aucun tag supprimé par ce lot.
