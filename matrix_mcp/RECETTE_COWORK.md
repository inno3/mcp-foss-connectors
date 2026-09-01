# Recette Cowork — connecteur Matrix 1.3.0

**Cible : matrix-inno3 v1.3.0, 22 outils.** Version issue de trois passages de
recette : elle ferme onze écarts et ajoute l'état de lecture.

## Avant de lancer

Un **salon de test**, ou un salon à faible trafic dont vous êtes membre. Les
phases C et D y écrivent ; tout est supprimé en phase F. Ne pas viser un salon
client.

Prévoyez un petit **PNG** et un petit **PDF** locaux pour la phase C.

## Prompt à copier

```
Tu recettes le connecteur MCP Matrix 1.3.0 contre l'instance de production.
Après chaque phase, un tableau : contrôle | attendu | constaté (OK / ÉCART).
En cas d'ÉCART, montre la réponse brute.

Salon de test : <ROOM_ID à remplir>
PNG local : <chemin>   PDF local : <chemin>

ÉTAPE 0 — Contrôle de version, AVANT tout le reste
Vérifie que les outils `matrix_list_files`, `matrix_list_unread` et
`matrix_mark_read` existent. Si l'un manque, la 1.3.0 n'est pas chargée :
ARRÊTE-TOI et dis-le. Les trois passages précédents ont produit des « écarts »
qui n'étaient que des versions antérieures — ne recommence pas.

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
D4. matrix_get_permalink(room_id, <racine>) : ouvre l'URL, elle doit surligner
    le message dans le client.

PHASE E — État de lecture (nouveau en 1.2.0)
E1. matrix_list_unread()
    Attendu : les salons ayant du non-lu, mentions (highlights) en tête, avec
    le dernier message tronqué. Compare les compteurs avec ceux d'Element :
    ils doivent coïncider. C'est l'outil qui répond à « qu'est-ce qui est
    arrivé depuis hier » sur 218 salons.
E2. matrix_list_unread(only_unread=False) : tous les salons, y compris calmes.
E3. matrix_mark_read(room_id=<salon de test>)
    ⚠️ L'état de lecture est PARTAGÉ côté serveur : marquer lu ici efface le
    badge dans Element aussi. Ne le faire que sur le salon de test.
    Attendu : unread_after=0, et le badge disparu dans le client.

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
| `matrix_list_files` absent | 1.3.0 non chargée — ne pas recetter |
| A5 sans `index_matches_reliable` | version antérieure à 1.2.0 |
| B2 rend 0 message | le budget de pages saute encore : montrer `empty_page_note`, `pages_fetched` et `events_scanned` |
| C2 apparaît comme message normal | la relation de fil n'a pas pris |
| D2 rend 4 après pour 3 demandés | la coupe côté client ne s'applique pas |
| F3 sans `edits_redacted` | les corrections ne sont plus emportées — le piège revient |
| E1 : compteurs différents d'Element | l'état vient du serveur : montrer la réponse brute |

## Ce qui reste hors de portée

- **La suppression d'une réaction d'autrui** dépend du droit de modération
  Matrix. Rien à faire côté connecteur.
- **Les médias téléversés** restent dans le dépôt Synapse sous leurs URI
  `mxc://` après suppression du message : seule une purge côté serveur les
  retire.
- **Les tombstones** (« a supprimé N messages ») sont inhérentes au protocole :
  la suppression retire le contenu, jamais la trace.
