# Historique des changements n8n

## 1.3.1 (2026-10-17)

- Activation et désactivation d'un workflow par `POST` avec un corps JSON
  vide sur `workflows/{id}/activate` et `workflows/{id}/deactivate`.
  Les appels `PATCH` provoquaient une erreur HTTP 405.
- Régressions reprises du correctif `8243999`, complétées pour préserver le
  contrat de transport 1.3.0 : état d'écriture explicite, absence de rejeu après
  réponse perdue ou HTTP 5xx, rejeu sur connexion impossible et journal des
  écritures déjà appliquées.
