# 17/10/2026 — lot K2 : fiabiliser l'identité Kanboard 0.17.2

## Objectif et état de reprise

Corriger le diagnostic primary sans changer de version, compléter les contrats
d'auteur des commentaires, supprimer l'injection d'un auteur égal à zéro et
garder les fichiers d'entrée limités au renvoi vers ce document.

- Worktree : `/home/jean/ssd_data/jean/RepoGit/_codex_wt/K-mcp-foss-connectors`.
- Branche existante vérifiée : `codex/2026-10-17-kanboard-0.17.2` ; départ K2
  **`bff1697`**, arbre initial propre. Aucun autre worktree consulté.
- Venv partagé : `/home/jean/ssd_data/jean/RepoGit/_codex_wt/_venv-mcp`.
- Sans réseau, installation, changement de branche, tag ni push ; travail seul.
- Les trois branches à archiver indiquées dans le lot K existent toujours.
- Source de vérité : ce document ; historique du lot K conservé ci-dessous.
  Les JSON `validation/kanboard-0172-comparison.json` et
  `validation/kanboard-0172-known-limits.json` décrivent **`bff1697`**, pas les
  corrections K2. Leurs empreintes et limites ne décrivent plus le HEAD courant.

## Avancée K2 et vérifications

La suite initiale complète donne **1 352 réussis, 1 ignoré**, en **5,54 s** ;
aucun test rouge préexistant. Ruff initial propre sur `kanboard_mcp/`,
`tests/test_kanboard_agent_identity.py` et `tests/test_kanboard_compat_016.py`.

Point 1, commit **`3a045f1`** : le diagnostic conserve le `verified` réel de la garde. Un login
différent renvoyé par `getMe` est refusé pour primary aussi, y compris pendant
sa sonde avant une écriture agent. Une sonde primary indisponible reste tolérée ;
l'agent doit toujours établir sa propre identité, sans repli. Les neuf nouveaux
cas HTTP synthétiques donnent avant correction **5 échecs, 4 réussites,
39 désélectionnés**, en **0,13 s**, **0 tentative réseau**.
Après correction, les suites identité, compatibilité 0.16 et régression publique
donnent **63 réussis**, en **0,24 s**, **0 tentative réseau**. Un passage
intermédiaire donnait **1 échec, 62 réussites** : le libellé de refus agent
attendu par un contrat historique avait changé ; le marqueur `identite
incoherente` est conservé dans le message corrigé. Ruff et `git diff --check`
sont propres sur le périmètre modifié.

Point 2 : les tests historiques comportaient déjà une assertion minimale pour
chacun des champs `author_read_error` et `author_mismatch` sur les commentaires.
Ces assertions sont renforcées : succès maintenu, auteur relu ou inconnu,
preuve absente, cause conservée, aucun champ contradictoire et aucun rejeu de
création. Quatre cas supplémentaires couvrent un commentaire absent, un auteur
absent, un auteur illisible et un booléen. Aucun changement serveur nécessaire.
La suite de compatibilité donne **13 réussis**, en **0,07 s**, avec **0 tentative
réseau** ; Ruff et `git diff --check` propres.

## Prochaines actions K2

1. Point 1 committé en `3a045f1`.
2. Point 2 vérifié ; son commit accompagne cette mise à jour.
3. Vérifier l'injection des auteurs zéro et la corriger séparément si nécessaire.
4. Retirer les paragraphes propres au lot K de `AGENTS.md` et `CLAUDE.md`.
5. Exécuter la suite complète, Ruff et le contrôle de diff ; finaliser la reprise
   et `/home/jean/ssd_data/jean/RepoGit/_codex_wt/_rapports-2026-10-17/K2.rapport.md`.

# 17/10/2026 — lot K : réunir Kanboard en 0.17.2

**Historique au commit `bff1697` ; la section K2 ci-dessus fait foi pour la suite.**

## Objectif et décisions

Reprendre dans le dépôt canonique le comportement de la 0.17.1 installée, puis
les apports Kanboard de `959203a` : statut du tableau, entités HTML et dates.
Conserver les 53 outils, les paramètres existants et les champs historiques
d'auteur. Les paramètres facultatifs `status` du tableau et `modified_before`
de la liste sont les deux extensions explicites de signature en 0.17.2.

Le source installé est la référence de comportement : garde agent sans repli
en écriture, repli conservé en lecture, primary tolérant un `getMe` indisponible.
Le manifeste canonique conserve son nom générique et ses réglages sans données
d'instance. Aucun autre connecteur n'est à reprendre depuis les branches anciennes.

## État courant

- Worktree autorisé : `/home/jean/ssd_data/jean/RepoGit/_codex_wt/K-mcp-foss-connectors`.
- Branche : `codex/2026-10-17-kanboard-0.17.2`, base `f079642`.
- Alignement 0.17.1 committé en **`6e0efe9`** : source installé repris intégralement, classe
  `KanboardIdentityError` comprise ; `_identity_refusal` appelle directement
  cette classe au lieu du mécanisme conditionnel de la surcouche, sans différence
  de comportement. Suite complète : **1 285 réussis, 1 ignoré** (5,44 s).
- 0.17.2 réalisée : les seuls apports Kanboard de `959203a` sont reportés,
  avec décodage du cache de noms alimenté par la garde d'écriture. Aucun autre
  connecteur modifié. Le commit suivant `6e0efe9` porte ce report et ses preuves.
- État final validé : **1 352 tests réussis, 1 ignoré**, Ruff propre ; aucun
  appel réseau, aucune installation et aucun push. L'extension reste en 0.17.1.
- Référence installée lue uniquement :
  `~/.config/Claude/Claude Extensions/local.mcpb.inno.kanboard-inno3-v7/src/server.py`.
  SHA-256 : `50fa8b1f03855ff8efc91e681dfdb617fa2b81b73d7fec808fe3fc16d6a9a597`.
- `mcp-inno3` est une source secondaire en lecture seule. Son checkout courant
  n'est pas la référence : lire les objets Git de `main` (`f8f9a16` constaté),
  notamment `fa18893`, `1634a1e`, `90ffe35`, `24c72dc`, le début de
  `docs/REPRISE.md` et les contrats `docs/validation/kanboard-compat-20261014/`.
- Les anciens documents de branche décrivent des installations antérieures ;
  leurs assertions sur l'état du poste et leurs chemins de venv sont périmés.

## Vérifications exécutées

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
- Avant le report de `959203a`, ses tests donnent **63 échecs, 135 réussites**
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

## Diff fonctionnel final avec l'installé

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

## Fichiers de référence et suite du travail

- `tests/test_kanboard_agent_identity.py` : 37 cas de `mcp-inno3/main`, plus
  2 cas d'intégration du décodage des noms après écriture (39 au total).
- `tests/test_kanboard_compat_016.py` : 9 contrats historiques, désormais collectés.
- `tests/test_kanboard_weekend_regression.py` : protection publique issue de `03ca172`.
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

## Limites et branches à archiver après intégration

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
- `fix/kanboard-0.17.0-board-status-entites-dates` : correctifs de lecture `959203a`.

Les autres worktrees du dépôt sont des chantiers distincts. En particulier,
`/home/jean/ssd_data/jean/RepoGit/mcp-wt-builddoc` ne doit pas être touché.
