#!/usr/bin/env bash
# Raw dataset -> benchmark -> predictions -> scores -> chart -> viewer, in one go.
# Every step is idempotent and cached; re-running only does what is missing.
set -euo pipefail
cd "$(dirname "$0")"
# One `replicate` package split over three source folders.
export PYTHONPATH=data-generation:evaluation:visualization
SESSIONS=${SESSIONS:-8}
MODELS=${MODELS:-heuristic,haiku,sonnet,opus}

[ -f data/index/tasks.jsonl ]     || uv run python -m replicate.data.index        # metadata only, ~1 min
[ -f data/index/templates.json ]  || uv run python -m replicate.data.templates    # 36 LLM calls, cached
uv run python -m replicate.compose --sessions "$SESSIONS"                          # deterministic (seeded)
uv run python -m replicate.data.fetch                                              # only missing frames
uv run python -m replicate.export
uv run python -m replicate.check_bundle data/sessions/* --gt-dir data/ground_truth
uv run python -m replicate.evaluate --models "$MODELS"
uv run python -m replicate.report
uv run python -m replicate.viewer
