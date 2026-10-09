# Lot C : identité Kanboard

Mise à jour : 09/10/2026. Travail local terminé, sans installation ni recette réelle.

## Objectif et état vérifié

Interdire une écriture agent sous le compte primary, vérifier getMe une fois par
processus et relire l'auteur des créations. Le correctif était déjà fait au
commit `ed72c6e` et installé en 0.16.0. Le serveur installé est identique octet
pour octet à ce commit (SHA-256 83238a5bbe0ef1effe154f06928c21d11c6f5c1798c137f097f0e21c1e13f663).

Le dépôt canonique est `/home/jean/ssd_data/jean/RepoGit/inno3/mcp-foss-connectors`.
Il est resté sur `fix/kanboard-0.17.0-board-status-entites-dates`, sans changement.
Le présent worktree est `/home/jean/ssd_data/jean/RepoGit/_codex_wt/C-mcp-foss-connectors`,
branche `codex/2026-10-10-kanboard-identite`, créée depuis `main` (`0d454de`).
Seuls les six fichiers Kanboard de `ed72c6e` ont été repris : le transport 0.15.0
est son prérequis. Les autres connecteurs, la 0.17.0 et le lot A sont exclus.
Le commit de livraison se lit par `git log -1` sur cette branche.

## Décisions et différences

Aucune réécriture du correctif existant. Six sondes supplémentaires exercent le
contrat public, même sur le serveur 0.15.0 : compte agent incomplet, token primary,
getMe muet, ordre des appels/cache et auteur enregistré différent.
La ponctuation U+2013/U+2014 des fichiers repris est remplacée par le tiret ASCII.
Aucune autre différence avec le code installé. Les champs historiques restent
`creator_id` pour une tâche et `author_user_id_recorded` pour un commentaire.

## Tests et paquet

- Avant : **6 échecs** sur le serveur de `7a0c7f2` (0.15.0), dans une copie temporaire.
- Après : **212 tests Kanboard passent**, dont les 23 tests d'identité existants.
- `scripts/test_kanboard_offline.py` bloque sockets, DNS et UDP ; zéro tentative.
- Ruff sur le serveur et les ajouts : propre. `git diff --check` : propre.
- Paquet : `archives/kanboard-inno3-v7-0.16.0.mcpb`, construit localement.
  SHA-256 `57e239280b4ef67f2fb58904726fdfc1f851b405d706fba7ea8f85c6ffe735c2`.
- Import du paquet sous Python système 3.14.4 : 53 outils conformes au manifeste,
  mcp 1.29.1, 29 dépendances avec METADATA. Le source empaqueté est celui du worktree.
- Le manifeste installé, son identité et user_config sont conservés. Seuls le
  source et la sérialisation du manifeste changent. Aucune dépendance installée.
- Journaux et empreintes : `docs/validation/`. Constructeur sans écrasement :
  `../C-mcp-inno3/scripts/build_weekend_mcpb.py` (usage en tête du script).

Rejeu sans installer :

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=/home/jean/ssd_data/jean/RepoGit/mcp-inno3/.venv/lib/python3.12/site-packages /home/jean/.local/share/uv/python/cpython-3.12.14-linux-x86_64-gnu/bin/python3   -B scripts/test_kanboard_offline.py
```

Pour le rouge, copier `tests/test_kanboard_weekend_regression.py`, le lanceur,
`pyproject.toml` et `7a0c7f2:kanboard_mcp/{server.py,__init__.py}` dans un nouveau
répertoire temporaire puis y lancer la même commande. Ne pas modifier un checkout.

## Limites et en attente de Benjamin

Versions présentes avant/après ce lot : **0.16.0 / 0.16.0**. La version chargée
par le processus Claude Desktop n'est pas vérifiée. Aucun who_am_i réel exécuté.
Les dépendances natives du paquet visent le poste Linux/Python 3.14 actuel.

1. Relire `git diff 0d454de..HEAD -- kanboard_mcp tests scripts docs`.
2. Décider d'une installation ou d'un redémarrage, si utile puisque 0.16.0 est déjà là.
3. Faire la recette who_am_i agent, puis désigner la carte de test du projet 262
   pour le commentaire témoin. Aucune création, suppression ou clôture faite ici.
4. Garder la 0.17.0 sur sa branche distincte, à examiner séparément.

Contrôle final des paquets : descriptions du manifeste normalisées en tirets ASCII,
configurations inchangées. Cette ponctuation est la seule différence additionnelle
au source et à la version. Les nouvelles empreintes ci-dessus en tiennent compte.
