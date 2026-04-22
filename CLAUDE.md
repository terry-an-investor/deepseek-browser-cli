# Claude Companion Notes

This file is intentionally short. It should not duplicate `README.md`.

## Read Order

1. `AGENTS.md` (canonical coding constraints)
2. Target source files and tests
3. `README.md` only when user-facing behavior/docs need updates

## Claude Execution Guidance

- Keep edits atomic and tied to one behavior change per patch when possible.
- Favor semantic selectors and page markers over CSS classes.
- In polling paths, optimize for fewer subprocess invocations first.
- Preserve compatibility assumptions used by `FakeA11y` test doubles.
- If behavior changes, add/update regression coverage before finishing.

## Delivery Standard

- Summarize what changed and why.
- State test commands executed and outcomes.
- Call out any residual risk or untested path explicitly.

## Note on Documentation

- `README.md` owns product overview, installation, and interface usage.
- `AGENTS.md` owns implementation constraints and engineering guardrails.
- `CLAUDE.md` owns Claude-specific workflow hints only.
