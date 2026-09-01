# Vérification globale — après la consolidation du 01/09/2026

Cinq connecteurs ont été mis à jour en une fois, depuis une branche `main`
unique. Ce script **ne remplace pas** les recettes détaillées par connecteur :
il vérifie en dix minutes que le déploiement a pris et que rien n'a régressé,
avant d'engager une recette longue.

| Extension | Version | Outils | Recette détaillée |
|---|---|---|---|
| dolibarr-erp-inno3-v2 | 1.7.0 | 107 | `dolibarr_mcp/RECETTE_COWORK.md` |
| n8n-inno3 | 1.2.0 | 22 | `n8n_mcp/RECETTE_COWORK.md` |
| matrix-inno3 | 1.3.0 | 22 | `matrix_mcp/RECETTE_COWORK.md` |
| kanboard-inno3-v7 | 0.13.2 | 53 | `kanboard_mcp/RECETTE_COWORK.md` |
| wordpress-inno3 | 1.4.0 | 33 | — |

## Prompt à copier

```
Vérification globale après consolidation des connecteurs MCP inno³.
Entièrement en LECTURE SEULE : aucune écriture, aucun nettoyage à prévoir.
Rends un tableau unique : connecteur | chargé | contrôle | constaté.

ÉTAPE 1 — Les cinq connecteurs répondent-ils ?
Pour chacun, un appel trivial :
  dolibarr_list_bank_accounts()      → des comptes et des soldes
  n8n_list_workflows(limit=5)        → des workflows
  matrix_whoami()                    → @bjean:matrix.inno3.fr
  kanboard_who_am_i()                → ton compte
  wordpress_site_info()              → le site inno3
Si l'un ne répond pas, dis lequel et arrête-toi là pour celui-là seulement.

ÉTAPE 2 — Les versions déployées sont-elles bien celles attendues ?
Vérifie la PRÉSENCE de ces outils signature. Un absent = version périmée, et
tout écart constaté ensuite serait celui d'une version antérieure, pas un
défaut :
  dolibarr_update_ticket        (Dolibarr 1.7.0)
  n8n_workflows_health          (n8n 1.2.0)
  matrix_list_files             (Matrix 1.3.0)
  kanboard_search_tasks         avec un paramètre `offset` (Kanboard 0.13.2)
  wordpress_list_revisions      (WordPress 1.4.0)

ÉTAPE 3 — Un contrôle de fond par connecteur
Un seul appel chacun, celui qui prouve que le correctif principal est actif.

3a. dolibarr_list_invoices(status="all", date_start="2026-08-01",
    date_end="2026-08-31")
    Attendu : count=7 avec credit_notes_count=2. Si count=5, les avoirs sont
    de nouveau masqués — c'était le défaut le plus coûteux.

3b. n8n_workflows_health()
    Attendu : les workflows actifs, les plus dégradés en tête, avec
    consecutive_failures et last_error renseignés. Dis-moi lesquels sont en
    échec et depuis quand — c'est aussi une information de fond, pas seulement
    un contrôle.

3c. matrix_search_messages(query=<un mot fréquent>, limit=50, body_chars=40)
    Attendu : 50 résultats dans une réponse COURTE, corps portant
    body_truncated. Sans le plafond, cet appel sortait à 872 000 caractères.

3d. kanboard_search_tasks(query="Dolibarr", limit=20, scan_all=True, offset=20)
    Attendu : des tâches DIFFÉRENTES des 20 premières, next_offset qui avance.
    C'est ce qui était inatteignable.

3e. wordpress_list_revisions sur un article récent.
    Attendu : une liste de révisions. Ce connecteur vient d'une session
    parallèle que je n'ai pas exercée contre l'instance : traite tout écart
    comme non éprouvé plutôt que comme une régression.

ÉTAPE 4 — Rien n'a régressé ailleurs ?
Trois appels sur des fonctions anciennes, pour vérifier que la mise à jour n'a
rien cassé au passage :
  dolibarr_get_revenue_summary(year=2026, month=8) → month_ca_ht = 6350
  kanboard_my_overdue()                            → répond sans erreur
  matrix_list_rooms()                              → tes salons

SYNTHÈSE
- Le tableau.
- Tout connecteur muet ou toute version périmée, nommés.
- Les workflows n8n en échec, avec leur cause.
- Ce que tu recommandes de recetter en profondeur ensuite, et pourquoi.
```

## Si quelque chose cloche

Les cinq installations ont été sauvegardées avant remplacement, dans le
répertoire de session `backup-consolidation`. Le retour arrière d'une
extension consiste à restaurer son `manifest.json` et son `src/`, puis à
redémarrer le client.

Le registre du client affiche encore les anciens numéros de version : c'est
cosmétique, le code chargé est celui du dossier. Seuls `tools` et `version` du
manifeste ont été remplacés — le contrat de configuration, lui, est celui de
l'installation, vérifié identique pour les cinq.
