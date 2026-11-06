# Historique Dolibarr

## Non publié : 1.8.17

`dolibarr_list_time_entries` :

- Changement visible : `note` n'est plus rendue par défaut dans `entries`, et
  `omitted_fields` le signale. Tout lecteur de ce champ doit passer `fields`.
  `dolibarr_update_time_entry` relit toujours la note et ne l'efface pas.
- Ajout de `fields`, colonnes séparées par des virgules ou `all`. Une colonne
  inconnue est refusée avant tout appel. Les totaux et la pagination n'en dépendent
  pas, et les curseurs déjà émis restent valides.
- Ajout de `aggregate` : `user`, `project`, `month` ou `year`, qui ne rend que les
  totaux en heures et en secondes. `limit` y est sans objet et `fields` refusé.
- Un parcours incomplet est annoncé par `truncated` et `aggregates_complete`.
  `next_cursor` porte le cumul, la dernière réponse donne donc le total complet.
- Limite : le périmètre reste une tâche ou un projet, `aggregate="project"` rend une
  seule ligne.
- Test adapté : `test_the_entry_id_needed_to_correct_a_line_is_exposed` demande
  désormais `fields="all"` pour lire la note. Aucun test supprimé.

## Non publié : 1.8.16

Branche `lot/2026-11-06-dolibarr-actions-balayage`, partie de `main` en 1.8.15.

Défaut constaté en réel le 06/11/2026, en 1.8.15, en lecture seule :
`dolibarr_list_actions_open` sans filtre rendait « Request timed out ». Le balayage
continuait après l'abandon du client (deux `ConnectTimeout` vers l'instance dans le
journal), et un appel limité à deux mois échouait dans la foulée. Avec un projet ou
un mois, l'appel répondait en 4 secondes.

- Cause : `percent<>100` gardait les événements sans objet à -1, plus de 8 600
  depuis 2022, que la route relit un par un, pour les compter puis les écarter
  localement.
- Par défaut, l'API filtre `t.percent` de 0 à 99. `excluded_system_exact` vaut
  donc toujours `False` hors `include_system=True`, et la réponse porte
  `excluded_system_note`. `include_system=True` garde l'ancien périmètre.
- Ajout additif de `time_budget` (20 s par défaut, 25 au plus) et des clés
  `time_budget` et `elapsed_seconds`. `_scan_list` accepte une échéance : la page en
  cours est annulée, aucune autre n'est demandée, et le résultat est rendu
  incomplet. Les autres balayages n'ont pas d'échéance.
- Cinq assertions des tests existants changent avec le contrat : le compteur exact
  par défaut et le filtre `<>100`. Le test de version du lot 1.8.15 vérifie
  désormais un minimum.

## Non publié : 1.8.15

Branche `lot/2026-11-05-dolibarr-taches-masquees`, partie de la 1.8.13 : la 1.8.14
(colonnes et agrégats de `dolibarr_list_time_entries`) est menée dans un autre lot et
n'est pas comprise ici.

Défaut constaté en réel le 05/11/2026, en 1.8.13, en lecture seule, sur un projet de
production : `dolibarr_list_tasks` rendait `total=0` et `total_exact=true`,
`dolibarr_list_time_entries` `tasks_total=0`, `aggregates_complete=true` et
`reason=complete`, alors qu'une tâche du projet portait une heure, lisible par `task_id`.

- Cause, lue dans le cœur 24.0.1 : `projects/{id}/tasks` écarte toutes les tâches d'un
  projet non public dont le compte de la clef n'est pas contact, administrateur et
  « projet all lire » compris.
- Si cette route ne rend rien, `GET tasks?sqlfilters=(t.fk_projet:=:ID)` est balayé et
  chaque ligne revérifiée par `fk_project`. Un projet visible coûte toujours une seule
  requête.
- Un total n'est plus certifié quand un masquage reste possible : `GET tasks` en échec,
  balayage incomplet, ou projet vide des deux côtés sans que `users/info` confirme le
  droit « societe client voir », faute duquel `GET tasks` écarte les projets des tiers
  dont le compte n'est pas commercial. Ce droit est lu une fois par processus.
