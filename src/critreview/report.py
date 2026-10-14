"""Render the report as Markdown + a machine-readable risk register (JSON)."""
from __future__ import annotations

import json
from pathlib import Path

from .workflow import Report

VERDICT_TEXT = {
    "ACEPTABLE": "ACEPTABLE (con cobertura completa y sin hallazgos altos/críticos verificados)",
    "CON_RIESGO": "CON RIESGO (al menos un hallazgo alto verificado)",
    "FRAGIL": "FRÁGIL (al menos un hallazgo crítico verificado)",
    "NO_VIABLE": "NO VIABLE (tres o más hallazgos críticos verificados)",
    "INCOMPLETO": "INCOMPLETO (sin hallazgos altos/críticos, pero la cobertura no fue completa: no se puede afirmar que esté limpio)",
}


def _code(text: str, limit: int = 160) -> str:
    """Inline-code a model/repo-supplied string without letting backticks or newlines break the layout."""
    return "`" + " ".join(text.strip()[:limit].split()).replace("`", "'") + "`"


def render_markdown(r: Report) -> str:
    L = [f"# Revisión técnica crítica\n", f"**Objetivo:** `{r.target}`  ",
         f"**Veredicto (regla determinista):** {VERDICT_TEXT.get(r.verdict, r.verdict)}\n"]
    if r.incomplete:
        L.append("> ⚠ **Cobertura incompleta**: " + (r.partial_reason or "una o más lentes no devolvieron salida válida") + "\n")
    if r.uncovered_signals:
        L.append("> ⚠ **Señales de inyección sin reportar** (ninguna lente las señaló; una lente pudo obedecerlas): "
                 + ", ".join(f"`{x['file']}:{x['line']}`" for x in r.uncovered_signals) + "\n")
    if r.summary:
        L.append(f"**Resumen (redactado por el modelo, no cambia el veredicto):** {r.summary}\n")
    fn = r.funnel()
    L.append("## Embudo de verificación\n")
    L.append(f"- Propuestos por los modelos: **{fn['proposed']}**")
    L.append(f"- Rechazados por evidencia inexistente: **{fn['rejected_no_evidence']}**")
    L.append(f"- Rechazados por esquema inválido: **{fn['rejected_schema']}**")
    L.append(f"- Duplicados fusionados: **{fn['merged_duplicates']}**")
    L.append(f"- Hallazgos verificados: **{fn['verified']}**\n")
    L.append("## Cobertura\n\n| Lente | Estado | Hallazgos crudos | Errores |\n|---|---|---|---|")
    for lr in r.lens_results:
        L.append(f"| {lr.lens} | {lr.status} | {lr.raw_count} | {'; '.join(lr.errors) or '-'} |")
    if r.omitted_files:
        L.append(f"\nArchivos omitidos del contexto: {len(r.omitted_files)}"
                 + (" (**contexto truncado**)" if r.context_truncated else ""))
    L.append(f"\n## Hallazgos verificados ({len(r.findings)})\n")
    for f in r.findings:
        L.append(f"### [{f.severity.upper()}] {f.title}  \n`{f.id}` · área: {f.area} · tipo: {f.finding_type} · lentes: {', '.join(f.lenses)}")
        for e in f.evidence:
            L.append(f"- Evidencia: `{e.file}:{e.start_line}-{e.end_line}` → {_code(e.quote)}")
        L += [f"- **Cómo falla:** {f.failure_mechanism}", f"- **Condición de activación:** {f.trigger}",
              f"- **Impacto ({f.impact_level}):** {f.impact_description}",
              f"- **Probabilidad:** {f.likelihood} · **Detectabilidad:** {f.detectability} · "
              f"**Severidad propuesta por el modelo:** {f.proposed_severity} → **calculada:** {f.severity}",
              f"- **Cómo se calculó la severidad:** " + " → ".join(f.severity_trace),
              f"- **Mitigación:** {f.recommendation}", f"- **Coste de la mitigación:** {f.tradeoff}"]
        if f.adjudication:
            L.append(f"- **Adjudicación (modelo fuerte):** {f.adjudication.get('decision')} — {f.adjudication.get('reason', '')}")
        if f.needs_human:
            L.append("- **Requiere revisión humana**")
        L.append("")
    L.append(f"## Rechazados antes del informe ({len(r.rejected)})\n")
    for x in r.rejected:
        L.append(f"- `{x['id']}` ({x['reason']}): {'; '.join(x['detail']) if isinstance(x['detail'], list) else x['detail']}")
    L.append("\n## Trazabilidad\n")
    L.append(f"- Huella del código revisado (SHA-256): `{r.snapshot_digest}`")
    L.append(f"- Cabeza de la cadena de auditoría: `{r.audit_head}` (publícala fuera del alcance de quien edita el log)")
    L.append("\n## Presupuesto\n\n```json\n" + json.dumps(r.budget, indent=2) + "\n```")
    return "\n".join(L) + "\n"


def write_outputs(r: Report, out: Path) -> tuple[Path, Path]:
    out.mkdir(parents=True, exist_ok=True)
    md, js = out / "review.md", out / "risk_register.json"
    md.write_text(render_markdown(r), encoding="utf-8")
    js.write_text(json.dumps(r.to_dict(), indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    return md, js
