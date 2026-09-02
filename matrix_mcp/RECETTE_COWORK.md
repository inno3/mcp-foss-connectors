# Recette Cowork — connecteur Matrix 1.4.0

**Cible : matrix-inno3 v1.4.0, 22 outils.** Version issue de quatre passages de
recette : elle ferme le contexte amputé, rend les compteurs de non-lus
auditables et fiabilise la relecture après marquage.

## Avant de lancer

Un **salon de test**, ou un salon à faible trafic dont vous êtes membre. Les
phases C et D y écrivent ; tout est supprimé en phase F. Ne pas viser un salon
client.

Prévoyez un petit **PNG** et un petit **PDF** locaux pour la phase C.

## Prompt à copier

```
Tu recettes le connecteur MCP Matrix 1.4.0 contre l'instance de production.
Après chaque phase, un tableau : contrôle | attendu | constaté (OK / ÉCART).
En cas d'ÉCART, montre la réponse brute.

Salon de test : <ROOM_ID à remplir>
PNG local : <chemin>   PDF local : <chemin>

ÉTAPE 0 — Contrôle de version, AVANT tout le reste
Vérifie que les outils `matrix_list_files`, `matrix_list_unread` et
`matrix_mark_read` existent, PUIS qu'un `matrix_get_event_context` rend bien un
champ `complete` et que `matrix_list_unread` rend `counters_note`. Si l'un
manque, la 1.4.0 n'est pas chargée : ARRÊTE-TOI et dis-le. Les quatre passages
précédents ont produit des « écarts » qui n'étaient que des versions
antérieures — ne recommence pas.

Règles : aucune écriture hors du salon de test. Tu nettoies en phase F.

PHASE A — Recherche
A1. matrix_search_messages(query=<un mot fréquent>)
    Attendu : index_matches renseigné, index_matches_note, next_batch,
    has_more=true, et sur chaque résultat room_name ET permalink.
A2. Reprends le next_batch : résultats DIFFÉRENTS de A1, event_id disjoints.
A3. limit=50, body_chars=40
    Attendu : 50 résultats dans une réponse COURTE (quelques dizaines de
    milliers de caractères). Sans plafond elle sortait à 872 000 caractères.
    Les corps coupés portent body_truncated et body_full_length.
A4. context_size=3 : chaque résultat porte context_before et context_after.
A5. sender=<votre @user:serveur>
    Attendu : uniquement vos messages, ET index_matches_reliable = FALSE.
    Le total d'index ne reflète pas le filtre d'expéditeur — il annonçait 109
    pour 2 résultats. Le drapeau évite qu'un appelant compare les deux.
A6. exclude_rooms=<un ou deux salons de notification>
    Attendu : index_matches PLUS BAS que sans exclusion (les filtres de salon,
    eux, passent côté serveur : 2613 → 2050 mesuré), et
    index_matches_reliable = TRUE.
A7. since / until sur une fenêtre de quelques jours
    Attendu : uniquement des résultats de la fenêtre, un filtered_out, un
    date_filter_note, et index_matches_reliable = FALSE.
A8. query="zzz-chaine-improbable-xyz" : count=0, has_more=false.

PHASE B — Chronologie d'un salon
B1. matrix_get_room_messages(room_id=<salon de test>, limit=5)
    Attendu : 5 MESSAGES réels — pas 5 événements bruts. count=5,
    limit_reached=true, pages_fetched renseigné, events_scanned renseigné.
    Chaque message porte room_id, type et permalink.
B2. Choisis un salon de NOTIFICATION chargé d'historique supprimé, et demande
    limit=5. Attendu : 5 messages malgré les blocs de suppressions en tête.
    C'est le cas qui rendait une page VIDE : le budget de pages sautait avant
    d'avoir trouvé le moindre message. Si count=0, regarde empty_page_note.
B3. Reprends le next_token de B1 : messages plus anciens, ids différents.
B4. Même appel avec since=<une date récente> : seuls les messages de la
    fenêtre.

PHASE C — Écriture
C1. matrix_send_message(room_id, "ZZ recette 1.3.0 — racine")
    Attendu : event_id ET permalink. Note l'event_id : c'est la racine.
C2. matrix_reply_in_thread(room_id, thread_root_event_id=<racine>,
    message="ZZ réponse dans le fil")
    Vérifie DANS TON CLIENT MATRIX que la réponse est bien repliée sous la
    racine, et non un message indépendant. C'est le contrôle qui compte.
C3. matrix_get_thread(room_id, <racine>) : count=1, root au texte de C1.
C4. matrix_react(room_id, <racine>, "✅")
    Attendu : reaction_event_id DIFFÉRENT de l'event_id du message. Note-le.
C5. matrix_get_reactions(room_id, <racine>)
    Attendu : by_emoji avec ✅, votre identifiant en expéditeur, et le
    reaction_event_id — celui qu'il faut pour retirer une réaction.
C6. matrix_edit_message(room_id, <racine>, "ZZ recette 1.3.0 — racine corrigée")
    Attendu : succès et une note rappelant que l'original reste dans
    l'historique. Vérifie dans le client la mention (modifié).
C7. matrix_send_file(room_id, <PNG>, caption="ZZ image de recette")
    puis matrix_send_file(room_id, <PDF>, caption="ZZ pièce jointe PDF")
    Attendu : m.image puis m.file, une content_uri mxc:// à chaque fois, et
    les deux VISIBLES dans le client — image rendue, PDF en carte de fichier.
C8. matrix_send_file avec un chemin inexistant : refus sans téléversement.

PHASE D — Fichiers, contexte, permaliens
D1. matrix_list_files(room_id=<salon de test>)
    Attendu : les deux pièces de C7, avec filename, mimetype, size, mxc:// et
    permalink. La recherche n'indexe PAS le corps des m.image et m.file : sans
    cet outil, une pièce jointe était introuvable autrement qu'en remontant la
    chronologie à la main.
D2. matrix_get_event_context(room_id, <racine>, before=3, after=3)
    Attendu : 3 avant et 3 APRÈS — exactement, pas 4. Et aucune ligne sans
    corps : les réactions, éditions et suppressions sont écartées par défaut.
D3. Même appel avec include_bodyless=True : les lignes sans corps réapparaissent
    et portent un `type` qui dit ce qu'elles sont.
D3bis. FENÊTRE SATURÉE — le défaut du 02/09/2026. Dans le salon de test :
    poste trois messages « ZZ recette contexte 1/2/3 », puis supprime les deux
    premiers (matrix_redact_message), puis poste « ZZ recette contexte 4 ».
    Appelle ensuite matrix_get_event_context(room_id, <id du message 4>,
    before=3, after=0).
    Attendu : before contient 3 MESSAGES réels — pas [] et pas les
    suppressions. `pages_fetched.before` vaut plus de 1 (la fenêtre a été
    élargie), `events_scanned.before` le confirme, `complete.before` vaut true.
    Si `complete.before` est false, `truncation_note` doit être présent et dire
    pourquoi : une amputation annoncée reste acceptable, une liste vide muette
    non.
D4. matrix_get_permalink(room_id, <racine>) : ouvre l'URL, elle doit surligner
    le message dans le client.

PHASE E — État de lecture (revu en 1.4.0)
E1. matrix_list_unread()
    Attendu : les salons ayant du non-lu, mentions (highlights) en tête, CHACUN
    avec un `last_message` — plus aucun salon rendu sans, dans la limite de
    `lookups_budget` (compare `lookups_spent`). Les conversations directes
    portent un `room_name` (le nom de l'interlocuteur) et `direct: true`, plus
    d'identifiant nu.
E1bis. COMPTEURS. Ne les compare PAS à Element en attendant l'égalité : ils
    mesurent autre chose, et `counters_note` le dit dans la réponse. Contrôle
    plutôt la cohérence interne : tout salon portant `counter_suspect: true`
    doit avoir un `read_marker_at` POSTÉRIEUR à la date de son `last_message`
    — c'est ce qui rend son compteur impossible. Vérifie ensuite dans Element
    que ces salons-là sont bien à zéro, et que `total_unread_confirmed` est
    plus proche du vécu que `total_unread`.
E2. matrix_list_unread(only_unread=False) : tous les salons, y compris calmes.
    Attendu : `total_rooms` égale le nombre de salons rejoints, et `truncated`
    vaut false tant que `limit` couvre ce total. Un compte de plus de 200
    salons ne doit plus être tronqué en silence.
E3. matrix_mark_read(room_id=<salon de test>)
    ⚠️ L'état de lecture est PARTAGÉ côté serveur : marquer lu ici efface le
    badge dans Element aussi. Ne le faire que sur le salon de test.
    Attendu : `unread_after` = 0 (un ENTIER, pas null), `highlights_after` = 0,
    `read_state_confirmed` = true, et le badge disparu dans le client. Un null
    accompagné de `unread_after_note` est un échec de relecture, pas un zéro.

PHASE F — Nettoyage
F1. matrix_redact_message sur la réaction de C4 — avec le reaction_event_id,
    pas l'id du message.
F2. matrix_redact_message sur les deux fichiers, puis sur la réponse de fil.
F3. matrix_redact_message sur la RACINE.
    Attendu : success=true, verified=true, ET un champ edits_redacted listant
    l'événement de correction de C6. Une correction est un événement distinct :
    supprimer la racine seule la laissait affichée, texte compris. Vérifie dans
    le client qu'il ne reste rien.
F4. matrix_get_room_messages et matrix_search_messages sur « ZZ recette » :
    plus rien. matrix_list_files : plus de pièce de recette.
F5. Dis-moi ce qui reste, et pourquoi ça ne peut pas être retiré.

SYNTHÈSE
- Le tableau des contrôles.
- Les ÉCARTS avec la réponse brute.
- Ce que tu as vérifié DANS le client et non seulement dans le JSON : fil,
  correction, réaction, pièces jointes, badge de non-lu.
- Ce qui te manque encore pour travailler dans Matrix.
```

