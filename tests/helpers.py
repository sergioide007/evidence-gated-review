from pathlib import Path

from critreview.config import load_config

ROOT = Path(__file__).resolve().parent.parent
SUITE = ROOT / "evals" / "seeded"
CFG = ROOT / "config.example.toml"


def cfg():
    return load_config(CFG)
