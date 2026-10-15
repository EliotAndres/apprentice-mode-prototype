# Workflow detection: dev ablations and held-out test

Sonnet throughout. Protocol fixed in `PROTOCOL.md` (commit d4f99c3) before any test scoring.
Solved = every real workflow matched at evidence IoU >= 0.5, no more proposals than real workflows.

## 1. Protocol

- dev: h001-h008 + 8 agent sessions; test: h009-h016 (new, never seen by any prompt) + 9 agent sessions (seen during earlier tuning, so reported separately).
- 3 fresh runs per (session, variant, input). Rule A picks the prompt, Rule B the ambiguous label, Rule C the input comparison (exact McNemar, alpha 0.05).

## 2. Ablations (dev, action log, 16 sessions x 3 runs)

| Variant | Solved | vs original [95% CI] | Solved without relabel |
|---|---|---|---|
| original prompt | 0.625 | - | 0.33 |
| + action numbers | 0.688 | +0.06 [-0.08, +0.23] | 0.35 |
| + whole-task rule | 0.625 | +0.00 [-0.15, +0.12] | 0.31 |
| + short-repeat rule | 0.542 | -0.08 [-0.21, +0.02] | 0.27 |
| all three | 0.708 | +0.08 [-0.04, +0.25] | 0.44 |

- No prompt change is individually significant. The ambiguous relabel (a scoring change) is the
  largest effect: 0.33 -> 0.62 on the original prompt.
- Rule A picked "all three" (0.708 vs 0.688: a 0.021 gap against a 0.02 margin, effectively a tie).
- Secondary: Recall@10 ~0.95 everywhere; whole-task rule P@5 +0.04 [+0.01, +0.07]; all three occurrence F1 +0.05 [+0.01, +0.09].

## 3. Error taxonomy (counts over session-runs)

Dev, action log, 48 session-runs:

| Variant | Grounding | Noise extra | Missed short | Missed | Ambiguous extra | Sub-step extra |
|---|---|---|---|---|---|---|
| original | 10 | 10 | 6 | 3 | 3 | 0 |
| + action numbers | 1 | 11 | 6 | 3 | 1 | 1 |
| + whole-task | 7 | 5 | 11 | 3 | 2 | 0 |
| + short-repeat | 6 | 9 | 6 | 3 | 4 | 6 |
| all three | 0 | 10 | 6 | 3 | 2 | 0 |

Test, final prompt:

| Input | Group | Missed short | Missed | Grounding | Noise extra | Sub-step extra |
|---|---|---|---|---|---|---|
| action log | human (24) | 15 | 5 | 3 | 9 | 2 |
| action log | agent (27) | 0 | 0 | 2 | 0 | 2 |
| + screenshots | human (24) | 13 | 0 | 0 | 14 | 1 |
| + screenshots | agent (27) | 0 | 0 | 4 | 2 | 25 |

- Action numbers remove grounding errors; the whole-task rule trades noise extras for missed short
  workflows; the short-repeat rule causes sub-step splits.
- Human sessions fail mostly on **short workflows** (<= 5 actions, e.g. two-click Mind2Web tasks)
  that the model doesn't propose.
- **Why screenshots add extras:** all 25 sub-step extras are in agent08 and agent16, the two sessions
  built around one long workflow (city break: Wikipedia, weather, Booking, Viator, OpenTable, notes, email).
  With screenshots Sonnet still finds the whole workflow, then also lists each site's stretch
  (hotel search, restaurant search, activity search...) as its own workflow, because those pages look
  identical across repeats. The action log alone rarely does this (2 extras).
  On human sessions screenshots add 5 noise extras and catch 2 more short workflows.

## 4. Label audit

13 sessions relabelled blind (trace + screenshots only): all ambiguous-relabelled agent sessions,
agent08, and h002, h005, h007.

- Cohen's kappa (action in a repeated workflow or not): mean 0.37. Low agent kappa is mostly auditors
  counting abandoned repeats as workflow (with ambiguous counted as workflow, agent15/17 reach 0.82).
  agent08: 0.12, the auditor counted abandoned Dublin and Paris attempts as occurrences.
