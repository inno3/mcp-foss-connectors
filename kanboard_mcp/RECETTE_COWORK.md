# Recette Cowork — connecteur Kanboard 0.13.2

**Cible : kanboard-inno3-v7 v0.13.2, 53 outils.** Trois correctifs à vérifier :
pagination de la recherche, création par nom, et complétude de `get_task`.

Valeurs relevées sur l'instance le 01/09/2026 : **66 projets actifs**, une
recherche « Dolibarr » à `limit=20` s'arrête au **27e projet**, et le balayage
exhaustif trouve **195 correspondances**.

## Avant de lancer

La phase C crée une tâche sur le projet **262** et la supprime en phase D.

## Prompt à copier

```
Tu recettes le connecteur MCP Kanboard 0.13.2 contre l'instance de production.
Après chaque phase, un tableau : contrôle | attendu | constaté (OK / ÉCART).
En cas d'ÉCART, montre la réponse brute.

ÉTAPE 0 — Contrôle de version, AVANT tout le reste
Appelle kanboard_get_task sur n'importe quelle tâche existante et vérifie que
la réponse contient un champ `category_name`. S'il est absent, la 0.13.2 n'est
pas chargée : ARRÊTE-TOI et dis-le. Les écarts que tu constaterais seraient
ceux d'une version antérieure, pas des défauts.

Phases A et B en lecture seule. La phase C crée puis supprime UNE tâche sur le
projet 262.

PHASE A — Le balayage, et l'accès à ce qu'il trouve
A1. kanboard_search_tasks(query="Dolibarr", limit=20)
    Attendu : has_more=true, projects_scanned nettement < projects_total
    (autour de 27 sur 66), un scan_warning nommant scan_all=True, ET
    has_more_results=true avec un next_offset — puisque total_matches (21)
    dépasse count (20). Ces deux derniers champs se contredisaient avec le
    scan_warning : un appelant fiant au booléen concluait avoir tout vu.
A2. kanboard_search_tasks(query="Dolibarr", limit=20, scan_all=True)
    Attendu : projects_scanned = projects_total = 66, has_more=false, aucun
    scan_warning, total_matches ≈ 195 très supérieur à count=20, next_offset=20.
A3. Enchaîne offset=20, puis offset=40, toujours avec scan_all=True.
    Attendu : des tâches DIFFÉRENTES à chaque page, aucun recoupement,
    next_offset qui progresse. Ce sont les tâches que la version d'avant ne
    pouvait pas nommer : le compte était corrigé, la liste restait plafonnée
    aux 20 premières. Nomme-en quelques-unes.
A4. kanboard_search_tasks(query="zzz-improbable-xyz", scan_all=True)
    Attendu : count=0, total_matches=0, 66/66, aucun scan_warning,
    has_more_results=false. Ici seulement, une absence prouve une absence.

PHASE B — Référentiels du projet 262
B1. kanboard_list_swimlanes(project_id=262) — note les noms exacts.
B2. kanboard_list_categories(project_id=262) — idem.
B3. kanboard_list_columns(project_id=262) — idem.
    Ces trois appels préparent la phase C. C'est exactement ce qu'il fallait
    faire avant CHAQUE création de tâche, et que la phase C rend inutile.

PHASE C — Création par nom [ÉCRITURE]
C1. kanboard_create_task(project_id=262, title="ZZ recette 0.13.2",
    description="Tâche de recette, à supprimer.",
    column_name=<un nom de B3>, swimlane_name=<un nom de B1>,
    category_name=<un nom de B2>, as_user="agent")
    Attendu : succès avec un task_id, AUCUN identifiant numérique fourni.
C2. kanboard_get_task(task_id=<le task_id>)
    Attendu : column_name, swimlane_name ET category_name conformes aux trois
    noms demandés, plus un project_name renseigné (il revenait vide, et la
    catégorie était absente : ce contrôle n'était pas faisable avec get_task).
    C'est ce qui prouve que la résolution par nom a visé juste sur les trois
    axes, sans passer par search_tasks.
C3. kanboard_create_task(project_id=262, title="ZZ ne doit pas exister",
    swimlane_name="Swimlane Inexistante XYZ")
    Attendu : REFUS énumérant les swimlanes, AUCUNE tâche créée. Vérifie-le par
    une recherche exhaustive. Un succès ici serait un échec de recette : la
    tâche aurait été classée au mauvais endroit.
C4. kanboard_create_task(..., swimlane_id=1, swimlane_name=<un nom de B1>)
    Attendu : refus « l'un OU l'autre, pas les deux ».
C5. kanboard_add_comment(task_id=<C1>, comment="Commentaire de recette.",
    as_user="agent")
    Attendu : succès, author_user_id correspondant au compte agent. C'est
    l'outil le plus utilisé du connecteur, autant le vérifier au passage.

PHASE D — Nettoyage
D1. kanboard_delete_task(task_id=<C1>, as_user="agent")
D2. kanboard_get_task sur ce même id.
    Attendu : found=false, avec un message expliquant que Kanboard rend un
    refus d'accès pour une tâche supprimée — les deux cas étant indiscernables
    par l'API. Un message parlant seulement de permissions serait un écart.
D3. Recherche exhaustive « ZZ » : 0 résultat. Dis-moi ce qui reste à la main.

SYNTHÈSE
- Le tableau des contrôles.
- Pour A3 : la liste nominative des tâches que l'ancienne version manquait.
- Les ÉCARTS avec la réponse brute.
- Ce qui te manque encore sur Kanboard au quotidien.
```

## Lecture des écarts

| Symptôme | Ce que ça veut dire |
|---|---|
| `category_name` absent de `get_task` | 0.13.2 non chargée — ne pas recetter |
| A1 : `has_more_results` false alors que `total_matches` > `count` | correctif inactif |
| A1 sans `scan_warning` | soit le balayage a couvert les 66 projets, soit version antérieure |
| A3 : mêmes tâches à offset=20 | `offset` non pris en compte |
| C3 crée une tâche | la validation par nom ne fonctionne pas — défaut grave |
| C2 : mauvais swimlane ou catégorie | la résolution a visé la mauvaise valeur |

## Chantiers ouverts, hors recette

Remontés lors des passages précédents, ce sont des choix de conception et non
des défauts. Ils ne sont pas dans le script :

- recherche par filtres (assignation, échéance, projet) plutôt que balayage
  texte projet par projet ;
- recherche dans les commentaires, où vit une part du contexte ;
- création et mise à jour en lot ;
- mutualisation du balayage entre pages successives de `scan_all`, qui coûte
  aujourd'hui 66 appels par page.
