# Historique Dolibarr

## 1.8.4 - 2026-10-17

- `dolibarr_update_thirdparty` accepte `cond_reglement_id`, entier strictement
  positif, validé avant tout appel REST y compris dans le schéma MCP.
- Avec ce paramètre, relecture du tiers après toute tentative d'écriture :
  état du PUT, possibilité d'écriture, valeur brute et condition résolue rendus
  séparément. Une erreur ou une valeur différente ne devient pas un succès.
  Aucun rejeu de PUT. Les autres paramètres conservent leur comportement.
- `dolibarr_get_thirdparty` conserve la lecture de la condition et du libellé,
  vérifiée par les tests de l'aller-retour.

## 1.8.3 - 2026-10-17

- Conserve les métadonnées d'export présentes dans main avant la reprise des
  temps : total exact ou minorant, troncature du périmètre, limite et décalage.
- Le curseur v2 transporte le nombre cumulé d'écritures rendues ; `offset`
  reste exploitable sans relire les tâches terminées. Repartir sans curseur
  après le prototype 1.8.2. Les agrégats partiels restent explicitement nommés.

## 1.8.2 - 2026-10-17

- Reprise de a62fd5a : budget global de lecture des temps (20 s par défaut,
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
- Intégration de 852659f, 073a2a6 et 35b643a sur la base 1.8.0.
