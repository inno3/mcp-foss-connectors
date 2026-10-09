## 1.8.2 - 2026-10-09

Pagination des temps par tache, budget global de 20 s (plafond 25 s),
curseur lie au perimetre et conservation des resultats partiels.
Les totaux portent explicitement sur les taches lues ; ils ne se somment
pas entre pages qui relisent une tache partiellement rendue.
Les anciens curseurs numeriques sont refuses. Aucun deploiement.

# Journal Dolibarr du lot A

## 1.8.1 - 2026-10-09

La liste fournisseur et la projection utilisent la meme lecture d'echeance,
avec priorite a date_echeance. Les trois references de regression concordent
en production apres lecture seule. Test comparatif rouge avant, vert apres.
Aucune installation.
