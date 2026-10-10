# Reprise : connecteur Kanboard

Mise a jour : 10/10/2026.

Document de reprise au sens des consignes inno3 : de quoi reprendre sans
l'historique de conversation. Ni secret, ni donnee client, ni transcription.

Deux chantiers se suivent sur `kanboard_mcp/`, dans cet ordre. Le second est
rebase sur le premier, qui n'est pas encore merge dans `main`.

## Kanboard 0.16.0, l'identite d'une ecriture

### Objectif et perimetre

Interdire qu'une ecriture demandee avec `as_user="agent"` parte sous l'identite
du compte primary. Perimetre : `kanboard_mcp/` et ses tests. Aucun autre
connecteur touche.

### Etat verifie

- Depot : clone de `contrib-OS/mcp-foss-connectors` (Forgejo `code.inno3.eu`),
  ici `/home/jean/ssd_data/jean/RepoGit/mcp-wt/mcp-foss-connectors`.
  Un second clone du meme depot existe en `RepoGit/inno3/mcp-foss-connectors`,
  sur `fix/dolibarr-1.7.4-note-public-entiere`, non touche par ce chantier.
  Le Kanboard de `RepoGit/mcp-inno3/kanboard_mcp` est une ligne anterieure
  (0.10.0) : ce n'est pas la source de ce qui est installe.
- Branche : `fix/kanboard-identite-agent`, creee depuis `origin/main`, avec
  `c4f612d` (kanboard 0.15.0) reporte par cherry-pick en `7a0c7f2`, puis le
  correctif en `ed72c6e`. Non poussee.
- Pourquoi une branche neuve : le 0.15.0 installe ne vivait que sur
  `fix/dolibarr-exports-complets`, 19 commits en avance sur `origin/main` et
  melangee a du travail Dolibarr. Un seul de ces commits touche Kanboard, d'ou
  le report. **Cette session a laisse le clone sur la nouvelle branche** : pour
  revenir a l'etat anterieur, `git checkout fix/dolibarr-exports-complets`
  (rien n'y a ete perdu, l'arbre etait propre).
- Version installee sur le poste : 0.15.0 avant, **0.16.0 apres**. Sauvegarde de
  l'etat precedent dans
  `~/.config/Claude/Claude Extensions/.backup-kanboard-inno3-v7-2026-09-29-v0.15.0`.

### Ce que le correctif etablit

Le detail fonctionnel est dans `kanboard_mcp/README.md`, section
« An identity that cannot be established forbids the call ». En resume :

- `_get_credentials()` ne retombe plus sur primary : elle leve
  `KanboardIdentityError`, en lecture comme en ecriture ;
- `_ensure_account_identity()` etablit l'identite par `getMe` avant la premiere
  ecriture d'un compte, puis la garde dans `_identity_cache`. Trois refus pour
  le compte agent : meme login que primary, token rattache a un autre login,
  `user_id` egal a celui du primary. Un `getMe` muet est un refus ;
- le compte primary tolere un `getMe` indisponible (token applicatif) mais pas
  un `getMe` qui repond un autre login. La verification manquante est declaree,
  pas supposee ;
- les reponses rendent le compte servi, et une creation rend l'auteur
  reellement enregistre, relu depuis l'objet.

Rupture assumee : sur une installation a un seul compte, un appel explicite
`as_user="agent"` echoue desormais au lieu de repondre avec le compte primary.

### Verifications effectuees

- Environnement de test : `/tmp/venv-mcp/bin/python` (pytest 9.1.1, mcp, httpx).
  Le `.venv` de `RepoGit/inno3/mcp-foss-connectors` est casse (il pointe vers
  `/home/bjean`), ne pas chercher a s'en servir.
- `python -m pytest tests/ -q` : 856 passes, 1 saute. Reference avant le
  correctif : 852 passes, 1 saute.
- `python -m ruff check kanboard_mcp/ tests/` : propre.
- Le bundle repacke demarre sous le python systeme (3.14.4) avec son `src/lib`
  sur PYTHONPATH, expose 53 outils, et `importlib.metadata.version("mcp")`
  repond 1.29.1.
