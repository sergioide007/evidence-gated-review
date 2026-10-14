# AI / multi-agent checklist
- Why is this an agent and not a function or fixed workflow? What decision does the model own?
- Single-agent baseline measured? Sequential/tightly coupled tasks often degrade with more agents.
- Max iterations, token budget, wall-clock limit, kill switch; behavior on non-convergence.
- Handoffs: context transferred is validated by schema; each agent sees only what it needs.
- Independent verification: external evidence (tests, tools) rather than a second opinion from the same model family.
- Memory: scoped, expiring, access-controlled; no cross-tenant or cross-repo leakage.
- Evaluation: seeded defects, regression suite, cost/latency tracked; judge bias considered.
- Observability: per-decision traces, tool-call audit log, cost attribution per tier.