- Ajout additif de `task_discovery` (nombre par route, `exact`, erreurs) et de
  `tasks_warning`. `dolibarr_list_time_entries` : `tasks_total=null`,
  `aggregates_complete=false` et `reason="tasks_unverified"` dans ce cas.
- `dolibarr_delete_project` : `tasks_remaining` du diagnostic compte les tâches
  masquées, `null` s'il n'est pas certifié, avec `tasks_warning`.
- `dolibarr_get_project` : la docstring promettait les tâches et les contacts, que
  l'outil n'a jamais rendus. Elle renvoie vers `dolibarr_list_tasks`.
- `assigned_to` reste `null` sur une tâche retrouvée, son créateur n'étant pas la
  personne affectée. Le filtre `assigned_to` ne retenait déjà rien en production ;
  c'est documenté, non corrigé.
- Aucun paramètre ni aucune clé retirés, aucun test existant modifié.

## Non publié : 1.8.13

Défauts constatés en réel le 03/11/2026 sur un projet de test, en 1.8.12 :

- `opp_status` : les valeurs documentées (`prospect`, `won`...) partaient telles quelles,
  et `Project::update()` met NULL sans erreur tout statut non numérique. Une mise à jour
  effaçait donc le statut en place en annonçant un succès. Codes et alias sont désormais
  traduits vers l'identifiant du dictionnaire, toute autre valeur est refusée avant
  écriture, y compris `0` et `PROSPEC`, sans créer de projet.
- `opp_status` envoie aussi `opp_percent`, sans quoi le montant pondéré reste à zéro, et
  `usage_opportunity=1`, sans quoi la carte du projet masque l'opportunité (lu dans le
  cœur 24.0.1). Le pourcentage reprend celui du dictionnaire, comme le tableau de bord
  maison. Un `opp_amount` seul n'active pas le suivi.
- Changement visible : un code qui ne faisait rien, sinon effacer, agit maintenant. Passer
  un projet à `won` déclenche ce que l'instance attache à ce statut, comme depuis la carte.
- `dolibarr_update_project` : `status` n'avait aucun effet, succès annoncé. Le connecteur
  envoyait `fk_statut`, alors que `Project::update()` écrit la propriété `status`. Il
  agit désormais, valeurs 0, 1 et 2 seulement. `PROJECT_MODIFY` part, mais ni
  `PROJECT_CLOSE` ni date de clôture : l'API n'a pas de route pour cela.
- Ajout additif de `update_warning`, et `enrich_warning` à la création : `status` ou
  `opp_status` que la réponse de Dolibarr ne confirme pas.
- `enrich_warning` donne la cause complète, plus un message vide sur un délai dépassé.
- `dolibarr_list_projects` : `status="0,1,2"` rendait `total=0` annoncé comme exact, alors
  que les projets existaient. Une valeur hors `0`, `1`, `2` et `""` est refusée.
- `dolibarr_get_project` : ajout additif de `opp_percent` et `usage_opportunity`, bruts.
- `dolibarr_delete_project` : retrait du diagnostic « renommage cassé », cause qui n'existe
  pas, `Project::setValid()` ne renommant rien.

## Non publié : 1.8.12

- `dolibarr_create_project` : `success`, `status=1` et `ref="PROV"` sortaient sans
  avertissement (projet 316, 02/11/2026). La validation était bien appelée, mais
  `Project::setValid()` ne renumérote pas, et l'index unique `uk_projet_ref` refusait
  la création suivante tant qu'un projet portait `PROV`. Le connecteur envoie
  désormais `ref="auto"` à la création, comme le correctif jamais repris dans ce dépôt
  (`ee2a12f` de `mcp-inno3`, 23/06/2026). Le chemin en trois étapes reste, pour le
  trigger `PROJECT_VALIDATE`.
- Changement visible : un brouillon reçoit son numéro dès la création au lieu de
  `PROV`, et `ref` vaut `null`, plus jamais `PROV` par défaut, si la relecture ne la
  confirme pas. Le projet est relu aussi après un brouillon ou une validation en échec.
