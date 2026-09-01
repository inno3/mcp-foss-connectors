# Recette Cowork — connecteur n8n 1.2.0

**Cible : n8n-inno3 v1.2.0, 22 outils.** Lecture seule sauf la phase D.

Valeurs relevées sur l'instance le 31/08/2026.

## Prompt à copier

```
Tu recettes le connecteur MCP n8n 1.2.0 contre l'instance de production.
Après chaque phase, un tableau : contrôle | attendu | constaté (OK / ÉCART).
En cas d'ÉCART, montre la réponse brute.

ÉTAPE 0 — Contrôle de version, à faire AVANT tout le reste
Vérifie que l'outil `n8n_workflows_health` existe. S'il est absent, la 1.2.0
n'est pas chargée : ARRÊTE-TOI et dis-le. Ne recette pas une version que tu
n'as pas identifiée — les écarts constatés seraient ceux d'une version
antérieure, pas des défauts.

Les phases A à C sont en lecture seule. La phase D écrit : demande-moi
confirmation avant de la jouer.

PHASE A — La surveillance qui manquait
A1. n8n_workflows_health()
    Attendu : les workflows actifs, les plus dégradés en tête.
    « Brief quotidien direction → Matrix » (esFmnG1NIMOQD2C0) en premier,
    health « en échec », consecutive_failures ≥ 5, last_error
    « Converting circular structure to JSON », last_error_node
    « API — Agenda aujourd'hui (CalDAV) ».
    C'est le contrôle central : ce workflow échouait chaque matin depuis le
    25 août sans que rien ne le signale.
A2. Dans la même réponse : les workflows actifs « jamais exécuté ». Liste-les —
    un déclencheur qui ne s'est jamais déclenché est plus souvent une erreur de
    configuration qu'une absence d'événement.
A3. n8n_list_failures(hours=168)
    Attendu : les échecs de la semaine groupés dans by_workflow, chacun avec
    failures, last_started_at et last_error. Vérifie que workflows_affected
    correspond à la longueur de by_workflow.
A4. n8n_list_failures(hours=24) : un sous-ensemble, window_hours = 24.

PHASE B — Ce que la version précédente ne savait pas faire
B1. n8n_get_executions() SANS workflow_id.
    Attendu : des exécutions de plusieurs workflows. Ce paramètre était
    obligatoire — il fallait dix-sept appels pour balayer l'instance.
B2. n8n_get_executions(workflow_id="esFmnG1NIMOQD2C0", status="error", limit=5)
    Attendu : cinq échecs, chacun avec un champ `error` RENSEIGNÉ et
    errors[0].node nommant le nœud CalDAV. errors_resolved = 5.
    Ce champ était toujours absent malgré la promesse de la docstring.
B3. Même appel avec with_errors=False : errors_resolved = 0, aucun champ error.
B4. n8n_get_executions(started_after="2026-08-30T00:00:00Z", limit=20)
    Attendu : uniquement des exécutions du 30 août ou après. Si la réponse
    porte un champ `note` parlant de filtrage côté client, signale-le :
    l'instance n'accepte pas les bornes de date et le filtre n'a porté que sur
    la page demandée.

PHASE C — Le diagnostic redevenu lisible
C1. n8n_get_execution_detail("31027")
    Attendu : une réponse COURTE (elle faisait 195 000 caractères et saturait
    le client). Contrôles : errors[0].message = « Converting circular structure
    to JSON », errors[0].node = « API — Agenda aujourd'hui (CalDAV) »,
    duration_s ≈ 2,8, un output_note expliquant l'omission des charges utiles,
    et aucun contenu de nœud volumineux.
C2. Même appel avec include_output=True, max_item_chars=300
    Attendu : aperçus bornés à 300 caractères, preview_truncated sur les nœuds
    concernés, preview_note global. La réponse reste lisible.
C3. n8n_list_tags()
    Attendu : soit la liste, soit success=false disant que l'instance n'expose
    pas /tags. Une liste vide se faisant passer pour « aucune étiquette »
    serait un écart.

PHASE D — ÉCRITURE, à ne jouer qu'après mon accord
⚠️ Rejouer relance le traitement RÉEL. Le brief quotidien envoie un message
Matrix : le rejouer en enverra un.
D1. Demande-moi confirmation, en rappelant ce que le workflow va faire.
D2. Si j'accepte : n8n_retry_execution("31027", load_workflow=True)
    load_workflow=True rejoue avec la version ACTUELLE du workflow, donc avec
    la correction du nœud CalDAV si elle a été faite. Sans elle le rejeu
    échouera pareil — ce qui est aussi une information.
D3. n8n_workflows_health() : consecutive_failures est-il retombé ?

SYNTHÈSE
- Le tableau des contrôles.
- Les ÉCARTS avec l'appel exact et la réponse brute.
- L'état réel du parc : workflows en échec, jamais exécutés, et depuis quand.
- Ce qui te manque encore pour surveiller ces automatisations.
```

## Lecture des écarts

| Symptôme | Ce que ça veut dire |
|---|---|
| `n8n_workflows_health` absent | 1.2.0 non chargée — ne pas recetter |
| A1 ne montre pas le brief en échec | le workflow a été corrigé depuis, ou le parc a changé |
| B2 : `error` absent | la relecture par exécution ne se fait pas |
| C1 dépasse quelques milliers de caractères | ancienne version active |
| B4 : champ `note` présent | l'instance ignore les bornes de date |
| A3 : `error_lookup_note` présent | plus de 25 échecs sur la fenêtre, causes résolues partiellement |
