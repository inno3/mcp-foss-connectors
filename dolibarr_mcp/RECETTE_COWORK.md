# Recette Cowork — connecteur Dolibarr 1.7.0

**Cible : dolibarr-erp-inno3-v2 v1.7.0, 107 outils** (78 du cœur + 29 de
l'extension inno³).

Cette recette vérifie ce qui a changé depuis la 1.4.1 : banque, règlements,
facturation client et fournisseur, pièces jointes, et les correctifs issus du
premier passage. Elle est **en lecture seule sauf mention contraire**.

Valeurs relevées sur l'instance le 31/08/2026.

## Avant de lancer

1. Un **compte bancaire de test** (type compte courant, solde 0, rapprochement
   activé). Toutes les écritures bancaires y restent confinées.
2. Un **tiers de test** — le 290 « TEST MCP Fix inno3 » a servi aux passages
   précédents.
3. Pour la phase D : `DOLIBARR_WEB_LOGIN` et `DOLIBARR_WEB_PASSWORD` sur un
   compte ayant le droit *Banque → Rapprocher*. Sans eux, le refus explicite
   est un résultat valide.

**Aucune facture n'est validée par cette recette** : aucun numéro de séquence
comptable n'est consommé. C'est la différence avec la version précédente, qui
en brûlait un.

## Prompt à copier

```
Tu recettes le connecteur MCP Dolibarr 1.7.0 contre l'instance de production.
Après chaque phase, un tableau : contrôle | attendu | constaté (OK / ÉCART).
N'enchaîne pas si une phase a produit un ÉCART sur une écriture : arrête-toi
et montre la réponse brute.

Contexte : compte bancaire de test <ID> · tiers de test <ID>

ÉTAPE 0 — Contrôle de version, AVANT tout le reste
Vérifie que `dolibarr_delete_invoice`, `dolibarr_validate_supplier_invoice` et
`dolibarr_update_ticket` existent. Si l'un manque, la 1.7.0 n'est pas chargée :
ARRÊTE-TOI et dis-le.

Règles : aucune écriture hors du compte et du tiers de test. Montants < 10 €.
Ne valide AUCUNE facture. Tu nettoies en phase F.

PHASE A — Les avoirs ne disparaissent plus
A1. dolibarr_list_invoices(status="all", date_start="2026-08-01",
    date_end="2026-08-31")
    Attendu : count=7 et credit_notes_count=2, avec AV2608-0651 (−33 300 TTC)
    et AV2608-0652 (−21 960 TTC) dans la liste. On obtenait 5, sans eux :
    `min_amount` valant 0 par défaut écartait tout total négatif.
A2. dolibarr_list_invoices(status="all", limit=100) : count=100. C'était 87.
A3. Même appel avec min_amount=1000 : un seuil explicite filtre toujours.
A4. dolibarr_list_supplier_invoices(status="all", limit=50) :
    credit_notes_count présent.

PHASE B — Le chiffre d'affaires se lit
Août 2026 contient FA2608-0940 (46 050 HT) émise le 19/08 et créditée le même
jour par deux avoirs de −27 750 et −18 300, soit −46 050 exactement.
B1. dolibarr_get_revenue_summary(year=2026, month=8, basis="emises")
    Attendu, exactement : month_ca_ht=6350, month_gross_ht=52400,
    month_credit_notes_ht=−46050, month_nb_credit_notes=2.
    Contrôle : brut + avoirs = net. basis_label doit dire « NET des avoirs ».
B2. Volet annuel : ytd_ca_ht=190991.67, ytd_gross_ht=382561.67,
    ytd_credit_notes_ht=−191570. Même contrôle arithmétique.
B3. basis="encaissees" : ≈ 23 036,50 € TTC sur 3 encaissements, et PAS de
    champ month_ca_ht — un encaissement n'a pas de base HT.

PHASE C — Banque, le sens du montant [ÉCRITURE, compte de test]
C1. dolibarr_create_bank_entry(direction="credit", amount=5, libellé
    "ZZ recette credit") : amount_signed=+5, balance_delta=+5, entry.credit=5.
C2. Idem direction="debit", amount=2 : amount_signed=−2, balance_delta=−2,
    entry.debit=2.
C3. dolibarr_get_bank_balance sur le compte de test : +3,00 € exactement
    depuis le début de la phase.
C4. amount=-5 avec direction="debit" : REFUS attendu. Un succès ici est un
    échec de recette — c'est la double négation qu'on cherche à empêcher.
C5. payment_type="SEPA" : refus avec la liste des codes valides.
C6. dolibarr_list_bank_entries sur le compte de test : les deux écritures,
    solde cumulé cohérent.
C7. Même appel avec reconciled="no" : sous-ensemble, et running_balance_note
    expliquant l'absence de cumul.

PHASE D — Rapprochement [ÉCRITURE]
D1. dolibarr_toggle_bank_reconciliation(compte de test, enabled=False) puis
    dolibarr_set_bank_statement : REFUS « compte non rapprochable », sans
    aucune écriture. Vérifie que les lignes sont intactes.
D2. enabled=True : rappro_before=0, rappro_after=1, et account.balance égal au
    solde réel (il rendait 0, valeur par défaut de l'objet API).
D3. dolibarr_set_bank_statement(compte, [les deux entry_id], "ZZ-RECETTE")
    - identifiants web configurés : applied_count=2, et les lignes portent le
      relevé dans Dolibarr ;
    - sinon : refus nommant DOLIBARR_WEB_LOGIN, aucune écriture touchée.
      Vérifie-le en relisant les lignes.
D4. dolibarr_list_bank_statements sur le compte de test.

PHASE E — Facturation, pièces jointes, trésorerie [ÉCRITURE]
E1. dolibarr_create_invoice sur le tiers de test, une ligne « ZZ recette » à
    4 € HT, TVA 20 % : brouillon, ref (PROVxxx), total_ht=4, lines_added=1.
E2. dolibarr_add_invoice_lines : une ligne à 1 €, total HT → 5 €.
E3. dolibarr_delete_invoice_line sur la deuxième : lines_before/lines_after
    différents, total revenu à 4 €.
E4. dolibarr_create_supplier_invoice sur le tiers de test,
    ref_supplier="ZZ-RECETTE-01", une ligne à 3 € HT. Note l'id et la ref.
E5. dolibarr_upload_document sur cette facture fournisseur, en passant la ref
    provisoire TELLE QUELLE, parenthèses comprises : ref="(PROVxx)".
    Attendu : succès, ref="PROVxx", ref_requested="(PROVxx)". Cette forme
    produisait « Erreur JSON Dolibarr : Expecting value ».
E6. dolibarr_list_documents(module="supplier_invoice", ref=<ref interne>)
    Attendu : PLUS de 404. Soit la liste, soit success=false avec le message
    de Dolibarr et modulepart_sent="facture_fournisseur". Le point vérifié est
    la traduction du nom de module.
E7. Crée une proposition de test (create_proposal + add_proposal_lines, 2
    lignes), puis dolibarr_create_invoice_from_proposal.
    Attendu : les 2 lignes reprises, lines_added=2, pas de total_warning, et
    dans Dolibarr la facture visible dans les « éléments liés » de la propale.
E8. dolibarr_cash_forecast(horizon_days=90) : opening_balance égal à la somme
    des comptes ouverts hors caisse, et les retards connus dans overdue_in.
E9. dolibarr_add_invoice_payment sur une facture BROUILLON : refus attendu.

PHASE E bis — Tickets (nouveau en 1.7.0) [ÉCRITURE]
Ces quatre outils viennent d'une session parallèle : je ne les ai pas encore
exercés contre l'instance. Traite-les comme non éprouvés.
Eb1. dolibarr_create_ticket sur le tiers de test, sujet « ZZ recette ticket ».
     Note l'id et la ref.
Eb2. dolibarr_add_ticket_message : un message de recette. Puis relis le ticket
     et vérifie que le message y figure.
Eb3. dolibarr_update_ticket : change le sujet. Relis pour confirmer.
Eb4. dolibarr_close_ticket. Attendu : le statut passe à fermé, vérifié par
     relecture — pas seulement annoncé.
Eb5. dolibarr_delete_ticket. Attendu : succès, puis get_ticket ne le trouve
     plus. Si l'un de ces cinq contrôles échoue, montre la réponse brute :
     c'est du code que je n'ai pas relu en profondeur.

PHASE F — Nettoyage
F1. dolibarr_delete_bank_entry sur les deux écritures de C1/C2. Si elles ont
    été rapprochées en D3, l'outil DOIT refuser : c'est le comportement
    attendu, dis-le et laisse-les.
F2. dolibarr_delete_invoice sur la facture de E1, puis
    dolibarr_delete_invoice(<id E4>, supplier=True).
    Attendu : success=true, vérifié par relecture.
F3. Tente dolibarr_delete_invoice sur une facture VALIDÉE de ton choix, sans
    la supprimer : REFUS parlant de la séquence comptable. Un succès ici est
    un échec de recette.
F4. dolibarr_delete_proposal sur la propale de E7.
F5. Liste ce qui reste à retirer à la main, et pourquoi.

SYNTHÈSE
- Le tableau des contrôles.
- Les ÉCARTS avec l'appel exact et la réponse brute.
- Ce qui reste à nettoyer manuellement.
- Ce qui te manque encore pour le pilotage quotidien.
```

## Lecture des écarts

| Symptôme | Ce que ça veut dire |
|---|---|
| `dolibarr_delete_invoice` ou `dolibarr_update_ticket` absent | 1.7.0 non chargée — ne pas recetter |
| A1 rend 5 au lieu de 7 | le correctif `min_amount` n'est pas actif |
| B1 : brut + avoirs ≠ net | régression arithmétique, à remonter telle quelle |
| B1 juste mais chiffres différents | une facture a été émise depuis : recouper avec A1 |
| C1/C2 : `balance_delta` opposé | inversion de signe, le défaut le plus grave |
| E5 : « Expecting value » | la normalisation de la ref provisoire n'est pas active |
| E6 : 404 sec sans message | les alias de module ne sont pas appliqués |
| F3 supprime la facture | le garde-fou de statut ne fonctionne pas |

## Ce qui reste hors de portée

- **Le rapprochement bancaire** n'a aucune route REST dans Dolibarr 24 : il
  dépend du repli formulaire, donc des identifiants web.
- **Les liens sortants d'une propale** ne sont pas énumérables par l'API :
  `delete_proposal` ne peut pas prévenir qu'une facture y est rattachée.
- **Les quatre lignes de virement interne** des recettes précédentes sont liées
  entre elles et non supprimables par le connecteur.
