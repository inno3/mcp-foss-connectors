# Reprise du cœur

## 17/10/2026 — lot L terminé, carnets et échéances

### Objectif, branche et état vérifié

Réunir les lignées Claude et Codex du lot A sans remplacer la base actuelle.
Worktree : `/home/jean/ssd_data/jean/RepoGit/_codex_wt/L-mcp-foss-connectors`.
Branche : `codex/2026-10-17-carnets`, partie de `main` à `f079642`.
Dernier commit fonctionnel : `faa6f29`. Dolibarr **1.8.3**, Nextcloud **1.4.1**,
paquet commun inchangé **0.2.0**. Les commits documentaires suivants ne changent
pas les sources testées ; reprendre depuis le HEAD de la branche du lot.

Extension associée : `../L-inno3-mcp-extensions`, même branche, base `e55c558`,
version **0.8.4**, dernier commit fonctionnel `aca0bcc`. Son `docs/REPRISE.md`
détaille le contrôle des carnets et la préservation de MeetingNotes 0.8.1.
Les versions installées 1.8.0 et 0.8.1 sont des données de la mission,
pas des mesures de cette session : aucune installation inspectée ou modifiée.

### Décisions et commits

| Commit | Version | Contenu |
| --- | --- | --- |
| `f607c5a` | Dolibarr 1.8.1 | Conditions de règlement de `08420a9`, échéance commune de `05dfc5f`, tests de `d689ac5`, contrôle de version MCP de `17e1d3e` |
| `8f98923` | Dolibarr 1.8.2 | Temps bornés et paginés de `3818b79`, résultats conservés après erreur ou expiration |
| `daadab9` | Nextcloud 1.4.1 | Racines d'envoi résolue et configurée de `0077f99`, protections de main conservées |
| `faa6f29` | Dolibarr 1.8.3 | Métadonnées d'export de main conservées dans les temps, curseur v2 avec décalage cumulé |

La reprise de base `0211d5e` est déjà dans `main` : AST de Dolibarr et Nextcloud
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
(`08420a9`, worktree historique `mcp-wt-carnets/mcp-foss-connectors`) et
`codex/2026-10-10-dolibarr-nextcloud` (`17e1d3e`, `_codex_wt/a-mcp-foss-connectors`).
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
