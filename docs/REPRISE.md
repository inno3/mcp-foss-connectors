# Etat final du lot A au 09/10/2026

Worktree : `/home/jean/ssd_data/jean/RepoGit/_codex_wt/a-mcp-foss-connectors`. Branche : `codex/2026-10-10-dolibarr-nextcloud`.
Base : main, sans reprise d'etat non commite. Copies de travail d'origine
preservees. Commits locaux uniquement, aucune installation ni aucun push.

Le rapport transversal et les commandes de verification sont dans
[le compte rendu du lot A](LOT_A_2026-10-09.md).

Commits fonctionnels utiles avant le compte rendu final :

```text
0077f99 fix(nextcloud 1.4.1): annoncer la racine locale resolue des envois
3818b79 fix(dolibarr 1.8.2): borner et paginer la lecture des temps par tache
d689ac5 test(dolibarr): comparer liste et projection sur les trois echeances
05dfc5f fix(dolibarr 1.8.1): partager la lecture des echeances fournisseurs
0211d5e chore(connecteurs): reprendre les bases Dolibarr et Nextcloud verifiees
```

Les tests de regression simules passent et les lectures reelles ont ete
consignees ci-dessous. Les codes installes ont les memes empreintes Git
qu'au debut du lot. Aucun secret ni payload client conserve dans les rapports.

En attente de Benjamin : revue et integration des branches, construction et
installation des extensions, deploiement du module, activation de la creation
sur facture et produit configure. Toute recette en ecriture reste a faire sur
une instance isolee. PROV92 n'a pas ete supprime. L'absence de temps renvoyes
par l'API du projet 300 reste a examiner si des temps sont attendus.

Les sections suivantes conservent les mesures et decisions par etape ; les
consignes de production du week-end priment sur tous les historiques.

## Point 8 du lot A termine

Nextcloud 1.4.1 : upload_root est resolu et upload_root_configured preserve
la valeur configuree. La docstring explique que le fichier est sur le poste
executant le connecteur. Regression reproduite avec un lien Downloads et un
fichier absent, puis corrigee. Aucune requete Nextcloud ni fichier televerse.

## Point 5 du lot A termine

Version 1.8.2 : budget global 20 s, maximum 25 s, pagination par tache et
curseur avec perimetre verifie. Les erreurs ne perdent pas les lignes deja
collectees. Six tests rouges avant, sept verts apres (annulation comprise).
Les totaux partiels sont nommes comme tels et ne doivent pas etre additionnes
lorsqu'une tache est relue a cause d'une limite d'ecritures.

Production en GET uniquement : project_id=300, 436 taches. Appel par defaut :
20 taches, 1,027 s, truncated=true et curseur. Parcours par pages de 100 taches :
8,806 s ; 9,003 s ; 14,731 s ; 9,547 s ; 8,710 s (derniere page de 36).
Chaque appel reste sous 30 s. L'API n'a renvoye aucune ecriture sur les 436
lectures : cela ne prouve pas l'absence de temps en base ni dans Kanboard.
Cette donnee source reste a examiner si des temps sont attendus. Rien n'a
ete modifie pour tenter de les reconstituer.

## Point 3 du lot A termine

Echeance fournisseur partagee entre liste et projection, version 1.8.1.
Tests : deux variantes de champ, dont une rouge avant correction.
Lecture de production : les trois dates concordent pour les references
fournies dans la mission. Aucun reglement ni facture modifie.

# Lot A du week-end du 09/10/2026

Reprise courante dans `/home/jean/ssd_data/jean/RepoGit/_codex_wt/a-mcp-foss-connectors`, branche `codex/2026-10-10-dolibarr-nextcloud`, creee depuis `main`.
Les copies de travail preexistantes restent intactes. Production en lecture seule,
aucune installation, aucun deploiement, aucun push. PROV92 reste en attente de Benjamin.

Source reprise sans reecrire les correctifs deja faits : Dolibarr 41e7b0f (1.8.0), identique au serveur installe. Nextcloud 8843017 (1.4.0), successeur du 1.3.0 installe. Le code installe du 1.3.0 differe de f9b49b4 uniquement par son emballage inno3.
Seuls les fichiers des connecteurs et de SupportCredits sont reportes depuis
les commits cites, jamais les modifications non commitees des autres sessions.
Les sections historiques ci-dessous decrivent d'autres chantiers et ne valent
pas autorisation de deployer. Les versions et chemins historiques restent a confirmer.

Etat : references locales reconstituees ; tests de regression du lot a ajouter.
Harnais sans installation : PHPUnit du depot canonique, dependances Python
deja presentes dans le cache uv et les bundles installes, lus sans bytecode.
Tests de reference : DAV 60 passes et 1 ignore ; SupportCredits 274 tests,
804 assertions ; coeur Dolibarr et Nextcloud 594 passes et 1 ignore.
Extension : 275 passes et deux controles de metadonnees a relancer avec
les metadonnees du bundle existant accessibles au harnais.

Prochaines actions : tests des defauts restants, correctifs locaux, controles
en lecture seule, documentation de reprise et commits locaux par sujet.

## Historique anterieur au lot A
