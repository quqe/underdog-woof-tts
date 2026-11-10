#!/usr/bin/env python3
"""Re-download model code + weights from the Hugging Face Hub into this project.

Only needed if woof_int8.npz or underdog_woof/ is missing (both are already vendored here).

    python scripts/fetch_model.py
"""
import os
import pathlib
import sys

try:
    from huggingface_hub import snapshot_download
except ImportError:
    sys.exit("pip install huggingface_hub  # or: uv run --with huggingface_hub python scripts/fetch_model.py")

ROOT = pathlib.Path(__file__).resolve().parent.parent
REPO = "ConwayResearch/Underdog-Woof"

snapshot_download(
    repo_id=REPO,
    local_dir=ROOT,
    allow_patterns=["woof_int8.npz", "underdog_woof/*", "LICENSE", "NOTICE"],
)
print(f"model files are in {ROOT}")
