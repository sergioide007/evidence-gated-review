.PHONY: models-gemini models-groq models-deepseek models-openai doctor-free doctor-multi doctor-gemini live-gemini live-gemini-x3 live-free test demo demo-inyeccion demo-obediencia-valida audit-check eval diagram
export PYTHONPATH := src

test:
	python -m unittest discover -s tests -v

demo:
	python -m critreview run --target evals/seeded/project --config config.example.toml \
		--fake evals/seeded/fake_responses.json --out out

demo-inyeccion:
	python -m critreview run --target evals/seeded/project --config config.example.toml \
		--fake evals/seeded/fake_responses_injected.json --out out-inyeccion

# El caso que el esquema NO detecta: el modelo obedece la trampa y devuelve un JSON vacío y válido.
demo-obediencia-valida:
	python -m critreview run --target evals/seeded/project --config config.example.toml \
		--fake evals/seeded/fake_responses_obedient_valid.json --out out-obediencia

# Recalcula la cadena de hashes de la auditoría de `make demo`.
audit-check: demo
	python -m critreview audit-verify out/audit.jsonl

eval:
	python -m critreview eval --suite evals/seeded --config config.example.toml

# Requiere mermaid-cli (mmdc) con un Chrome disponible. Regenera docs/architecture-animated.svg
diagram:
	mmdc -i docs/architecture.mmd -o /tmp/architecture.svg -b white
	python tools/animate_mermaid_svg.py /tmp/architecture.svg docs/architecture-animated.svg

# ---- Con tus claves reales (lee .env). No gastan tokens: solo listan modelos / comprueban la config ----
models-gemini:
	python -m critreview models --provider gemini
models-groq:
	python -m critreview models --provider groq
models-deepseek:
	python -m critreview models --provider deepseek
models-openai:
	python -m critreview models --provider openai
doctor-free:
	python -m critreview doctor --config config.free.toml --target evals/seeded/project
doctor-multi:
	python -m critreview doctor --config config.multi.toml --target evals/seeded/project

# Primera medición real (GASTA cuota): recall/precisión con modelos de verdad sobre los defectos sembrados.
live-free:
	python -m critreview eval --suite evals/seeded --config config.free.toml --live

doctor-gemini:
	python -m critreview doctor --config config.gemini-only.toml --target evals/seeded/project --probe
live-gemini:
	python -m critreview eval --suite evals/seeded --config config.gemini-only.toml --live
live-gemini-x3:
	python -m critreview eval --suite evals/seeded --config config.gemini-only.toml --live --repeat 3
