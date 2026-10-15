# Replicate: a benchmark for discovering automatable browser workflows

Watch someone work in the browser, find the procedures they keep repeating, and
propose each as an automation ("Replicate this"). This repo is the benchmark and
evaluation harness for that discovery step, built only from public data:

- **Sessions** are made by chaining short [Multimodal-Mind2Web](https://huggingface.co/datasets/osunlp/Multimodal-Mind2Web)
  tasks (CC BY 4.0) into 15-50 minute work sessions: 3-5 repeated travel workflows
  with 2-6 instances each, travel one-offs, non-travel distractors, and interrupted
  instances. Two tiers share the same tasks: **easy** (`s001`-`s008`, one task at a
  time) and **hard** (`h001`-`h008`, the user multitasks across 2-3 open tasks, so
  instances are interleaved with unrelated work and pauses give no boundaries away).
- **Ground truth comes from the chaining**: every cluster of same-template tasks
  repeated in a session is one opportunity, its task spans are the evidence, and
  everything else is a negative. No hand labelling.
- **Models** (a no-LLM heuristic and Claude Haiku/Sonnet/Opus via the `claude` CLI)
  propose ranked opportunities with time-span evidence from the browser signals
  only: DOM action trace, screenshots, or both.
- **The scorer** reports Precision@5, Recall@10, evidence IoU, instance F1,
  false positives on negatives, and an LLM-judged reconstruction/feasibility score.

Results and discussion: **[WRITEUP.md](WRITEUP.md)** · table: [data/results/results.md](data/results/results.md) · chart: [data/results/chart.png](data/results/chart.png)

## Quickstart

Needs [uv](https://docs.astral.sh/uv/). Nothing below needs an API key.

Run everything from this folder. The `replicate` package is split over three source folders, so set the path once:

```sh
export PYTHONPATH=data-generation:evaluation:visualization
uv sync
uv run pytest                                   # scorer, checker, composer tests
uv run python -m replicate.viewer && open viewer.html   # browse all 16 sessions
uv run python -m replicate.viewer --template dashboard.html --out dashboard.html && open dashboard.html   # performance chart + multitask run explorer

# Re-score the checked-in predictions offline (no model calls):
REPLICATE_OFFLINE=1 uv run python -m replicate.evaluate
```

Full rebuild, raw dataset to chart (downloads ~6.5 GB of parquet row groups over
HTTP range reads, keeps ~100 MB; LLM steps use the logged-in `claude` CLI and are
cached in `data/llm_cache/`):

```sh
./run_all.sh                      # SESSIONS=8 MODELS=heuristic,haiku,sonnet,opus
```

## Pipeline

| Step | Module | Output |
| --- | --- | --- |
| 1. Index Mind2Web (metadata columns only) | `replicate.data.index` | `data/index/tasks.jsonl` (2,022 tasks) |
| 2. Cluster travel tasks into per-site workflow templates (Sonnet, structured output) | `replicate.data.templates` | `data/index/templates.json` |
| 3. Compose sessions (easy + hard tier): pick workflows, one-offs, distractors, interleaving, synthetic timing | `replicate.compose` | `data/plans/{s,h}NNN.json` |
| 4. Fetch screenshots + DOM for the actions used | `replicate.data.fetch` | `data/raw/` |
| 5. Export bundles (schema v0.2) + ground truth, validate | `replicate.export`, `replicate.check_bundle` | `data/sessions/`, `data/ground_truth/` |
| 6. Run models, score, judge | `replicate.evaluate` | `data/results/` |
| 7. Chart, viewer | `replicate.report`, `replicate.viewer` | `data/results/chart.png`, `viewer.html`, `dashboard.html` |

## Layout

```
data-generation/replicate/   steps 1-5: data/ (index, templates, fetch), compose, export, check_bundle
evaluation/replicate/        step 6: models, score, judge, evaluate; llm.py wraps `claude -p`
visualization/replicate/     step 7: report (chart), viewer (viewer.html and dashboard.html templates)
data/
  sessions/{s,h}NNN/         all 16 bundles
    manifest.json
    traces/dom.jsonl         one action per line: t, site, op, value, element role/text/bbox
    screenshots/*.jpg        1280x800 viewport before each action
    dom/*.html.gz            cleaned DOM snapshot before each action
  ground_truth/{s,h}NNN.json kept outside the bundles
  schemas/                   JSON Schemas: bundle manifest (v0.2, browser-only), DOM event,
                             ground truth, prediction
  index/                     task index + templates (so steps 1-2 can be skipped)
  plans/                     composed sessions (seeded, reproducible)
  llm_cache/                 every model reply, keyed by prompt+images hash
  results/
    predictions/<model>__<input>/{s,h}NNN.json
    scores.json              per-session metrics, judge outputs
    results.md, chart.png
    judge_agreement_sample.csv   25 judged items with a blank column for a human pass
tests/
viewer.html, dashboard.html  generated pages (not checked in)
```

## Session viewer

`uv run python -m replicate.viewer` writes `viewer.html`. `--template dashboard.html --out dashboard.html` writes a two-tab page instead (design brief and reference images: [visualization/design/](visualization/design/README.md)). **Performance** is a vertical bar chart for one model at a time (its three inputs plus the heuristic baseline, best on the left), on any metric and tier, with the full table below. **Dataset explorer** shows each multitask run: the workflows a chosen detector found (real, wrong, missed) and the recorded demonstration as a feed of screenshots and actions. `dashboard.html#explorer` opens the second tab. It shows each session's
timeline coloured by ground-truth workflow (one-offs grey, distractors hatched,
interrupted instances as split segments), lets you pick a model/input to overlay
its ranked predictions as lanes (unmatched ones outlined in red), and plays the
screenshot sequence with the acted-on element boxed. Arrow keys step, space plays.

## LLM calls

`evaluation/replicate/llm.py` shells out to `claude -p` (no API key; uses the CLI's login).
JSON replies use `--json-schema` structured output; images go inline as base64
content blocks over `--input-format stream-json`. Calls run in an empty temp dir
so no project instructions leak into prompts, and every reply is cached on disk.