## Lecture des écarts

| Symptôme | Ce que ça veut dire |
|---|---|
| `complete` absent de D2 ou `counters_note` absent de E1 | 1.4.0 non chargée — ne pas recetter |
| A5 sans `index_matches_reliable` | version antérieure à 1.2.0 |
| B2 rend 0 message | le budget de pages saute encore : montrer `empty_page_note`, `pages_fetched` et `events_scanned` |
| C2 apparaît comme message normal | la relation de fil n'a pas pris |
| D2 rend 4 après pour 3 demandés | la coupe côté client ne s'applique pas |
| F3 sans `edits_redacted` | les corrections ne sont plus emportées — le piège revient |
| D3bis rend `before: []` | la fenêtre n'est plus élargie : montrer `pages_fetched`, `events_scanned` et `complete` |
| E1 : compteurs différents d'Element | attendu, et documenté : ce sont les compteurs du homeserver. L'écart n'est un défaut que si `counter_suspect` manque sur un salon dont le dernier message précède le repère de lecture |
| E3 rend `unread_after: null` sans note | la relecture ciblée a échoué sans le dire |

## Ce qui reste hors de portée

- **L'alignement des compteurs de non-lus sur ceux d'un client.** `unread` et
  `highlights` viennent de `unread_notifications` du /sync : ce que le
  homeserver compte après votre reçu de lecture, selon vos règles de
  notification. Element recalcule côté client, compte les fils à part et
  décrémente à l'affichage. Le connecteur ne peut pas reproduire ce calcul ;
  il rend le sien vérifiable (`read_marker_at`, `counter_suspect`,
  `total_unread_confirmed`).

- **La suppression d'une réaction d'autrui** dépend du droit de modération
  Matrix. Rien à faire côté connecteur.
- **Les médias téléversés** restent dans le dépôt Synapse sous leurs URI
  `mxc://` après suppression du message : seule une purge côté serveur les
  retire.
- **Les tombstones** (« a supprimé N messages ») sont inhérentes au protocole :
  la suppression retire le contenu, jamais la trace.
