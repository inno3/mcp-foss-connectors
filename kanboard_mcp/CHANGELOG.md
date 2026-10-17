# Changelog Kanboard

## 0.17.2 — 17/10/2026

### Complément du lot K2

- `kanboard_who_am_i` conserve `identity_verified=false` lorsque `getMe`
  primary ne permet pas de vérifier le compte, même si l'annuaire résout son ID.
- Un login rendu par `getMe` différent du login configuré provoque un refus
  d'identité avant écriture, y compris pour primary et lors de sa sonde avant
  une écriture agent. Un `getMe` primary indisponible reste toléré ; la garde
  agent exige toujours une identité vérifiée et ne se replie jamais sur primary.

### Lot K

- Tableau : filtre `status` facultatif, actif par défaut ; les comptes et la
  troncature suivent le périmètre filtré.
- Dates de création et de modification dans les tableaux et résumés ; filtre
  `modified_before` facultatif dans la liste des tâches.
- Entités HTML décodées une fois dans les textes lus et les noms mis en cache.
- Les 53 outils et tous leurs paramètres antérieurs sont conservés.

Les détails de lecture figurent dans le [README](README.md). La provenance et
les validations sont consignées dans [la reprise](../docs/REPRISE.md).
