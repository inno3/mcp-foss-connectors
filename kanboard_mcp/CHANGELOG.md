# Changelog Kanboard

## 0.17.3 (21/10/2026)

- La documentation de `modified_before` promettait de repérer les cartes sans
  « aucun signal ». Or Kanboard n'avance pas `date_modification` quand une
  carte est commentée, quand une sous-tâche ou un lien change, ni quand la carte
  est réordonnée dans sa colonne. Constaté sur l'instance le 29/09/2026 (cartes
  7970 et 8012) et confirmé dans la source de Kanboard. Une carte qui ne vit que
  par ses commentaires sortait dormante à tort.
- Les docstrings de `kanboard_list_tasks` et `kanboard_get_board`, le README et
  ce journal disent désormais ce qui fait avancer la date et ce qui ne la fait
  pas, et comment obtenir un critère qui compte les commentaires.
- Aucun changement de comportement : mêmes 53 outils, mêmes paramètres, mêmes
  données rendues.

## 0.17.2 — 17/10/2026

### Complément du lot K2

- `kanboard_who_am_i` conserve `identity_verified=false` lorsque `getMe`
  primary ne permet pas de vérifier le compte, même si l'annuaire résout son ID.
- Un login rendu par `getMe` différent du login configuré provoque un refus
  d'identité avant écriture, y compris pour primary et lors de sa sonde avant
  une écriture agent. Un `getMe` primary indisponible reste toléré ; la garde
  agent exige toujours une identité vérifiée et ne se replie jamais sur primary.
- Contrats des commentaires renforcés : `author_read_error` conserve la cause
  d'une relecture échouée ou indique un auteur absent/illisible ;
  `author_mismatch` accompagne l'auteur relu lorsqu'il diffère de l'identité
  vérifiée. La création réussie n'est pas rejouée et aucun auteur n'est inventé.
- Sans identité primary vérifiée, les créations omettent `creator_id` et
  `user_id` pour laisser Kanboard attribuer l'auteur. Aucun zéro ni ID issu du
  cache de résolution ou fourni à `kb_call` n'est envoyé comme auteur supposé.
  Un auteur relu à zéro ne devient pas une preuve d'identité. Pour une identité
  vérifiée, l'ID prouvé reste imposé sur les créations primary et agent.

### Lot K

- Tableau : filtre `status` facultatif, actif par défaut ; les comptes et la
  troncature suivent le périmètre filtré.
- Dates de création et de modification dans les tableaux et résumés ; filtre
  `modified_before` facultatif dans la liste des tâches.
- Entités HTML décodées une fois dans les textes lus et les noms mis en cache.
- Les 53 outils et tous leurs paramètres antérieurs sont conservés.

Les détails de lecture figurent dans le [README](README.md). La provenance et
les validations sont consignées dans [la reprise](../docs/REPRISE.md).
