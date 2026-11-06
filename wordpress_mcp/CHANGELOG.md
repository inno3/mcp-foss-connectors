# Historique WordPress

Les versions antérieures à la 1.6.1 sont décrites dans l'historique Git et dans le
[README](README.md).

## 1.6.1 - 2026-11-06

- Constaté dans l'environnement du processus installé (1.6.0) : Claude Desktop transmet
  `${user_config.x}` tel quel quand un réglage optionnel sans valeur par défaut n'est
  pas renseigné. `WP_PROD_SUBSITES` et `WP_TEST_SUBSITES` valaient le gabarit, lu comme
  un JSON invalide : sous-sites écartés, mais avertissement trompeur à chaque démarrage,
  sans le nom de la variable.
- `wp_prod_subsites` et `wp_test_subsites` portent `"default": ""`.
- Tout réglage resté en gabarit est ignoré, avec un avertissement au journal qui nomme
  la variable. Une valeur qui ne fait que contenir le gabarit reste prise telle quelle.
- Aucun outil, paramètre ni champ de réponse modifié.