- Configuration de production relue et confrontee aux nouvelles regles :
  `KANBOARD_USER=bjean` / `getMe` repond `bjean` (uid 4),
  `KANBOARD_USER_ALT=claude` / `getMe` repond `claude` (uid 84). Logins
  distincts, uid distincts : les quatre controles passent, aucune regression
  attendue sur ce poste.

### Trois tests existants adaptes, et pourquoi

- `tests/test_kanboard_transport.py` recoit une fixture `autouse` qui
  pre-etablit l'identite des deux comptes : ces tests comptent les requetes une
  par une, et le `getMe` initial decalait leurs scenarios.
- `tests/test_kanboard.py::test_single_account_setup_does_not_suggest_a_phantom_account`
  figeait l'ancien contrat (« agent non configure, c'est primary qui a signe »).
  Reecrit sur le seul compte configure, et double d'un
  `test_single_account_setup_refuses_an_agent_write` qui verifie le refus.

### Limites connues

- Le processus MCP de Claude Desktop est lance au demarrage du client : le
  29/09, **la 0.16.0 installee n'etait pas celle qui tournait dans la session**,
  et `kanboard_who_am_i(as_user="agent")` ne rendait pas encore
  `identity_verified`. **Leve le 10/10/2026** : apres redemarrage, c'est bien
  le nouveau code qui repond, et la recette ci-dessous est verte. La limite
  vaut toujours pour toute installation future.
- `tests/` couvre les refus sur faux serveur. Le comportement reel de Kanboard
  sur `getMe` avec un token applicatif n'est pas verifie ici : il est deduit du
  fait que `getUserByLoginName` repond deja `-32601` sur ce serveur.
- Le repack d'installation remplace `manifest.json` et `src/server.py`, et
  conserve `name`, `display_name`, la commande `python3` et `user_config` du
  bundle en place. Le manifeste du depot porte un `name` different
  (`kanboard-mcp`, vendor-neutre) : l'installer tel quel creerait un doublon
  d'extension au lieu d'une mise a jour.

### Recette, jouee le 10/10/2026 : verte

Mesures de ce jour, par les outils MCP de la session :

- etape 1 : `identity_verified: true`, `effective_account: "agent"`,
  `resolved_user_id: 84`, et `getMe` qui repond `claude`/84. Le refus documente
  sur `getUserByLoginName` (`-32601`) apparait bien comme une verification
  manquante declaree, pas comme un echec d'identite ;
- etape 2 : `identity_verified: true`, `resolved_user_id: 4`, `getMe` repond
  `bjean`/4 ;
- etape 3 : `kanboard_list_accounts()` rend les deux comptes en
  `status: "configured"`, aucun `fallback_to_primary` ;
- etape 4 : commentaire 2670 pose sur la carte 8010 du projet 262 en
  `as_user="agent"`. Reponse d'ecriture : `author_user_id: 84`,
  `author_user_id_recorded: 84`, sans `author_mismatch`. Relu **depuis le
  compte primary**, le commentaire est enregistre sous `user_id: 84`,
  `username: "claude"` : la confirmation vient de Kanboard, pas du connecteur.
  La carte 8010 est la carte de suivi du chantier, pas une carte jetable ; si
  une recette sur carte dediee est voulue, elle reste a designer.

La rupture assumee n'a produit aucun effet de bord observe : aucun appel de la
session n'a ete refuse pour identite non etablie.

Un ecart de libelle, sans effet sur l'identite : la recette d'origine attendait
un commentaire signe `Claude (Assistant IA)`. Sur cette instance, l'utilisateur
84 porte `username: "claude"` et `name: "agent"` ; c'est donc « agent » qui
s'affiche. L'uid est le bon, le nom affiche est une donnee de l'instance, pas du
connecteur. A corriger dans Kanboard si la signature longue est voulue.

### Recette d'origine, pour memoire

1. `kanboard_who_am_i(as_user="agent")` doit rendre `identity_verified: true`,
   `effective_account: "agent"` et `resolved_user_id: 84`.
2. `kanboard_who_am_i(as_user="primary")` doit rendre `identity_verified: true`
   et `resolved_user_id: 4`.
