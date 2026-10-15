# Dashboard: design brief

These are the instructions for `benchmark/dashboard.html`, the page that presents the benchmark. They come from Eliot. The current version works but was built quickly. The next step is a code cleanup that keeps this behaviour.

## Files

| File | What it is |
|---|---|
| `reference-performance-coding-agent-index.png` | **Target look for the Performance tab.** Artificial Analysis Coding Agent Index (https://artificialanalysis.ai/agents/coding-agents#coding-agents-index). |
| `reference-explorer-recorded-demonstration.png` | **Target look for the Dataset explorer.** A "Recorded demonstration" feed with screenshots and action lines. |
| `reference-page-artificial-analysis.png` | **Target for the whole page's style** (chrome, type, colors, controls): the top of https://artificialanalysis.ai/agents/coding-agents. |
| `current-performance.png`, `current-explorer.png` | The page after the restyle to the Artificial Analysis look (light only). |

## How the page is built

- **Source:** `visualization/dashboard/`, a Vite app in TypeScript with React, MobX (`src/store.ts` holds all state) and Tailwind (design tokens in `src/index.css`).
- **Template:** `npm run build` in `visualization/dashboard/` bundles everything into one file, `visualization/replicate/viewer/dashboard.html` (committed, so the Python build needs no Node).
- **Generator:** `visualization/replicate/viewer/__main__.py` fills the template. It replaces `/*DATA*/null` with every session (events, ground truth, predictions, which ground-truth workflow each prediction matched, plus the explorer facets `use_cases` and `source`) and `/*SCORES*/null` with the `table` from `data/results/scores.json`.
- **Build:** run this from `benchmark/`:
  ```sh
  export PYTHONPATH=data-generation:evaluation:visualization
  uv run python -m replicate.viewer --template dashboard.html --out dashboard.html
  ```
- **Runtime:** the output is static and opens from `file://`, with no server and no network. Screenshots load by relative path from `data/sessions/<id>/screenshots/`.
- **Deep link:** `dashboard.html#explorer` opens the second tab directly.

## Requirements

### Overall
- Two tabs at the top: **Performance** and **Dataset explorer**.
- Readable by someone who hasn't seen the benchmark. Use plain words ("Action log", "Screenshots", "found 3 of 3 workflows") rather than internal names (`trace`, `opp`, `h001-op0`).
- **Light only, matching the reference.** No dark mode: the page stays light even when the OS is dark (`color-scheme: light`). Colors come from the tokens at the top of the template; see "Design language" below.

### Performance tab
The target is the Coding Agent Index screenshot.
- **Vertical bar chart, best on the left.** For lower-is-better metrics (false positives, cost, time), the lowest value goes on the left.
- **One model at a time**, for simplicity, with a **model picker** (Haiku / Sonnet / Opus). The bars are that model's three inputs: action log, screenshots, and both. Add the no-LLM heuristic as a reference bar with a **checkered fill**, the way the reference marks "Not publicly available", plus a legend line explaining it. The heuristic is left out for cost and time.
- **Layout like the reference:**
  - a large serif title (`<metric>: <model>`);
  - a grey subtitle ending in "· Higher is better" or "· Lower is better";
  - the **Runs** switch as the card's top tab strip; an icon button (jump to table), the **Model** segmented control and the labelled **Metric** select top right;
  - dotted gridlines, no y-axis labels (the value is in the bar);
  - the value in white inside each bar;
  - under each bar, a bold label, then the model, then the cost per run;
  - a "What this metric means" row at the bottom that expands with a "+".
- **Runs switch:** Multitask (`h*` sessions, the default) or Single-task (`s*`, the same tasks done one after another).
- **Metric picker:** Runs solved (default: all real workflows found tightly in the top n, nothing extra; see `score.py`), Recall@10, Precision@5, occurrence F1, evidence overlap, distractor false positives, the two judge scores (1–5), cost per run, and time per call.
- **Hover a bar** to see its value, the 95% confidence interval where one exists (Runs solved, Recall@10, Precision@5), recall and precision together, and cost and time.
- **Below the chart**, a table with every model and input and every metric, best per column in bold. This is also the accessible fallback for the chart.

### Dataset explorer tab
The target is the recorded-demonstration screenshot.
- **Opens on a list of every run** (h001–h008, s001–s008): duration, actions, use case, source, the timeline strip and its workflows. Clicking a run opens the run view below, with "← All runs" to go back. `#explorer/h001` deep-links to a run; navigation goes through the URL hash, so the browser Back button works.
- **Left sidebar of facets** (on the list) narrowing the runs, each value with its count:
  - **Use case:** the business behind the repeated workflows ("Travel agency"), mapped from their Mind2Web domain in `data/index/tasks.jsonl` (every run today is travel);
  - **Source:** "Agent generated", or the human dataset the actions come from ("Multimodal-Mind2Web (human)"), from `manifest.source`;
  - **Interleaved with distractions:** Yes for the multitask runs (`h*`), No for the single-task runs (`s*`);
  - **Duration:** a histogram with a two-handle range bar, in minutes.
- **Run view, summary card:**
  - duration, number of actions, number of tasks, number of task switches and number of repeated workflows;
  - a colored timeline strip, one color per repeated workflow and grey for one-off tasks or distractions, with a legend;
  - a **Detector** picker, with Opus · action log + screenshots as the default;
  - what that detector proposed, each item marked **real**, **not a real workflow**, or **missed** (for ground-truth workflows it didn't find). Each item carries time chips that jump to that moment in the feed.
- **"Recorded demonstration · <duration>"**, a card that collapses, with an eye icon and a chevron, as in the reference:
  - **"Screenshot · m:ss"** at each change of task. The task sentence sits on the right. The image is wide with rounded corners, the clicked element is outlined in red, and a tag names the workflow (or "one-off task" / "distraction").
  - **One line per action**, each with an icon on the left, a sentence in the middle and the time on the right: "Clicked **Vacation packages**", "Typed **“san francisco”** into From", "Selected **“2”** in Number of passengers".
  - **"Switched to <site>"** lines (arrow icon) when the site changes. "· · · N min later · · ·" marks breaks over 2 minutes.
  - **Ground truth vs model prediction:** every action line carries its real workflow (or "One-off task" / "Distraction") in a Ground truth column, then a shaded Opus prediction group comparing inputs: one mark each for Opus given the action log, the screenshots, or both (independent of the Detector picker): ✓ right workflow or correctly left out, ✗ wrong workflow or false alarm, ○ missed. Hover a mark for the proposed workflow's name. The column header sticks under the page header; the feed scrolls with the page.
  - **Clicking a screenshot or an action line** opens that action's screenshot full size with the element boxed. Esc or a click closes it.

### Design language

Copied from Artificial Analysis (style only: no logo, name or watermark). Keep new UI inside these rules.

- **Fonts.** The reference uses Suisse Intl (sans) and Victor Serif (serif), both commercial and not on Google Fonts. We load the closest free pair from Google Fonts: **Inter** 400/500/600 for everything, **Newsreader** 500 for headings. Fallbacks: Helvetica Neue/Arial, Georgia.
- **Type scale.** Page title serif 36px; section heads serif 30px after a 20px black square; card titles serif 20px; card-strip and nav 13–14px medium; body 14px; subtitles, control labels, chart labels and notes 11–13px grey.
- **Colors** (from their CSS tokens and screenshot): band and select background `#f1f2f4`, page and cards `#ffffff`, nav pill track `#e4e5e8`, card tab strip `#fafafa` with active tab `#e7e7e7`, borders `#e7e7e7` / `#d9d9d9`, text `#000` / `#4d4d4d` / `#787878` / `#949494`, gridlines `#c2c2c2`. Series colors are their chart colors: `#0089f4` action log, `#ff7018` screenshots, `#736cd3` both; `#34a853` and `#cc785c` extend the workflow palette. Good `#34a853`, bad `#dc3333`.
- **Chrome.** Grey band on top holding a black pill title, a pill nav (white pill = current tab), the hero title, lede and a white summary card. Below it the page is white. Content width 1180px, 20px gutters (16px on phones).
- **Cards.** White, 1px `#d9d9d9` border, 8px radius, 20px padding, optional tab strip across the top.
- **Controls.** Segmented control: 28px buttons in a light grey track, the active one white with a hairline border. Select: 32px, grey fill, up/down chevron. Icon buttons: 32px white squares, 6px radius.
- **Chart.** Vertical bars best-left, 3px top radius, white 13px value centered in the bar, dotted gridlines and baseline, no axis labels. Under each bar: a 10px color square + bold label, the model, then a grey "(…)" line. Reference-only bars use a grey checkerboard (the reference's "Not publicly available"), explained by a checker legend line above the chart. "What This Metric Means" is a grey 12px row with an info icon and a "+" at the far right, closed by a bottom rule.

## Rejected on the way
Don't bring these back:
- **Horizontal grouped bars** with every model at once: too busy.
- **A single long page** without tabs, listing the "workflows found" and then a text-only history: "hard to read".
- **The first viewer**, `viewer.html` (template `template.html`), with timeline lanes per model and a screenshot player. It's still generated and works, but it's for detailed debugging, not presentation.

## Notes for the cleanup
- The hero (title, "What the models are shown" card) was removed; "All results" is collapsed by default and the table icon button opens it.
- `__main__.py` serves both templates. The `/*SCORES*/` replacement is only used by the dashboard.
- Nothing tests the page. A useful check is to render both tabs in headless Chrome and compare against the `current-*.png` screenshots. Headless Chrome won't go below 500px wide; check phone width by loading the page in a 390px iframe. In headless captures of `#explorer`, the sticky header sits a little lower; that's a capture artifact, not a page bug.
- **Data quirks** to keep handling:
  - The heuristic has only the action-log input.
  - Cost and time don't apply to the heuristic.
  - In the multitask runs, each action is attributed to one task (`event.task`), and the workflow (`event.kind`, `event.opp`) comes from the ground truth.
