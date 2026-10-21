# Historique Dolibarr

## Non publié : 1.8.7

- Intégration du lot builddoc sur main 1.8.6, sans perdre les contrats des
  conditions de règlement des tiers, factures et devis.
- `dolibarr_list_documents` distingue un modulepart non implémenté d'un objet
  absent : `unsupported_modulepart=true` pour cette erreur du cœur Dolibarr.
- Les comptes rendus utilisent `dolibarr_list_meetingnote_documents`, fourni
  par l'extension inno³ 0.8.8 avec MeetingNotes 4.45.0-alpha.77.
- Numéro réservé le 21/10/2026 pour l'intégration ; aucun outil cœur ajouté
  (78 outils), aucune installation ni publication.

## 1.8.6 - 2026-10-19

- `dolibarr_create_thirdparty` accepte `cond_reglement_id`, entier strictement
  positif facultatif, prioritaire sur `DOLIBARR_THIRDPARTY_PAYMENT_TERM`.
  Ce réglage d'installation est lu à chaque appel : code du dictionnaire
  insensible à la casse ou identifiant positif, vide par défaut.
- La condition est envoyée à la création des clients et prospects uniquement.
  Un code inconnu ou un dictionnaire illisible laisse créer sans condition et
  rend l'erreur ; un identifiant numérique peut être envoyé sans dictionnaire.
  Le helper de création et le cache des conditions sont réutilisés.
- `payment_terms_applied` rend la condition, sa provenance et sa vérification
  par relecture après le POST. Une relecture impossible (`verified=null`) ou
  différente (`verified=false`) est signalée sans faire échouer la création.
  Aucun POST n'est rejoué. Sans condition envoyée, aucune relecture du tiers.
- L'anti-doublon précède le dictionnaire. Sans paramètre ni réglage, les appels
  et le contenu du POST restent inchangés ; un fournisseur seul reçoit
  `source=non_applicable`. Aucun champ `user_config` ajouté au manifeste :
  le réglage est documenté dans le README et la docstring.

## 1.8.5 - 2026-10-19

- Les factures et devis créés reprennent la condition de règlement du tiers.
  Une facture issue d'un devis reprend d'abord celle du devis, puis celle du tiers.
  `cond_reglement_id`, entier strictement positif facultatif, permet de la forcer.
- `payment_terms_applied` rend la condition envoyée, son code, son libellé,
  sa provenance et les erreurs de lecture. Un tiers inaccessible ne bloque pas
  la création ; aucune condition n'est alors envoyée. Le dictionnaire est mémorisé.
- `due_date` explicite reste prioritaire dans les deux créations de facture.
  L'indicateur d'échéance à +1 jour rappelle la condition « À réception »
  (`RECEP`, `nbjour = 1`) et ne présume plus une absence de condition.

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