3. `kanboard_list_accounts()` ne doit plus contenir `fallback_to_primary`.
4. Un commentaire temoin par `kanboard_add_comment(as_user="agent")` sur une
   carte de test du projet 262, a designer par le mainteneur. La reponse doit
   porter `as_user: "agent"`, `author_user_id: 84` et
   `author_user_id_recorded: 84`, sans `author_mismatch`. Verifier ensuite dans
   Kanboard que le commentaire est signe `Claude (Assistant IA)` et non du
   compte humain.

### Prochaines actions

1. ~~Redemarrer Claude Desktop, jouer la recette ci-dessus.~~ Fait, et vert, le
   10/10/2026. Le chantier 0.16.0 est clos cote recette.
2. Decider du sort de `fix/dolibarr-exports-complets` : la 0.15.0 installee en
   venait, et cette branche reste la seule a porter le travail Dolibarr 1.8.0.
3. Pousser quand demande. Rien n'a ete pousse.
4. Le poste porte toujours la 0.16.0 : la 0.17.0 de la section suivante n'est ni
   construite ni installee. C'est la seule marche restante sur ce connecteur.

## Perimetre du board et rendu des textes

### Objectif

Corriger trois defauts du connecteur `kanboard_mcp`, releves en production le
29/09/2026 sur le projet Kanboard 262 lors d'un relevé des colonnes de suivi :

1. `kanboard_get_board` rendait les cartes fermees sans aucun marqueur, et les
   comptait dans `task_count` ;
2. les entites HTML des textes lus (titres, descriptions, commentaires)
   n'etaient pas decodees ;
3. aucun outil de liste ne rendait `date_modification`, ce qui mettait le
   critere de dormance hors de portee sans un appel par carte.

### Emplacement et branche

- Depot : `/home/jean/RepoGit/inno3/mcp-foss-connectors`, remote `inno3/plugins/mcp`.
  `/home/jean/ssd_data/jean/RepoGit/inno3/mcp-foss-connectors` designe le meme
  arbre, pas une seconde copie de travail.
- Branche : `fix/kanboard-0.17.0-board-status-entites-dates`, rebasee sur
  `fix/kanboard-identite-agent` (26cde39). Le premier jet partait de `c1a1995`,
  anterieur au 0.16.0 : le garder aurait silencieusement defait le correctif
  d'identite deja installe sur le poste.
- Ce chantier a laisse le worktree `RepoGit/inno3/mcp-foss-connectors` sur cette
  branche. Il etait sur `fix/dolibarr-1.7.4-note-public-entiere` auparavant,
  arbre propre : `git checkout fix/dolibarr-1.7.4-note-public-entiere` y ramene.
- Suivi : carte Kanboard 8012 du projet 262 (swimlane Dev & infra).

### Constats verifies en production, pas deduits

Mesures faites le 29/09/2026 sur le projet 262, via les outils MCP deployes :

- `getBoard` rendait 61 des 63 cartes fermees du projet (les 2 manquantes
  tombaient dans une colonne deja tronquee a 100). La somme des `task_count`
  valait 523, soit exactement 460 cartes actives plus 63 fermees.
- Colonne « Action Benjamin » : 107 cartes annoncees, 45 fermees, donc 62
  actives. Colonne « Suspens » : 13 annoncees, 8 fermees, donc 5 actives.
- Cause en amont : `BoardFormatter` de Kanboard ne pose aucune condition sur
  `is_active`, alors que `getExtendedQuery` selectionne explicitement
  `is_active`, `date_creation`, `date_modification` et `date_completed`. Le
  champ etait donc disponible et jete par le connecteur.
- Carte 7970 : titre stocke `[Alice &amp; Bob] Proposer une intervention au
  ticket`, un seul niveau d'encodage, description comprise.

### Decisions arretees

- Le defaut de `kanboard_get_board` passe a `status=1` (cartes actives), qui
  correspond a ce qu'affiche un tableau Kanboard. `status=-1` restitue le
  perimetre des versions anterieures a 0.17.0 : l'ancien comportement reste
  atteignable, il cesse d'etre le defaut.
- `task_count`, `total` et la liste `tasks` d'une colonne portent tous sur le
  meme perimetre. Un compte plus large que la liste se lisait comme une
  troncature et envoyait chercher des cartes qui etaient ailleurs.
