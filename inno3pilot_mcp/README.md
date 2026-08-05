# inno3pilot MCP

<!-- SPDX-License-Identifier: Apache-2.0 -->

Connecteur MCP pour le module Dolibarr **inno3pilot** (boards Kanban
polymorphes) : boards, colonnes, cartes résolues par type (tâches, tickets,
comptes rendus), déplacement de carte avec synchronisation du statut natif.

## Configuration

Mêmes variables que le connecteur `dolibarr_mcp` :

| Variable | Rôle |
|---|---|
| `DOLIBARR_URL` | URL de base de l'instance (sans slash final) |
| `DOLIBARR_API_KEY` | Clé API (header `DOLAPIKEY`) d'un utilisateur avec droits inno3pilot |

## Tools (6)

- `inno3pilot_list_boards(fk_projet=0)` — boards (tous ou par projet)
- `inno3pilot_get_board(board_id)` — colonnes + types activés
- `inno3pilot_get_cards(board_id, limit=50)` — cartes résolues (réf, titre, projet, assigné, statut, échéance, priorité)
- `inno3pilot_move_card(card_id, fk_column, position=0)` — ⚠ synchronise le statut natif (Terminé → 100 %/résolu)
- `inno3pilot_add_card(board_id, elementtype, fk_element, fk_column=0)`
- `inno3pilot_add_column(board_id, code, label, position=0, wip_limit=0)`

## Hors périmètre

Les commentaires de cartes, le partage client et les filtres sauvegardés
passent par des endpoints de session (AJAX), pas par l'API REST — non couverts.

## Build

```bash
python scripts/build_mcpb.py inno3pilot --output-dir dist
```
