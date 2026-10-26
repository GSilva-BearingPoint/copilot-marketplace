# Seed Email Aliasing and Safety

## Why

Many tables require an email-like value (contact email, user email, portal invite address, etc.). Rather
than generating fake/unreachable addresses, the user supplies **one real seed email they control** and
every generated record gets a unique `+alias` variant of that same address — so all test-data mail still
lands in the user's own inbox (most mail providers, including Outlook/Exchange and Gmail, deliver
`local+anything@domain` to `local@domain`).

## When to ask for the seed email

Ask **once per authenticated generation session, before metadata discovery**. The seed email is mandatory
even when the initial tables do not contain an email column; related tables dynamically added during
relationship discovery may require one. Do not proceed past the dv-connect environment-selection gate
without it.

Prompt (keep it short, one line):

> "These tables include an email column. What seed email should I alias records from (e.g.
> `jane.doe@contoso.com`)? I'll append a unique `+id` per record."

Keep it in the current agent session and embed it in the approval workbook metadata. If the user reconnects
or selects a different environment, ask for the email again.

## The transform

Split the seed email at the last `@`:

```python
def alias_email(seed_email: str, unique_id: str) -> str:
    local, _, domain = seed_email.rpartition("@")
    # strip any pre-existing +tag on the seed itself so aliases don't nest (a+b+c@domain)
    local = local.split("+", 1)[0]
    return f"{local}+{unique_id}@{domain}"
```

`unique_id` is intentionally compact and uses only lowercase ASCII letters, digits, and hyphens. It contains
a two-character table code, three-character run hash, base-36 row number, and one-character column code:
`{tt}-{run}-{row}-{c}` (example: `jane.doe+co-a7f-01-1@contoso.com`). This remains unique and traceable
without producing overly long addresses.

## Multiple email columns on the same table

If a table has multiple email columns, the final one-character column code differs so aliases do not collide.

## Safety rules

- Never fabricate a second, unrelated domain — only the one seed address the user provided is used as the
  base for every aliased email in the run.
- Validate the seed as an ASCII email, strip any existing `+tag`, sanitize the generated identifier, and
  enforce the 64-character local-part and 254-character full-address limits.
- Don't silently reuse a seed email from a previous unrelated project/session without confirming it still
  applies — cache per-session only, not persisted across sessions by default. If the user wants it
  persisted (e.g., for recurring demo refreshes), store it only where they explicitly ask (e.g., a local
  `.env`/config file they control), never hardcoded into the skill itself.
- Treat the seed email itself as user-provided data, not a secret, but still avoid echoing it in logs more
  than necessary (one confirmation line is enough).
