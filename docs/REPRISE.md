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

