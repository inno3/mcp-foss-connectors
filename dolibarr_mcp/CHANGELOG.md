# Historique Dolibarr

## 1.8.2 - 2026-10-17

- Reprise de 3818b79 : budget global de lecture des temps (20 s par défaut,
  25 s maximum), pagination par tâche et reprise après erreur ou expiration.
- Les résultats déjà collectés restent disponibles, les agrégats annoncent
  leur périmètre et les réponses de temps invalides ne deviennent plus du vide.
- Curseurs liés aux filtres ; les anciens curseurs numériques sont refusés.

## 1.8.1 - 2026-10-17

- Conditions de règlement de la facture et du tiers dans les quatre lectures
  de factures, avec cache, lectures limitées à la page rendue et indisponibilités
  explicites. La fiche tiers expose aussi ses conditions.
- Signalement des échéances à facture + un jour, à confirmer avant une relance.
- Échéance commune aux listes, détails et projection de trésorerie ; un alias
  présent mais nul n'est pas remplacé par une autre date.
- Intégration de 08420a9, 05dfc5f et d689ac5 sur la base 1.8.0.
