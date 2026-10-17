# Reprise du cœur

## 17/10/2026 — lot L, carnets et échéances

Objectif : réunir les deux lignées sur `codex/2026-10-17-carnets`, partie de
`main` à `f079642`, sans perdre les comportements de Dolibarr 1.8.0.
Worktree autorisé : `_codex_wt/L-mcp-foss-connectors`.
L'extension associée est dans `_codex_wt/L-inno3-mcp-extensions`, sur la même
branche ; elle part de `e55c558` et doit préserver MeetingNotes 0.8.1.

Décisions et état courant :

- Dolibarr 1.8.1 réunit les conditions de règlement de `08420a9` et la lecture
  commune des échéances de `05dfc5f` / `d689ac5`. L'indicateur facture + un jour
  reste une heuristique à vérifier avant une relance.
- La reprise de base `0211d5e` est déjà dans `main` : AST de Dolibarr et de
  Nextcloud identiques après normalisation des seuls tirets typographiques.
- Le test de version MCP de `17e1d3e` est repris pour comparer au manifeste.
- À intégrer ensuite : temps bornés (`3818b79`) et diagnostic local Nextcloud
  (`0077f99`). Les documents historiques ne valent pas vérification actuelle.

Vérifications réellement exécutées, hors ligne :

- Départ : **1 220 réussis, 1 ignoré** (test d'intégration en écriture).
- Régression d'échéance issue de `d689ac5`, avant le helper commun :
  **1 échec, 1 réussite**, sur `date_echeance` ignoré par la projection.
- Première intégration ciblée : **489 réussis, 1 ignoré, 1 échec** ; le test MCP
  attendait encore la version source 1.9.0. Il compare désormais au manifeste.
- Après correction et ajout des cas de priorité des alias : **1 307 réussis,
  1 ignoré** sur toute la suite. Ruff ciblé et `git diff --check` propres.

Commande de référence, depuis la racine de ce worktree :

```sh
PYTHONPATH=. /home/jean/ssd_data/jean/RepoGit/_codex_wt/_venv-mcp/bin/python -m pytest tests -q -p no:cacheprovider
```

Références : `dolibarr_mcp/server.py`, `dolibarr_mcp/CHANGELOG.md`, son README
et son manifeste ; tests des conditions de règlement, échéances et exports.
Sources historiques consultées par Git uniquement : `fix/dolibarr-carnets-echeances`
(`08420a9`) et `codex/2026-10-10-dolibarr-nextcloud` (`17e1d3e`).
Leurs autres worktrees sont inchangés. Aucun service réel, module serveur ou
bundle installé n'a été interrogé ou modifié.

Prochaines actions : terminer les reprises fonctionnelles, exécuter les suites
complètes et Ruff, actualiser cette reprise et le rapport du lot, puis commiter.
Construction et recette réelle restent à arbitrer par Benjamin après revue ;
le rapport final doit fournir une procédure depuis `git archive`, non exécutée.
