# Evaluation protocol: workflow detection

Written 2026-10-14, before any session in the test set was scored with a changed prompt.
Everything below is fixed; results that break a rule are reported as such, not re-run.

## Why this exists

The prompt changes of 2026-10-14 (commit 293c093) were tuned and reported on the same
25 sessions, bundled four changes, compared inputs on one run each, and relabelled agent
ground truth after looking at model failures. Those numbers (Sonnet 0.72 solved) are
optimistic. This protocol re-measures each change on a dev set and reports once on a
test set.

## Sessions

`evaluation/split.json`.

| Set | Human multitask (Mind2Web) | Agent-recorded |
|---|---|---|
| dev (16) | h001-h008 | 8 of agent01-17 |
| test (17) | h009-h016 | the other 9 |

- **h009-h016 are new**: composed with `compose --seed 1 --sessions 16 --tiers hard`
  (h001-h008 come out byte-identical, and the composer never reuses a Mind2Web task
  within a tier). No prompt has seen them. Workflow *templates* can repeat across
  sessions; tasks, values and interleavings do not.
- **The agent test sessions are contaminated**: all 17 agent sessions were looked at
  while the current prompt was written. They are held out from every decision from
  here on, and reported separately. Primary claims rest on h009-h016.

## Model, inputs, repeats

- Sonnet (`claude` CLI alias) for everything. No Opus run without a cost estimate first.
- Inputs: action log (`trace`) and action log + screenshots (`both`). Screenshots alone are dropped.
- 3 runs per (session, variant, input), REPLICATE_RUN=1..3 (fresh calls, separately cached).

## Metrics

- **Primary: work sessions solved** (score.py): every real workflow matched at evidence
  IoU >= 0.5 without padding, and no more proposals than real workflows.
  Per session, averaged over the 3 runs; then averaged over sessions.
- Secondary: Recall@10, Precision@5, occurrence F1, parameter recall; replay success (below);
  judge scores (Sonnet, 1-5) on the final configuration only.

## Ablations (dev only, action log)

Variants via REPLICATE_PROMPT_FEATURES: `none` (the original prompt, byte-identical:
verified by hitting its cached calls), `actions`, `whole`, `short`, and `actions,whole,short`.
Every variant gets fresh runs 1-3. The ambiguous relabel is
a scoring change, measured by re-scoring every variant with and without it (free).

Effect of a variant = mean over dev sessions of (variant - none), with a paired bootstrap
95% CI over sessions (4,000 resamples, seed 0).

**Rule A (configuration).** The final prompt is the dev variant with the highest mean solved.
If another variant is within 0.02 and uses fewer features, take that one.

## Label audit

Blind relabelling of a sample: every agent session with an `ambiguous` label, agent08
(long workflow granularity) and 3 human dev sessions. Labellers see only the session's
action trace and screenshots, never scenario scripts, ground truth or model output.
Agreement with the current ground truth: Cohen's kappa at the action level
(in a repeated workflow vs not) and workflow-level match counts.

**Rule B (ambiguous labels).** The ambiguous label stays if the auditors call at least half of
the ambiguous workflow groups a repeated workflow; otherwise those actions revert to
one-off negatives for test scoring. Other disagreements are listed, and labels are only
changed where the audit shows a clear error in the scenario-derived ground truth.

## Test (once)

The Rule A prompt, both inputs, 3 runs, 17 test sessions, scored once.

**Rule C (inputs).** Action log vs action log + screenshots: exact McNemar test on the
solved outcome over (session, run) pairs, two-sided, alpha 0.05; also the paired bootstrap of
the per-session difference. A difference is claimed only if p < 0.05.

## Replay (execution check)

For each workflow the final configuration (action log, run 1) correctly detects in an agent
test session, a browser-use agent on a boat.dev sandbox gets only the model's proposed steps
and the parameter values of the model's first instance, and does the task on the live sites,
stopping before any final confirm button. The replay is recorded with the same recorder.

Compared with the ground-truth run whose actions the model's first instance overlaps most:
- site order: longest common subsequence of the deduplicated site sequences / GT length
- typed values: share of the GT run's typed values (lower-cased, trimmed) that the replay also typed

**Rule D (replay success).** Success = the agent finished the script, site order >= 0.8,
typed values >= 0.5. Mind2Web sessions are not replayed (2023 snapshots; the live sites changed).

## What would change the conclusions

- Rule A picks a different configuration than the current prompt: the dashboard uses Rule A's.
- Rule B fails: the agent numbers are reported without the relabel.
- Test results that disagree with dev are reported as they are.
