# Reprise du connecteur Dolibarr

État au 28/09/2026. Source commune de `AGENTS.md` et `CLAUDE.md`.

## Objectif et état

CX-38, K-12, K-28 et K-05 : exports complets, champs financiers fidèles,
calculs documentés et refus de droits explicites. Travail hors ligne dans
`/home/jean/ssd_data/jean/RepoGit/mcp-wt/mcp-foss-connectors`, branche
`fix/dolibarr-exports-complets`, base `c1a1995` (manifeste 1.7.6).
Ne pas modifier le dépôt d'origine, qui porte un autre chantier.

Le contrat de pagination 1.8.0 est implémenté et documenté dans
`dolibarr_mcp/README.md`. Les tests utilisent uniquement des données fictives.
Le paquet commun passe de 0.1.0 à 0.2.0, le manifeste Dolibarr à 1.8.0.
Les étapes financières et les vérifications finales restent en cours.

## Vérifications

Référence réelle : 1146 passés, 1 ignoré en 2,43 s, ruff propre.
Reproduction pagination : 13 échecs, 1 passant avant correction, puis 14 passants.
Deux tests historiques du tri ont été adaptés au départage par `t.rowid`.

Commandes depuis le worktree : `../venv-core/bin/pytest -q` et
`../venv-core/bin/ruff check .`. Le venv avec extensions enregistre un
observateur et n'est pas le bon environnement pour cette suite.

## Références et limites

- Code : `dolibarr_mcp/server.py`, tests `tests/test_dolibarr_exports.py`.
- Packaging : `docs/PACKAGING.md`, `scripts/build_mcpb.py`.
- Extension dans le worktree frère `../inno3-mcp-extensions`, même branche.
  Sa reprise décrit les décisions métier propres à cette extension.
- Les totaux REST ne sont pas des compteurs natifs séparés.
- Les curseurs nécessitent des données stables entre appels.
- Aucun service contacté, aucun bundle construit ou installé, aucun push.

## Prochaines actions

1. Compléter les champs financiers et vérifier absence, null et zéro.
2. Finaliser les tests et la documentation des refus de droits.
3. Relancer toutes les vérifications puis construire hors bac à sable depuis
   les deux états commités et vérifier la poignée de main MCP.
