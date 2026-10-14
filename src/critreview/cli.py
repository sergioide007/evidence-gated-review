from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .audit import AuditLog, verify_chain
from .clients import KNOWN_PROVIDERS, PROVIDERS, FakeClient, ProviderError, build_client, list_models
from .doctor import run_doctor
from .envfile import load_env_file
from .config import load_config
from .evals import score
from .report import write_outputs
from .worktree import ephemeral_worktree
from .workflow import run_review


def _clients(cfg, fake_path: str | None):
    if fake_path:
        fc = FakeClient(json.loads(Path(fake_path).read_text(encoding="utf-8")))
        return {name: fc for name in cfg.tiers}
    return {name: build_client(t.provider, t.model) for name, t in cfg.tiers.items()}


def summary_text(rep) -> str:
    from collections import Counter
    fn = rep.funnel()
    sev = Counter(f.severity for f in rep.findings)
    sev_txt = ", ".join(f"{sev[s]} {s}" for s in ("critical", "high", "medium", "low") if sev[s]) or "ninguno"
    def row(label, value):
        return f"  {label:<36}: {value}"

    lines = [
        "== Embudo de verificación ==",
        row("Propuestos por los modelos", fn["proposed"]),
        row("Rechazados (cita inexistente)", fn["rejected_no_evidence"]),
        row("Rechazados (esquema inválido)", fn["rejected_schema"]),
        row("Duplicados fusionados", fn["merged_duplicates"]),
        row("Hallazgos verificados", f"{fn['verified']}  ({sev_txt})"),
        "",
    ]
    for x in rep.rejected:
        why = "cita inexistente" if x["reason"] == "evidence" else "esquema inválido"
        lines.append(f"  ✗ {x['id']}: {why}")
    failed = [r.lens for r in rep.lens_results if r.status != "ok"]
    lines.append("")
    gaps = [f"{x['file']}:{x['line']}" for x in rep.uncovered_signals]
    if failed:
        cov = "INCOMPLETA - lente(s) fallida(s): " + ", ".join(failed)
    elif gaps:
        cov = "INCOMPLETA - señal de inyección sin reportar en " + ", ".join(gaps)
    else:
        cov = "COMPLETA"
    lines.append(f"  Cobertura: {cov}")
    lines.append(f"  Veredicto (regla determinista): {rep.verdict}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="critreview", description="Evidence-gated technical review")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="review a directory or a git ref (via an ephemeral read-only worktree)")
    r.add_argument("--target", required=True)
    r.add_argument("--ref", help="git ref: review that commit in an ephemeral worktree (target must be a repo)")
    r.add_argument("--config", required=True)
    r.add_argument("--out", default="out")
    r.add_argument("--fake", help="JSON of scripted responses (offline demo/tests; no API calls)")
    r.add_argument("--fail-on", default="", help="comma list of verdicts that make the exit code 2 "
                   "(e.g. FRAGIL,NO_VIABLE,INCOMPLETO). Lets CI gate on the deterministic verdict.")
    for sp in (r,):
        sp.add_argument("--env-file", default=".env", help="load API keys from this file if it exists (default .env)")
    m = sub.add_parser("models", help="list the model ids your key can use (no tokens spent)")
    m.add_argument("--provider", required=True, choices=sorted(PROVIDERS))
    m.add_argument("--env-file", default=".env")
    d = sub.add_parser("doctor", help="check keys, model ids and limits of a config WITHOUT spending tokens")
    d.add_argument("--config", required=True)
    d.add_argument("--target", help="optional: also check the review context fits the per-minute token limit")
    d.add_argument("--probe", action="store_true",
                   help="make ONE tiny real call per distinct model to prove it answers chat requests (spends a few tokens)")
    d.add_argument("--env-file", default=".env")
    v = sub.add_parser("audit-verify", help="recompute the hash chain of an audit.jsonl")
    v.add_argument("path")
    e = sub.add_parser("eval", help="score a review against seeded defects")
    e.add_argument("--suite", required=True, help="dir with project/, expected.json, fake_responses.json")
    e.add_argument("--config", required=True)
    e.add_argument("--live", action="store_true", help="use real providers instead of fake_responses.json")
    e.add_argument("--env-file", default=".env", help="load API keys from this file if it exists (default .env)")
    e.add_argument("--out", default="out-eval", help="where to save the report, risk register and audit log of the run")
    a = ap.parse_args(argv)

    if getattr(a, "env_file", None):
        load_env_file(Path(a.env_file))   # names only are ever tracked; values are never printed
    if a.cmd == "models":
        try:
            for mid in list_models(a.provider):
                print(mid)
        except (ProviderError, Exception) as e:
            print(f"error: {type(e).__name__}: {e}", file=sys.stderr)
            return 1
        return 0
    if a.cmd == "doctor":
        checks = run_doctor(load_config(Path(a.config)), Path(a.target) if a.target else None, probe=a.probe)
        for c in checks:
            print(f"{'OK  ' if c.ok else 'FAIL'} {c.where}: {c.message}")
        return 0 if all(c.ok for c in checks) else 1
    if a.cmd == "audit-verify":
        ok, msg = verify_chain(Path(a.path))
        print(("OK: " if ok else "TAMPERED: ") + msg)
        return 0 if ok else 3
    cfg = load_config(Path(a.config))
    if a.cmd == "run":
        clients = _clients(cfg, a.fake)
        audit = AuditLog(Path(a.out) / "audit.jsonl")
        if a.ref:
            with ephemeral_worktree(Path(a.target), a.ref) as wt:
                rep = run_review(wt, cfg, clients, audit)
        else:
            rep = run_review(Path(a.target), cfg, clients, audit)
        md, js = write_outputs(rep, Path(a.out))
        print(summary_text(rep))
        print(f"\nInforme: {md}\nRegistro de riesgos: {js}\nAuditoría: {audit.path}  (cabeza: {audit.head[:16]}…)")
        gate = {x.strip().upper() for x in a.fail_on.split(",") if x.strip()}
        return 2 if rep.verdict in gate else 0
    suite = Path(a.suite)
    clients = _clients(cfg, None if a.live else str(suite / "fake_responses.json"))
    audit = AuditLog(Path(a.out) / "audit.jsonl")
    rep = run_review(suite / "project", cfg, clients, audit)
    res = score(rep, suite / "expected.json")
    write_outputs(rep, Path(a.out))
    (Path(a.out) / "eval_result.json").write_text(json.dumps(
        {**res.to_dict(), "live": bool(a.live), "models": {n: f"{t.provider}:{t.model}" for n, t in cfg.tiers.items()},
         "audit_head": audit.head}, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(res.to_dict(), indent=2, ensure_ascii=False))
    print(f"\nGuardado en {a.out}/ (review.md, risk_register.json, eval_result.json, audit.jsonl)", file=sys.stderr)
    if res.unlabeled:
        print("Hallazgos verificados que NO están en la verdad de referencia (juzga a mano si son reales):", file=sys.stderr)
        for u in res.unlabeled:
            print(f"  - {u}", file=sys.stderr)
    if not res.valid:
        print("\nINVALID RUN: every lens failed. Numbers above are NOT a measurement of the method.", file=sys.stderr)
        for line in res.lens_errors:
            print(f"  - {line}", file=sys.stderr)
        return 4
    return 0


if __name__ == "__main__":
    sys.exit(main())
