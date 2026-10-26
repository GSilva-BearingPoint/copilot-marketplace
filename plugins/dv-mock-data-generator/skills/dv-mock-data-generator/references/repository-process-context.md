# Repository Process Context

Ask on every new generation run whether a repository should provide context for custom Dataverse processes.
The user may choose the current repository, provide a local path/GitHub repository, or continue without one.

## Read-only inspection

Do not build, load, or execute repository code. Inspect source and configuration only:

- C# `IPlugin` implementations, plugin base classes, custom workflow activities, custom APIs.
- Plugin registration/export data (messages, stages, filtering attributes, images).
- JavaScript/TypeScript web resources that default, validate, or calculate Dataverse fields.
- Power Automate/Logic Apps definitions stored in solutions.
- Constants/enums, early-bound models, test builders/fixtures, and unit/integration tests.

Extract table/column logical names, default values, valid choices, conditional requirements, formulas,
status transitions, and lookup restrictions. Correlate every logical name with live `dv-metadata`; repository
code may be stale or undeployed.

Show the extracted rules to the user and obtain confirmation before applying them.

## Safe rules file

Never execute expressions copied from the repository. Translate confirmed behavior to JSON:

```json
{
  "source": "C:\\repos\\contoso-dataverse-plugins",
  "notes": ["Extracted from ContactPreOperation and its tests."],
  "tables": {
    "contact": {
      "defaults": {
        "preferredcontactmethodcode": 2
      },
      "allowed_values": {
        "new_customertier": [100000000, 100000001]
      },
      "templates": {
        "description": "Test contact {firstname} {lastname}"
      },
      "calculations": [
        {
          "target": "new_estimatedannualvalue",
          "operation": "multiply",
          "sources": ["new_monthlyvalue", "new_contractmonths"]
        }
      ],
      "relationships": {
        "new_customerid": {
          "target_table": "account"
        }
      }
    }
  }
}
```

Supported calculations are `add`, `subtract`, `multiply`, `divide`, and `concat`. Referenced fields must be
writable structural columns in the live environment. Missing/non-writable columns stop generation.

`relationships` may select a single target for an optional or polymorphic lookup. The selected target must
match the live relationship metadata. A selected target that is not otherwise requested is added as an
implicit generated dependency and reported to the user with its reason.

Pass the confirmed file with `--context-rules`. The workbook README and approval summary identify the
repository source; if the user declines repository context, they explicitly show `none provided`.
