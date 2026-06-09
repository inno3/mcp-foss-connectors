# GitLab connector (`gitlab-mcp`)

MCP server for the [GitLab](https://gitlab.com/) REST API v4. Projects, issues, merge requests, repository files & branches, and CI/CD pipelines. Works against gitlab.com or any self-managed instance.

Part of [**mcp-foss-connectors**](../README.md). Configured entirely through
environment variables — no host, login or secret is baked into the code.

## Environment variables

| Variable | Required | Description |
|---|---|---|
| `GITLAB_URL` | no | Base URL of the GitLab instance. Default `https://gitlab.com`. No trailing slash. |
| `GITLAB_TOKEN` | yes | Personal Access Token with the `api` (or `read_api`) scope. |

## Tools (18)

- `gitlab_list_projects` — Liste les projets GitLab auxquels l'utilisateur appartient
- `gitlab_get_project` — Retourne les informations essentielles d'un projet GitLab
- `gitlab_list_issues` — Liste les issues d'un projet GitLab
- `gitlab_get_issue` — Retourne une issue complète avec sa description
- `gitlab_create_issue` — Crée une nouvelle issue dans un projet GitLab
- `gitlab_update_issue` — Met à jour une issue existante dans GitLab
- `gitlab_add_issue_comment` — Ajoute un commentaire (note) sur une issue GitLab
- `gitlab_list_merge_requests` — Liste les merge requests d'un projet GitLab
- `gitlab_get_merge_request` — Retourne un merge request complet avec tous ses détails
- `gitlab_create_merge_request` — Crée un nouveau merge request dans un projet GitLab
- `gitlab_list_repo_tree` — Liste les fichiers et dossiers d'un répertoire du dépôt GitLab
- `gitlab_get_file` — Retourne le contenu d'un fichier du dépôt GitLab (décodé depuis base64)
- `gitlab_list_pipelines` — Liste les pipelines CI/CD d'un projet GitLab
- `gitlab_get_pipeline` — Retourne les détails complets d'un pipeline GitLab avec ses jobs
- `gitlab_whoami` — Retourne les informations de l'utilisateur GitLab authentifié
- `gitlab_create_file` — Crée un nouveau fichier dans un projet GitLab via POST /projects/{id}/repository/files
- `gitlab_update_file` — Met à jour un fichier existant dans un projet GitLab via PUT /projects/{id}/repository/files
- `gitlab_list_branches` — Liste les branches d'un projet GitLab via GET /projects/{id}/repository/branches

## License

[Apache-2.0](../LICENSE) — © 2026 inno³ and contributors.
