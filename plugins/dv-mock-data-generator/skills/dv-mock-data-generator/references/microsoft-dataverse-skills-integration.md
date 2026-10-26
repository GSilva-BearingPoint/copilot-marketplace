# Microsoft Dataverse Skills Integration

Canonical dependency: <https://github.com/microsoft/Dataverse-skills>

This tool is an orchestration layer, not a replacement for Microsoft's Dataverse skills.

## Routing

1. Load `dv-overview` first.
2. Use `dv-connect` for installation, delegated authentication, environment listing and explicit selection,
   `.env`, shared token caches, MCP registration, and connection checks.
3. Use `dv-metadata` for current table/column/choice/key/relationship metadata.
4. Use `dv-query` for read-only checks and existing reference data.
5. Use `dv-data` for SDK authentication helpers, record creation, dependency-ordered bulk operations, and
   rollback.

The local Python files are limited to functionality not provided by those skills:

- Render the dynamically discovered proposal as an editable Excel workbook.
- Give every proposed record a stable pre-create key.
- Link lookup cells to the exact related worksheet row.
- Validate edited workbook references offline.
- Translate approved stable keys into the real GUIDs returned by `dv-data` writes.

## Installation requirement

If the official skills are not available to the agent, stop and install them:

```powershell
npx skills add microsoft/Dataverse-skills -s "*" -g
```

Dedicated marketplace installations documented by Microsoft are also valid (`/plugin install
dataverse@awesome-copilot` for GitHub Copilot and `/plugin install
dataverse@claude-plugins-official` for Claude Code).
