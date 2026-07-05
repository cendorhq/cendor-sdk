---
name: namespace-guard
description: Verify the cendor PEP 420 namespace packaging is correct — there must be no src/cendor/__init__.py. Use before committing, building, or releasing, or whenever cross-package imports break.
---
# Namespace guard

`cendor-sdk` is one distribution contributing to the shared PEP 420 `cendor` namespace. It must own
`src/cendor/sdk/` **only** and never ship `src/cendor/__init__.py` — the #1 way multi-repo namespace
packages break.

```bash
# Must print NOTHING:
find src -path '*/cendor/__init__.py' -print
```

- If it prints any path → **delete that file.** A top-level `cendor/__init__.py` turns the implicit
  namespace into a regular package and silently breaks every other `cendor.<tool>` import.
- `src/cendor/sdk/__init__.py` SHOULD exist — only the `src/cendor/__init__.py` *level* is forbidden.
- The same check runs in CI (`.github/workflows/ci.yml`).

If it passes, report "namespace-guard: OK".