- Une carte dont Kanboard ne rend pas `is_active` n'est jamais ecartee : elle
  sort avec `is_active=null` et sa colonne porte `status_filter_incomplete`.
  Filtrer sur un champ absent rejouerait la perte silencieuse a l'envers.
- `modified_before` s'applique AVANT la pagination, sans quoi `total_available`
  porterait sur le projet entier et la liste sur autre chose. Les cartes sans
  date exploitable sont mises de cote et comptees dans
  `modified_before_unknown` : une carte sans date n'est pas dormante, elle est
  indatable.
- Le decodage des entites se fait par `_text`, une seule fois, uniquement sur
  les chemins de lecture. Il ne touche ni les textes envoyes a Kanboard, ni les
  comparaisons de libelle, ni les messages d'erreur de transport, dont le
  contenu peut venir d'un proxy.
- `kanboard_update_swimlane` et `kanboard_update_category` relisent le nom
  courant pour le RENVOYER a Kanboard quand seule la description ou la couleur
  change : ce nom reste brut, le decoder reecrirait le libelle stocke.
- La liste d'AMBIGUITE de `_resolve_label` reste brute, contrairement aux
  « valeurs disponibles ». Elle enumere des libelles que le repli confond
  justement : `Missions &amp; Hermine` et `Missions  &  Hermine` se decodent
  sur la meme chaine, et les decoder priverait l'appelant du seul moyen de les
  distinguer. Le test
  `TestCreateTaskResolvesLabelsThatWentThroughHtml.test_a_name_matching_two_rows_after_normalisation_is_refused`
  fige ce choix.

### Limites connues, documentees dans les docstrings

- `getBoard` ne pagine pas cote serveur. Le connecteur recoit toutes les cartes
  du projet, fermees comprises, et filtre ensuite : `status` economise des
  tokens en sortie, pas le volume de l'appel JSON-RPC.
- `getAllTasks` ne pagine pas non plus : `offset` epargne la resolution des
  libelles, pas l'appel.
- `getProjectActivity` plafonne a 50 evenements, plafond global et non
  parametrable. `modified_before` est la reponse au besoin de dormance, pas
  `kanboard_recent_activity`.
- Les bornes de date s'entendent dans le fuseau du serveur MCP, comme toutes
  les dates rendues par le connecteur.

### Etat courant

- `kanboard_mcp/manifest.json` en version 0.17.0. Le poste porte la 0.16.0 :
  le correctif n'est PAS deploye tant que le `.mcpb` n'est pas reconstruit et
  reinstalle, et le processus MCP n'est relance qu'au demarrage du client.
- Suite complete : 1211 tests reussis, 1 ignore. `ruff check .` passe.
- Verification de non-vacuite : en neutralisant `_text`, 47 tests tombent.

### Harnais de test

Le `.venv` du depot est mort depuis la restauration du poste : il pointe vers
un interpreteur disparu sous `/home/bjean`. Reconstruire un harnais hors de
l'arbre du depot :

```sh
python3 -m venv /tmp/venv-mcp && /tmp/venv-mcp/bin/pip install \
  pytest pytest-asyncio httpx ruff "mcp<2"
cd /home/jean/RepoGit/inno3/mcp-foss-connectors
PYTHONPATH=. /tmp/venv-mcp/bin/python -m pytest tests/ -q
```

`mcp<2` est obligatoire : en 2.x, `FastMCP` est renomme et tous les `server.py`
du parc echouent a l'import, ce qui se lit comme une regression du code alors
que c'est la dependance qui a bouge.

### Fichiers de reference

- Implementation : `kanboard_mcp/server.py`.
- Tests : `tests/test_kanboard.py`, sections de non regression en fin de fichier.
- Procedure de publication : `docs/RELEASE.md`, `docs/PACKAGING.md`.
- Construction du bundle : `python scripts/build_mcpb.py kanboard`.

### Prochaines actions

1. Reconstruire le bundle `.mcpb` et le reinstaller, sans toucher aux noms de
   champs `user_config` : ce sont les cles sous lesquelles le client a stocke
   la configuration (voir `docs/RELEASE.md`, section 4bis).
2. Rejouer le relevé du projet 262 apres deploiement et verifier que
   « Action Benjamin » annonce 62 cartes et « Suspens » 5.
3. Clore la carte Kanboard 8012 une fois le relevé conforme.