- Ground-truth workflows confirmed: 25/27.
- Ambiguous groups called a repeated workflow: 10/13, so **Rule B keeps the relabel**. The 3 not
  confirmed (agent01 QUOTES, agent05 DEBUG, agent10 WALK) were blocked almost immediately.
- Not confirmed: h002 "Find deals by region" (both instances use the same value, California) and
  h005 "Search finance jobs" (same filters, apply vs save). These are near-duplicate Mind2Web tasks,
  not a scenario error, so per Rule B the labels are unchanged. Both are dev sessions.
- `data/results/judge_agreement_sample.csv`: 15 judged proposals from the test run, `human_*` columns empty for manual rating.

## 5. Replay (execution check)

**Not done.** Scenarios were built for 19 detected workflows on the agent test sessions
(`replicate.replay scenario`), but the sandboxes expired and the replays were deferred.
No replay success rate is reported.

## 6. Test (once, 17 sessions x 3 runs, final prompt)

| Group | Action log | + screenshots | only log / only both | McNemar p | Per-session diff [95% CI] |
|---|---|---|---|---|---|
| all (51 pairs) | **0.61** | 0.55 | 9 / 6 | 0.61 | +0.06 [-0.14, +0.24] |
| human, new (24) | 0.29 | 0.38 | 3 / 5 | 0.73 | -0.08 [-0.42, +0.21] |
| agent (27) | 0.89 | 0.70 | 6 / 1 | 0.13 | +0.19 [+0.04, +0.33] |
| agent, no relabel | 0.67 | 0.48 | 6 / 1 | 0.13 | +0.19 [+0.04, +0.33] |

- **No significant difference between inputs** (Rule C). On agent sessions the bootstrap CI excludes
  zero, but the pre-registered test does not reach 0.05; the gap is the sub-step splitting above.
- Dev (0.71) -> test (0.61): the drop is the new human sessions (0.29).
- Run 1, secondary: Recall@10 0.89 / 0.93, P@5 0.90 / 0.86, occurrence F1 0.92 / 0.92,
  judge step reconstruction 4.1 / 4.2 and feasibility 3.3 / 3.4 (Sonnet judge, 1-5),
  cost $0.05 / $0.26 per session.

## 7. Saturation

Agent test sessions sit at 0.89 with the action log (Recall@10 1.00). Five harder scenarios to record:

1. **Five workflows, two repeats each, interleaved** on 3 shared sites (more ways to merge or split).
2. **Look-alike distraction:** a real workflow (npm audit) plus one-off visits to the same npm and GitHub pages that do not continue to notes.
3. **Same sites, different goals:** Booking hotel search for a trip vs. Booking price-check for a colleague, each repeated twice.
4. **Short repeated workflow** (3-4 actions, e.g. weather check -> note) hidden among long ones; this is the human failure mode.
5. **Variant steps:** one workflow repeated 3 times with an optional step present in only some runs (granularity and grounding stress).

Cost: existing agent recordings average ~42 min (16-96 min). These would run ~60-90 min each, so about
5-8 sandbox-hours, ~100-200 MB of proxy traffic only where sites block, and Codex tokens for 5 recorder sessions.
Not recorded.

## What remains uncertain

- 8 new human sessions and 9 contaminated agent sessions: CIs on every comparison are ±0.2 or wider.
- The ambiguous relabel moves agent scores by ~0.2-0.3 and rests on auditor agreement of 10/13 groups.
- Mind2Web "workflows" can be two tasks with the same inputs (h002, h005); the human set may undercount one-offs.
- Replay was not run, so whether detected workflows are executable is unmeasured.
- Judge scores are Sonnet rating Sonnet; uncalibrated until `human_*` columns are filled.
- Process: about half of the test predictions ran on the claude.ai subscription before the switch to the API key; the prompt and model were the same, and cached results were not re-run.
