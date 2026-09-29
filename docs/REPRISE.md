# Reprise : Kanboard 0.16.0, l'identite d'une ecriture

Mise a jour : 29/09/2026.

Document de reprise du chantier en cours, au sens des consignes inno3 : de quoi
reprendre sans l'historique de conversation. Il ne contient ni secret, ni donnee
client, ni transcription.

## Objectif et perimetre

Interdire qu'une ecriture demandee avec `as_user="agent"` parte sous l'identite
du compte primary. Perimetre : `kanboard_mcp/` et ses tests. Aucun autre
connecteur touche.

## Etat verifie

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

## Ce que le correctif etablit

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

## Verifications effectuees

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

## Trois tests existants adaptes, et pourquoi

- `tests/test_kanboard_transport.py` recoit une fixture `autouse` qui
  pre-etablit l'identite des deux comptes : ces tests comptent les requetes une
  par une, et le `getMe` initial decalait leurs scenarios.
- `tests/test_kanboard.py::test_single_account_setup_does_not_suggest_a_phantom_account`
  figeait l'ancien contrat (« agent non configure, c'est primary qui a signe »).
  Reecrit sur le seul compte configure, et double d'un
  `test_single_account_setup_refuses_an_agent_write` qui verifie le refus.

## Limites connues

- Le processus MCP de Claude Desktop est lance au demarrage du client :
  **la 0.16.0 installee n'est pas celle qui tourne dans la session en cours**.
  Verifie le 29/09 : `kanboard_who_am_i(as_user="agent")` ne rend pas encore
  `identity_verified`. La recette exige un redemarrage du client.
- `tests/` couvre les refus sur faux serveur. Le comportement reel de Kanboard
  sur `getMe` avec un token applicatif n'est pas verifie ici : il est deduit du
  fait que `getUserByLoginName` repond deja `-32601` sur ce serveur.
- Le repack d'installation remplace `manifest.json` et `src/server.py`, et
  conserve `name`, `display_name`, la commande `python3` et `user_config` du
  bundle en place. Le manifeste du depot porte un `name` different
  (`kanboard-mcp`, vendor-neutre) : l'installer tel quel creerait un doublon
  d'extension au lieu d'une mise a jour.

## Recette a jouer apres redemarrage de Claude Desktop

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

## Prochaines actions

1. Redemarrer Claude Desktop, jouer la recette ci-dessus.
2. Decider du sort de `fix/dolibarr-exports-complets` : la 0.15.0 installee en
   venait, et cette branche reste la seule a porter le travail Dolibarr 1.8.0.
3. Pousser quand demande. Rien n'a ete pousse.
