# Réécriture de l'historique du 17/10/2026

Les lignes `Co-Authored-By` ont été retirées des messages de commit, à la demande
de Benjamin. Seuls les messages ont changé : arbres de fichiers, auteurs, dates et
filiation sont identiques, vérifiés commit par commit. Les empreintes changent donc
à partir du premier commit concerné. Les tags `archive/*` et les branches publiées
ont été réécrits de la même façon. Les empreintes citées dans les fichiers du dépôt
ont été mises à jour ; pour une citation ancienne trouvée ailleurs, cette table donne
la nouvelle empreinte.

## Nommage neutre du 21/10/2026

Les noms de branches de lot et de dossiers de travail cités dans la documentation
et dans les sorties de validation ont été neutralisés : préfixe de branche `lot/`,
dossier de travail `RepoGit/_lots_wt/`. Les branches citées sous ce préfixe sont
fusionnées et n'existent plus ; les étiquettes `archive/lot/` pointent les mêmes
commits que celles qu'elles remplacent. Les mesures enregistrées sont inchangées,
seuls les chemins et les noms de branche cités le sont.

96 commits concernés.

| Date | Ancienne | Nouvelle | Sujet |
| --- | --- | --- | --- |
| 2026-06-23 | `ba2be4b8ef61` | `f6b3292b466e` | feat(kanboard): réassignation de cartes + extensions update_task/move_task (v0.8.0) |
| 2026-07-29 | `73e7cb27586d` | `f15f247b2787` | fix(dolibarr,kanboard,wordpress): anti-doublons sur retry, as_user strict, TLS par défaut |
| 2026-07-29 | `8bdbe7b474d2` | `720040da5739` | feat(kanboard): échéances dans get_board, filtre et pagination sur list_projects |
| 2026-07-31 | `22e6a4dcfb85` | `c6766d48ba61` | fix(build): epingler mcp<2.0, la 2.0 supprime mcp.server.fastmcp |
| 2026-07-31 | `2a6555838076` | `3fe393d08ee3` | fix(dolibarr): name why time logging failed, and stop reporting seconds as hours — v1.1.0 |
| 2026-07-31 | `3deaf0aa6bf3` | `06d4ca39eb53` | fix(kanboard): a bad comment/task id no longer reads as a permission problem — v0.11.1 |
| 2026-07-31 | `5147cc071fa0` | `f95d59f8a124` | feat(kanboard): full comment management (read, edit, delete) — v0.9.0 |
| 2026-07-31 | `56c0843158c3` | `3f535c01cba0` | feat(kanboard): make the search sweep and its scope visible — v0.11.0 |
| 2026-07-31 | `e6b3da798981` | `b3893de82d43` | feat(kanboard): task links, so a card can be attached to a milestone — v0.12.0 |
| 2026-07-31 | `f0ccae93ff95` | `8daf7bd190d4` | feat(kanboard): project-access diagnostic, activity cap surfaced, non-admin fallback — v0.10.0 |
| 2026-08-05 | `1ad5c24a800d` | `9151707a3923` | revert(inno3pilot-mcp): retirer le connecteur séparé au profit de l'extension Dolibarr |
| 2026-08-05 | `2cb4ecfd8609` | `105a430c439c` | feat(inno3pilot-mcp): connecteur MCP pour les boards inno3pilot (6 tools) |
| 2026-08-05 | `7e90f0cfbf2c` | `8fce8cf4facb` | fix(inno3pilot-mcp): paramètre API project_id (pas fk_projet) — validé live |
| 2026-08-11 | `254835b27b53` | `1cbe7e139e41` | feat(dolibarr): mettre à jour une facture, projet compris — v1.2.0 |
| 2026-08-11 | `38633143c24c` | `8fb3c90c5efa` | feat(dolibarr): rapatrier les 5 outils propale génériques — v1.3.0 |
| 2026-08-11 | `6caaba33bdd4` | `1ca4a122bc80` | fix(build): le bundle avec extension declarait 42 outils et en exposait 71 |
| 2026-08-11 | `b8b97864bdc2` | `85fbbabc5ecb` | feat: reconcilier nextcloud, hedgedoc et wordpress avec la copie privee |
| 2026-08-11 | `cbe09b1231bd` | `7c76a47880db` | test: rapatrier les suites qui couvraient le code porte, et declarer les env manquantes |
| 2026-08-12 | `783152f9a905` | `ccc3e476040c` | feat(build): --bundle-name, pour mettre a jour une extension deja installee |
| 2026-08-13 | `473e2570b3e5` | `6b76dbf24a7d` | feat(dolibarr): read and manage the people assigned to a task |
| 2026-08-13 | `6b0feca5d543` | `9c670f56337c` | fix(dolibarr): name the Dolibarr 23 crash behind a failed time entry |
| 2026-08-13 | `83bee3dd18d6` | `012c25b70e10` | feat(dolibarr): read, correct and delete time entries, v1.4.0 |
| 2026-08-14 | `6eee7e63f80e` | `2aba4393d35e` | fix(dolibarr): send the fields task creation has always required, v1.4.1 |
| 2026-08-31 | `2a2bea1dd600` | `881caef7f39d` | feat(wordpress): warn on block loss, expose revisions and a content check, v1.4.0 |
| 2026-09-01 | `0be3a7337816` | `cb11c6a6b226` | docs(dolibarr): recette 1.7.0, avec une phase tickets signalée comme non éprouvée |
| 2026-09-01 | `69a7216c7ef1` | `806c136f6ee4` | feat(dolibarr): banque, règlements, facturation et pièces jointes — v1.7.0 |
| 2026-09-01 | `9a6167a6add0` | `95de2db7c844` | feat(matrix): recherche exploitable, fils, état de lecture — v1.3.0 |
| 2026-09-01 | `aa33a52cf3ce` | `701b34adc403` | docs: compteurs d'outils, et la règle de mise à jour d'une extension installée |
| 2026-09-01 | `c3788dff1ff3` | `9c44353d705d` | docs: script de vérification globale après consolidation |
| 2026-09-01 | `dc76cff059a1` | `17977d528bdf` | fix(kanboard): un balayage honnête et une création sans identifiants — v0.13.2 |
| 2026-09-01 | `dcb4b70edd83` | `a9e94a145cfe` | merge: wordpress 1.4.0 — perte de blocs, révisions et contrôle de contenu |
| 2026-09-01 | `f851368c539d` | `36af8e36dae6` | feat(n8n): diagnostiquer et surveiller les exécutions — v1.2.0 |
| 2026-09-02 | `5db50b313113` | `3da2ed579cbc` | feat(matrix 1.4.0): contexte non amputé, compteurs de non-lus auditables |
| 2026-09-02 | `a37e9a4483be` | `8b842704ad96` | perf(kanboard): mes tâches en retard en un appel au lieu d'un balayage — v0.14.0 |
| 2026-09-03 | `5cf2634a86ea` | `ac60470781df` | fix(n8n): des comptes qui cessent de bouger quand on change limit, v1.2.1 |
| 2026-09-03 | `9c4b77feeca3` | `449a1a780e42` | feat(wordpress 1.5.0): des taxonomies personnalisées écrites, vérifiées, et plus jamais ignorées en silence |
| 2026-09-03 | `9cd63f585a98` | `5764887a1cfd` | fix(kanboard): une page de tâches qui dit qu'elle est une page, v0.14.2 |
| 2026-09-03 | `b958c5e06a78` | `db69fb09f244` | fix(kanboard): un nom passé par du HTML retrouve sa colonne, v0.14.1 |
| 2026-09-08 | `0d454de09209` | `dfc91541110e` | fix(dolibarr): une erreur réseau qui dit son nom, et des lectures rejouées, v1.7.1 |
| 2026-09-24 | `7b85a18fa6ac` | `737f9e73c6ef` | fix(dolibarr 1.7.4): les notes d'une lecture de détail sont rendues entières, ou disent qu'elles sont coupées |
| 2026-09-24 | `a9c5596fe3a3` | `0c612c86aabb` | feat(nextcloud 1.2.0): dépôt de fichiers binaires (base64 ou fichier local) |
| 2026-09-24 | `c4f612db997a` | `bb20d42e552a` | fix(kanboard 0.15.0): une erreur dit son code, et une écriture dit si elle a pu passer |
| 2026-09-24 | `d1001c5e9267` | `e12289b7f622` | feat(dolibarr 1.7.5): créer un ticket dit ce qu'il a écrit ailleurs, et la saisie de temps ne se dit plus morte |
| 2026-09-25 | `61ef033a17c6` | `72b954518f9e` | fix(gitlab 1.1.0): une erreur dit son code, et une écriture dit si elle a pu passer |
| 2026-09-25 | `7af68b8cca3a` | `7cbdc262e7e8` | feat(nextcloud 1.3.0): condensat sha256, 404 sur parent absent, erreurs DAV lisibles |
| 2026-09-25 | `7b8dc1933ae6` | `95459752f4b4` | fix(wordpress 1.6.0): une erreur dit son code REST, et une écriture dit si elle a pu passer |
| 2026-09-25 | `7ba596386265` | `0e67fefbf99c` | fix(n8n 1.3.0): une erreur dit son code, et une écriture dit si elle a pu passer |
| 2026-09-25 | `8843017abfc9` | `c39d12923c49` | fix(nextcloud 1.4.0): une erreur dit son code, et une écriture dit si elle a pu passer |
| 2026-09-25 | `89c6f6953bd8` | `64e1659a036c` | fix(hedgedoc 1.2.0): une erreur dit son code, et une écriture dit si elle a pu passer |
| 2026-09-25 | `92af4d8eaa2e` | `c27af49ca4ce` | fix(matrix 1.5.0): onze outils cessent de rendre « Erreur : » et une écriture dit si elle a pu passer |
| 2026-09-25 | `c1a1995bfcd2` | `a30dc3065973` | fix(dolibarr 1.7.6): la production tourne en 24.0.1, pas en 23 — la docstring le disait faux |
| 2026-09-28 | `41e7b0fb9273` | `3e922e3a616d` | fix(dolibarr 1.8.0): un total financier incomplet ne se présente plus comme certain |
| 2026-09-28 | `706a06f6be43` | `6d686a6af881` | fix(dolibarr 1.8.0): un refus de droits reste explicite avant une suppression |
| 2026-09-28 | `7a0c7f21c1cb` | `f21539fcb9b5` | fix(kanboard 0.15.0): une erreur dit son code, et une écriture dit si elle a pu passer |
| 2026-09-28 | `91409913e528` | `2fed96700253` | fix(dolibarr 1.8.0): les exports distinguent montant absent et solde nul |
| 2026-09-28 | `cc5125b61124` | `c139ba6caff3` | feat(dolibarr 1.8.0): les listes annoncent leur total et leur page suivante |
| 2026-09-28 | `ed72c6efc221` | `71c6681e7a27` | fix(kanboard 0.16.0): une ecriture « agent » ne part plus sous le compte humain |
| 2026-09-28 | `f0796428c061` | `82fcbed6c1f8` | chore(dolibarr): le cœur reste générique, la reprise du chantier vit dans l'extension |
| 2026-09-29 | `08420a9d4487` | `852659f88cbf` | feat(dolibarr 1.9.0): une échéance par défaut se voit avant qu'une relance parte |
| 2026-09-29 | `26cde39073d7` | `b13a3ff6c679` | docs(reprise): de quoi reprendre le chantier Kanboard sans l'historique |
| 2026-09-29 | `370773b77de3` | `6ff6b542ff03` | fix(matrix 1.5.0): onze outils cessent de rendre « Erreur : » et une écriture dit si elle a pu passer |
| 2026-09-29 | `3bd55385f0b0` | `ce5c06b994b9` | feat(nextcloud 1.2.0): dépôt de fichiers binaires (base64 ou fichier local) |
| 2026-09-29 | `50f7f15cbf58` | `8983c14e4a5f` | fix(wordpress 1.6.0): une erreur dit son code REST, et une écriture dit si elle a pu passer |
| 2026-09-29 | `70b9c2d3881b` | `84b0a902ff98` | fix(dolibarr 1.7.6): la production tourne en 24.0.1, pas en 23 — la docstring le disait faux |
| 2026-09-29 | `739299e5a3cf` | `849a0651a606` | feat(dolibarr 1.7.5): créer un ticket dit ce qu'il a écrit ailleurs, et la saisie de temps ne se dit plus morte |
| 2026-09-29 | `873f2b0ec694` | `4ef833632b50` | fix(gitlab 1.1.0): une erreur dit son code, et une écriture dit si elle a pu passer |
| 2026-09-29 | `959203aa1156` | `adc1df07cbb5` | fix(kanboard 0.17.0): un tableau cesse de compter les cartes deja fermees |
| 2026-09-29 | `a71b9f98ef8d` | `9f0a91b7ed3c` | fix(nextcloud 1.4.0): une erreur dit son code, et une écriture dit si elle a pu passer |
| 2026-09-29 | `aa9b9fb474f6` | `fab4eae448a3` | fix(n8n 1.3.0): une erreur dit son code, et une écriture dit si elle a pu passer |
| 2026-09-29 | `b6833a784913` | `d3ae6f2acea2` | feat(wordpress 1.5.0): des taxonomies personnalisées écrites, vérifiées, et plus jamais ignorées en silence |
| 2026-09-29 | `bf560835ef80` | `6af907e42acf` | fix(dolibarr 1.7.4): les notes d'une lecture de détail sont rendues entières, ou disent qu'elles sont coupées |
| 2026-09-29 | `d40f501616b8` | `8af68c1718fb` | fix(dolibarr): une erreur réseau qui dit son nom, et des lectures rejouées, v1.7.1 |
| 2026-09-29 | `da8289c8994e` | `5515623cb2e8` | fix(hedgedoc 1.2.0): une erreur dit son code, et une écriture dit si elle a pu passer |
| 2026-09-29 | `f9b49b48f097` | `7697ff2d147d` | feat(nextcloud 1.3.0): condensat sha256, 404 sur parent absent, erreurs DAV lisibles |
| 2026-10-09 | `0077f992ac35` | `183a2a001e28` | fix(nextcloud 1.4.1): annoncer la racine locale resolue des envois |
| 2026-10-09 | `0211d5ecf332` | `03b17104da6d` | chore(connecteurs): reprendre les bases Dolibarr et Nextcloud verifiees |
| 2026-10-09 | `03ca172bd6fb` | `55e6278f404d` | test(kanboard): conserver et vérifier la protection d'identité 0.16.0 |
| 2026-10-09 | `05dfc5f8386b` | `073a2a610c50` | fix(dolibarr 1.8.1): partager la lecture des echeances fournisseurs |
| 2026-10-09 | `17e1d3e3ca23` | `76a307bd2a81` | docs(lot A): consigner les verifications et preparer la reprise |
| 2026-10-09 | `3818b79152d9` | `a62fd5a8e7aa` | fix(dolibarr 1.8.2): borner et paginer la lecture des temps par tache |
| 2026-10-09 | `d689ac52e64b` | `35b643a3016b` | test(dolibarr): comparer liste et projection sur les trois echeances |
| 2026-10-10 | `5365edaafde6` | `f741e9de5922` | docs(reprise kanboard): la recette d'identite est jouee et verte, le 10/10/2026 |
| 2026-10-16 | `13e45c4a2798` | `253b4736353c` | fix(dolibarr 1.8.3): un modulepart inconnu du cœur se dit comme tel |
| 2026-10-17 | `0fcd80f9b935` | `c3d2c1a8f167` | fix(kanboard 0.17.2): laisser Kanboard attribuer l’auteur sans identité primary |
| 2026-10-17 | `3a045f1bb1d3` | `5b95f82f4898` | fix(kanboard 0.17.2): conserver la preuve réelle de l’identité primary |
| 2026-10-17 | `523f9c5d1a8c` | `bc034405a967` | fix(kanboard 0.17.2): centraliser les consignes et la reprise du complément |
| 2026-10-17 | `54d891a46992` | `a326136e6a1f` | merge: connecteur Dolibarr 1.8.3 et Nextcloud 1.4.1 (lot L du 17/10/2026) |
| 2026-10-17 | `6e0efe95bbd0` | `c0f006eca5e1` | fix(kanboard 0.17.1): réunir le source installé et ses contrats d’identité |
| 2026-10-17 | `8f98923227db` | `892f4f9890a8` | fix(dolibarr 1.8.2): borner les lectures de temps et reprendre leur pagination |
| 2026-10-17 | `9c51522522bb` | `b8b42cb3bc74` | merge: Kanboard 0.17.2 (lots K et K2 du 17/10/2026) |
| 2026-10-17 | `a1ad5a101611` | `d872a4aa278b` | fix(dolibarr 1.8.3): consigner la reprise et les vérifications du lot L |
| 2026-10-17 | `bff16977221e` | `86eecc5b773b` | fix(kanboard 0.17.2): compter les bonnes cartes et rendre les textes lisibles |
| 2026-10-17 | `daadab925f94` | `92b9d59e39fa` | fix(nextcloud 1.4.1): indiquer la racine locale résolue des envois |
| 2026-10-17 | `e9b4a8a81481` | `1b5b70679cf1` | fix(kanboard 0.17.2): couvrir les erreurs d’auteur des commentaires |
| 2026-10-17 | `f607c5a0ab2d` | `64f64bd8258c` | fix(dolibarr 1.8.1): fiabiliser les échéances et afficher les conditions de règlement |
| 2026-10-17 | `faa6f2988723` | `310d6f628f61` | fix(dolibarr 1.8.3): conserver les métadonnées des exports de temps |
