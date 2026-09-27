# Reprise du connecteur Dolibarr

État au 28/09/2026. Source commune de `AGENTS.md` et `CLAUDE.md`.

## Objectif et état vérifié

CX-38, K-12, K-28 et K-05 : exports complets, champs financiers fidèles,
calculs documentés et refus de droits explicites. Travail hors ligne dans
`/home/jean/ssd_data/jean/RepoGit/mcp-wt/mcp-foss-connectors`, branche
`fix/dolibarr-exports-complets`, base `c1a1995` (manifeste 1.7.6).
Le dépôt d'origine porte un autre chantier et n'a pas été modifié.

Le paquet commun est en 0.2.0, le manifeste et le serveur Dolibarr en 1.8.0.
Commits de référence : `cc5125b` pour le contrat des listes, `9140991` pour
les champs financiers, `706a06f` pour les refus de droits. Les compléments
financiers et de contrôle protocolaire suivent ces commits sur la branche.

Le contrat des 20 listes figure dans `dolibarr_mcp/README.md`. Les montants
absents, null et zéro sont distincts. Les listes et détails de factures
lisent les paiements des objets rendus, désactivables par `include_payments`.
Le cœur conserve sa définition générique du CA et la rend dans la réponse.
Les parcours financiers incomplets ne certifient aucun total. La synthèse
bancaire annonce aussi les liens illisibles. Aucun choix métier propre à une
organisation n'a été ajouté au cœur.

## Vérifications

- Référence : 1146 passés, 1 ignoré en 2,43 s, ruff propre.
- État final : 1220 passés, 1 ignoré en 2,12 s, ruff propre.
- Pagination : 13 rouges avant correction, union de 250 objets vérifiée sur
  trois pages, chevauchements dédoublonnés et pages répétées signalées.
- Finance : six rouges avant correction, puis trois cas complémentaires
  pour les montants absents et les liens bancaires illisibles.
- Intervenants : une affectation répétée est dédoublonnée par lien, sans
  fusionner deux rôles du même utilisateur.
- Refus de droits : 46 outils d'écriture plus trois verbes REST, 49 cas verts.
- Protocole MCP en mémoire : initialize et tools/list, version 1.8.0,
  78 outils uniques, schéma cursor présent, stderr capturé vide.
- Stdio : trois expirations dans le bac à sable, cause non établie. Arrêt
  des tentatives conformément aux consignes. Le bundle reste à vérifier.

Commandes depuis ce worktree : `../venv-core/bin/pytest -q` et
`../venv-core/bin/ruff check .`. L'environnement avec extensions enregistre
un observateur et n'est pas celui de cette suite.

## Références, autres répertoires et limites

- Code : `dolibarr_mcp/server.py`, tests `tests/test_dolibarr_exports.py`
  et `tests/test_dolibarr_permissions.py`.
- Packaging : `docs/PACKAGING.md`, `scripts/build_mcpb.py`.
- Contrôle hors ligne : `scripts/check_mcp_stdio.py`. Aucun outil Dolibarr
  appelé, seules les méthodes de protocole initialize et tools/list.
- Worktree frère `../inno3-mcp-extensions`, même branche, base `bee8080`,
  version 0.8.0. Sa reprise et `docs/RECETTE_LOT12.md` portent la variante
  métier, la procédure de construction depuis archives Git et le retour arrière.
- Les totaux REST ne sont pas des compteurs natifs indépendants.
- Les curseurs nécessitent des données stables entre appels.
- Les ajouts du lot passent les contrôles typographiques. L'intervalle
  `main..HEAD` contient un commit et des ajouts hérités avec des caractères
  interdits par les consignes actuelles. La base imposée n'a pas été réécrite.
- Aucun service contacté, aucun bundle construit ou installé, aucun push.

## Prochaines actions ordonnées

1. Construire hors bac à sable depuis les deux états commités.
2. Vérifier le transport stdio du bundle, 110 outils avec l'extension,
   stderr muet, puis effectuer la recette sur le compte technique.
3. Confirmer le périmètre d'entités et la définition du CA dans les usages.
4. Installer localement avec sauvegarde, sur décision de l'utilisateur.
