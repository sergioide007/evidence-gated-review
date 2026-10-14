# AGENTS.md

Project rules for any coding agent (tool-agnostic). Keep this file short; task-specific procedures live in `skills/`.

## Commands
- Tests: `PYTHONPATH=src python -m unittest discover -s tests -v` (offline, no API keys)
- Demo: `make demo` · Injection demos: `make demo-inyeccion`, `make demo-obediencia-valida` · Audit: `make audit-check`
- Seeded eval (scripted responses): `make eval`

## Conventions
- Python >= 3.11, standard library only. No new dependencies without discussion.
- Model IDs and prices live ONLY in the `config*.toml` files. Never hard-code them in `src/`.
- New provider = one entry in `PROVIDERS` (`clients.py`) + tests with the fake HTTP layer. API keys come from env/.env only: never print, log, or put them in error messages (`scrub`).
- Findings must be evidence-anchored; severity is computed (`severity.py`), never taken from a model.
- A lens with invalid output is `failed`, never "no findings". Do not weaken this.
- An injection signal (`injection.py`) that no VERIFIED finding reports makes coverage incomplete, even if every lens
  returned valid JSON. Do not weaken this either.
- Repository text must never be able to close the `<file>` data block (`normalizer.neutralize`).
- Every pipeline decision (accept/reject/severity/verdict) is written to the audit chain (`audit.py`). The log stores
  hashes of prompts and responses, never source code. Do not log secrets.
- Pipeline phases are explicit (`phases.py`); do not add a path that reaches the verdict without VERIFY.
- Do not give review code write access to the target repo, network, or credentials.

## Skills
- `skills/critical-technical-review/` follows the Agent Skills format (SKILL.md + references/scripts/assets).

## Do not
- Commit API keys. Add a test for every new guardrail. Replace deterministic checks with LLM opinions.
