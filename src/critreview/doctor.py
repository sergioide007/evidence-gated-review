"""`critreview doctor`: check the configuration against the real providers WITHOUT spending tokens.

For every tier: provider known? API key present (only the variable NAME is ever shown)? model id listed by the
provider (GET /models)? Plus provider-specific data/privacy notes and, with a target, whether the review context
fits the tier's per-minute token allowance.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import re

from .clients import PROVIDERS, build_client, find_key, list_models
from .config import Config
from .normalizer import build_snapshot, estimate_tokens
from .lenses import SYSTEM_PROMPT


# Ids that usually denote realtime/audio/image/embedding models, which do not serve plain text chat completions.
_NON_CHAT = re.compile(r"(^|[-_/.])(live|realtime|audio|tts|stt|transcribe|whisper|embedding|embed|image|imagen|veo|vision-only)([-_/.]|$)", re.I)


def looks_non_chat(model: str) -> bool:
    return bool(_NON_CHAT.search(model))


@dataclass
class Check:
    where: str
    ok: bool
    message: str


def _default_prober(provider: str, model: str) -> str:
    comp = build_client(provider, model).complete("You are a connectivity probe.", "Reply with the single word: OK", 256)
    return comp.text


def run_doctor(cfg: Config, target: Path | None = None,
               lister: Callable[[str], list[str]] = list_models, probe: bool = False,
               prober: Callable[[str, str], str] = _default_prober) -> list[Check]:
    out: list[Check] = []
    cache: dict[str, list[str] | str] = {}
    for name, t in cfg.tiers.items():
        where = f"tiers.{name} ({t.provider}:{t.model})"
        if t.provider == "anthropic":
            out.append(Check(where, True, "anthropic: not checked by doctor (no /models probe implemented)"))
            continue
        key, env = find_key(t.provider)
        if not key:
            out.append(Check(where, False, f"{env} is not set (export it or put it in .env)"))
            continue
        if t.provider not in cache:
            try:
                cache[t.provider] = lister(t.provider)
            except Exception as e:  # network / auth / provider error: say so, never print the key
                cache[t.provider] = f"{type(e).__name__}: {e}"
        got = cache[t.provider]
        if isinstance(got, str):
            out.append(Check(where, False, f"could not list models: {got}"))
        elif t.model in got:
            out.append(Check(where, True, f"key found ({env}), model available"))
        else:
            close = [m for m in got if t.model.split("/")[-1].split("-")[0] in m][:5]
            hint = f" Similar ids: {', '.join(close)}" if close else ""
            out.append(Check(where, False, f"model id not listed by {t.provider}.{hint} "
                                           f"(run: critreview models --provider {t.provider})"))
        if looks_non_chat(t.model):
            out.append(Check(where, False, "the model id looks like a realtime/audio/image/embedding model, which "
                                           "usually does NOT serve text chat completions; pick a text model from "
                                           f"`critreview models --provider {t.provider}` (confirm with --probe)"))
        note = PROVIDERS[t.provider].data_note
        if note:
            out.append(Check(where, True, f"note: {note}"))
    if probe:
        seen: set[tuple[str, str]] = set()
        for name, t in cfg.tiers.items():
            if t.provider == "anthropic" or (t.provider, t.model) in seen:
                continue
            seen.add((t.provider, t.model))
            where = f"probe {t.provider}:{t.model}"
            try:
                reply = prober(t.provider, t.model)
                if reply.strip():
                    out.append(Check(where, True, "answered a chat request"))
                else:
                    out.append(Check(where, False, "empty answer: the model may spend its whole output budget on hidden "
                                                   "reasoning, or it does not serve chat; raise max_output_tokens or "
                                                   "pick another model"))
            except Exception as e:
                out.append(Check(where, False, f"call failed: {type(e).__name__}: {e}"))
    if target is not None:
        snap = build_snapshot(Path(target), cfg.max_context_tokens)
        mid = cfg.tiers["mid"]
        need = estimate_tokens(snap.render()) + estimate_tokens(SYSTEM_PROMPT) + 400 + mid.max_output_tokens
        if mid.tpm and need > mid.tpm:
            out.append(Check("context", False, f"~{need} tokens per lens call > tiers.mid.tpm={mid.tpm}: "
                                               f"lower review.max_context_tokens"))
        else:
            out.append(Check("context", True, f"~{need} tokens per lens call"
                                              + (f" (limit {mid.tpm}/min)" if mid.tpm else "")))
        calls = len(cfg.lenses) + 1 + 1
        out.append(Check("context", True, f"rough call count: {len(cfg.lenses)} lenses + adjudications + 1 summary "
                                          f"(>= {calls}); budget max_calls={cfg.budget.max_calls}"))
    return out