- Ajout additif de `validate_warning` (`status=1`) et `refetch_warning` (`status=0`) :
  référence relue à `PROV` ou `auto`, relecture impossible ou sans référence, statut
  relu inattendu. La relecture en échec était avalée sans rien dire.
- `validation_warning` signale toujours la validation en échec ; son texte ne prétend
  plus que le projet est en brouillon en `PROV` et donne la cause complète.
- `dolibarr_get_project` : ajout additif de `opp_status` et `opp_amount`, bruts.

## Non publié : 1.8.11

- Actions ouvertes : exclusion par défaut des événements `systemauto` et des
  progressions sans objet à -1, option `include_system` pour les rétablir.
- Période facultative `date_from` / `date_to`, validée avant toute requête et
  appliquée par l'API à la date prévue, bornes journalières incluses.
- `excluded_system` est calculé après assignation et avant pagination ;
  `excluded_system_exact` distingue un décompte complet d'un minimum observé.
- Les co-assignés, le repli sur auteur, le tri et les métadonnées d'export
  existants sont conservés. Aucun autre outil d'agenda modifié.
- Numéro distinct : deux lignées portaient 1.8.10 le 02/11/2026, seule celle de la
  section suivante a été installée.

## Non publié : 1.8.10

- Aucun changement de comportement du cœur : ce numéro désigne le bundle qui embarque
  une extension corrigée, pour qu'une seule version soit à vérifier une fois installé.
- Régression réelle en lecture seule : `dolibarr_list_actions_open` reste trié par date
  prévue croissante d'une page à l'autre, sans doublon.

## Non publié : 1.8.9

- `dolibarr_list_actions_open` ne répondait plus que par un HTTP 503 (« Unknown
  column 't.rowid' in 'order clause' ») : `_scan_list` ajoutait `t.rowid` à tout
  tri, or `llx_actioncomm` a pour clé primaire `id`. Le départage suit désormais
  la clé de la route (`EXPORT_SCAN_PRIMARY_KEYS`), soit `t.datep,t.id` ici. Les
  autres routes balayées gardent `rowid`.
- Même outil, défaut que le 503 masquait : les lignes de la route portent les
  propriétés d'`ActionComm` (`userownerid`, `socid`, `percentage`, `datef`), pas
  les colonnes SQL lues jusqu'ici. `assigned_to` aurait écarté toutes les actions
  sans le dire, et `percent`, `date_end`, `thirdparty_id` et `assigned_to` seraient
  sortis vides. Les deux formes se lisent. Le filtre retient le porteur et les
  co-assignés, puis l'auteur pour une action sans assigné.
- Ajout additif : `assigned_users`, liste des identifiants assignés.

## Non publié : 1.8.8

- `dolibarr_list_time_entries` : `date` est désormais le jour GMT de
  `datehour`, et le filtre date_start / date_end porte sur ce jour-là. Les
  deux champs viennent du même horodatage, mais `datehour` était formaté en
  GMT et `date` dans le fuseau local du poste du connecteur. Vu de Paris, une
  écriture passée à partir de 22:00 GMT (minuit à Paris l'été) se relisait au
  lendemain, et le filtre sur le jour écrit ne la retrouvait pas (constaté le
  25/09/2026, écriture 3936 : « 2026-09-25 23:59 » relue au 26). À l'ouest de
  l'UTC, la date reculait d'un jour pour toute écriture sans heure.
- Aucune donnée de production n'était touchée au 21/10/2026 : les 102
  écritures du projet Ledger sont toutes horodatées avant 22:00 GMT. Le défaut
  était muet, pas encore coûteux.
- Les autres dates du connecteur (factures, projets, tickets) passent
  toujours par `_ts_to_date`, en heure locale du poste : sans effet depuis
  Paris pour une date à minuit GMT, mais un jour trop tôt à l'ouest de l'UTC.
  Changement de sens pour les horodatages de création, laissé à une décision
  distincte.
- 7 tests, fuseau du poste fixé (Paris, New York, UTC) : sans cela la suite
  passe sur une machine en UTC, que le code soit juste ou faux. Suite
  complète verte dans les trois fuseaux.

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
