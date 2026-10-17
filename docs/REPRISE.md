# 17/10/2026 — lot K : réunir Kanboard en 0.17.2

## Objectif et décisions

Reprendre dans le dépôt canonique le comportement de la 0.17.1 installée, puis
les apports Kanboard de `959203a` : statut du tableau, entités HTML et dates.
Conserver les 53 outils, les paramètres existants et les champs historiques
d'auteur. Les paramètres facultatifs `status` du tableau et `modified_before`
de la liste seront les deux extensions explicites de signature en 0.17.2.

Le source installé est la référence de comportement : garde agent sans repli
en écriture, repli conservé en lecture, primary tolérant un `getMe` indisponible.
Le manifeste canonique conserve son nom générique et ses réglages sans données
d'instance. Aucun autre connecteur n'est à reprendre depuis les branches anciennes.

## État courant

- Worktree autorisé : `/home/jean/ssd_data/jean/RepoGit/_codex_wt/K-mcp-foss-connectors`.
- Branche : `codex/2026-10-17-kanboard-0.17.2`, base `f079642`.
- Alignement 0.17.1 réalisé : source installé repris intégralement, classe
  `KanboardIdentityError` comprise ; `_identity_refusal` appelle directement
  cette classe au lieu du mécanisme conditionnel de la surcouche, sans différence
  de comportement. Suite complète : **1 285 réussis, 1 ignoré** (5,44 s).
  Correctif 0.17.2 encore à reporter.
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

## Fichiers de référence et suite du travail

- `tests/test_kanboard_agent_identity.py` : 37 cas de `mcp-inno3/main`.
- `tests/test_kanboard_compat_016.py` : 9 contrats historiques, désormais collectés.
- `tests/test_kanboard_weekend_regression.py` : protection publique issue de `03ca172`.
- `tests/test_kanboard_verified_write.py` : 12 cas de relecture de création et
  des helpers présents dans l'installé. Les 5 cas supplémentaires de `mcp-inno3`
  exigeant une relecture après déplacement/fermeture/réouverture échouent sur
  l'installé (champ `verified` absent) : comportement hors alignement, non porté.
  Ce constat ne concerne pas la suite initiale du dépôt, verte.
- `tests/fixtures/kanboard_0171_signatures.json` : signatures extraites de l'installé.
- `scripts/test_kanboard_crosscheck_offline.py` : lanceur avec garde réseau.
- Rapport final prévu :
  `/home/jean/ssd_data/jean/RepoGit/_codex_wt/_rapports-2026-10-17/K.rapport.md`.

Prochaines actions : finir l'alignement et ses tests ; reporter les seuls apports
Kanboard de `959203a` ; vérifier la suite complète, Ruff et le diff fonctionnel
avec l'installé ; committer et compléter ce document et le rapport.

## Limites et branches à archiver après intégration

Aucune recette sur service réel ni installation ne sera exécutée dans ce lot.
La publication et l'installation de la 0.17.2 restent à arbitrer par Benjamin.
Claude Code se chargera ensuite de l'archivage de ces branches ; elles ne sont
pas supprimées ici :

- `fix/kanboard-identite-agent` : garde d'identité 0.16.
- `codex/2026-10-10-kanboard-identite` : contre-vérification de cette garde.
- `fix/kanboard-0.17.0-board-status-entites-dates` : correctifs de lecture `959203a`.

Les autres worktrees du dépôt sont des chantiers distincts. En particulier,
`/home/jean/ssd_data/jean/RepoGit/mcp-wt-builddoc` ne doit pas être touché.
