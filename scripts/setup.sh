#!/bin/bash
# Build the venv from scratch. openwakeword ships without its base feature
# models, so pip install alone leaves it broken -- they download separately.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

/opt/homebrew/opt/python@3.14/bin/python3.14 -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -r requirements.txt

.venv/bin/python -c "
import sys; sys.path.insert(0, 'src')
from wake import tflite_compat; tflite_compat.install()
import openwakeword.utils as u
u.download_models(model_names=[])
"

echo "setup complete"
