# Historique HedgeDoc

Les versions antérieures à la 1.2.1 sont décrites dans l'historique Git et dans le
[README](README.md).

## 1.2.1 - 2026-11-06

- Claude Desktop transmet `${user_config.x}` tel quel quand un réglage optionnel sans
  valeur par défaut n'est pas renseigné (constaté le 06/11/2026 sur Nextcloud et
  WordPress). Le manifeste passait `hedgedoc_api_token` sans défaut : laissé vide, le
  jeton valait le gabarit. Le serveur l'envoyait en en-tête
  `Authorization: Bearer ${user_config.hedgedoc_api_token}` tant qu'aucune session
  n'était ouverte, et tentait une connexion avant chaque requête en mode anonyme.
- `hedgedoc_api_token` porte `"default": ""`.
- Tout réglage resté en gabarit est ignoré, avec un avertissement au journal qui nomme
  la variable. Une valeur qui ne fait que contenir le gabarit reste prise telle quelle.
- L'extension installée en 1.2.0 ne transmet pas ce jeton : le défaut y est latent, il
  ne touche qu'un bundle construit depuis le manifeste du dépôt.
- Aucun outil, paramètre ni champ de réponse modifié.
