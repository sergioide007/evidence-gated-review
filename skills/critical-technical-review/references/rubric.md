# Rubric

## Severity (computed, not asserted)

| impact \ likelihood | low | medium | high |
|---|---|---|---|
| critical | high | critical | critical |
| high | medium | high | critical |
| medium | low | medium | high |
| low | low | low | medium |

Bump one level if `detectability = low` (hard to notice in time). Cap `inference` findings at `high`.

## Evidence

- File path as shown in the snapshot, line range, EXACT quote (>= 8 chars after whitespace normalization).
- Accept +/-3 lines of drift; reject quotes that do not exist in the file.

## Verdict (deterministic)

- `NO_VIABLE`: >= 3 verified critical. `FRAGIL`: >= 1 critical. `CON_RIESGO`: >= 1 high. `ACEPTABLE`: none of the above AND full lens coverage.
- No findings with incomplete coverage => `INCOMPLETO`, never `ACEPTABLE`.

## Anti-patterns to avoid in a review

- "Be harsh" as a substitute for method (produces manufactured criticism).
- Same-model reviewer and verifier (correlated errors, self-preference bias).
- Verifier = another LLM opining without external evidence.
- Whole-repo dumps as context (retrieve what the lens needs; models use mid-context information poorly).
