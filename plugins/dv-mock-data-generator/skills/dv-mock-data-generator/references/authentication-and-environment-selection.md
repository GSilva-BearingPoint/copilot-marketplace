# Authentication and Environment Selection

This workflow is user-delegated only and delegates connection entirely to Microsoft's **dv-connect** skill
from [`microsoft/Dataverse-skills`](https://github.com/microsoft/Dataverse-skills). It must run under the
Power Apps user's own Microsoft account; service principals, client secrets, certificates, and unattended
identities are rejected.

## Required conversation sequence

1. Run the read-only dependency check. If dependencies are missing, ask whether to install them, let the
   user install manually, or cancel. Do not install from the original generation request alone.

2. Load **dv-overview**, then **dv-connect**. Check `dataverse auth who` and `pac org who` first. When they
   identify the same valid delegated connection, show the current account/environment/URL and ask whether
   to use it or change it.

3. If the user chooses change (or no valid connection exists), use dv-connect's environment discovery
   (`dataverse auth list` / `pac auth list` / `pac org list` as applicable). Return the environments available
   to the signed-in account and **stop**. Never auto-select the active, first, production, or most recently
   used environment.

4. The user must provide:
   - One environment selected from that returned list.
   - A valid seed email address.

5. Complete dv-connect for exactly that URL. Both Dataverse CLI/Python and PAC authentication surfaces must
   use the same user account and selected environment.

6. Require dv-connect's verification gates to pass: `dataverse auth who`, `dataverse org who`,
   `pac org who`, and `python scripts/auth.py --check`. Only then pass the verified URL and mandatory seed
   email into the workbook discovery command.

## Why two interactive auth surfaces may appear

PAC CLI lists environments available to the user. The Dataverse CLI writes the shared delegated MSAL cache
used by the official Python SDK and MCP proxy. They use different Microsoft applications/token caches, so
both can require sign-in. The user must choose the same account for both prompts.
