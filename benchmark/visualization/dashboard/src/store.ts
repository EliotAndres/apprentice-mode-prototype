import { makeAutoObservable } from "mobx";

// Shapes written by replicate/viewer/__main__.py.
export type Box = [x: number, y: number, width: number, height: number];
export interface ActionEvent {
  seq: number; t: number; site: string; op: string; value: string | null; role: string; text: string;
  bbox: Box | null; img: string; size?: [width: number, height: number]; // screenshot pixels, absent = 1280x800
  task?: string; kind?: string; opp?: number; // from the ground truth: workflow, one_off, distractor or ambiguous
}
export interface Opportunity {
  name: string; description: string;
  instances: { task: string; spans: [number, number][] }[];
}
export interface Prediction { name: string; description: string; spans: [number, number][][]; match: number | null }
export interface Session {
  id: string; duration: number; tier: "easy" | "hard"; use_cases: string[]; source: string; // "Agent generated" or "<dataset> (human)"
  events: ActionEvent[]; gt: { opportunities: Opportunity[] }; preds: Record<string, Prediction[]>;
}
type MetricKey = "solved" | "r_at_10" | "p_at_5" | "instance_f1" | "grounding_iou" | "fp_negative" | "reconstruction" | "feasibility"
  | "mean_cost_usd" | "median_seconds";
export type ScoreRow = { tier: string; sessions: number; model: string; condition: Input; r_at_10_ci?: [number, number]; p_at_5_ci?: [number, number]; solved_ci?: [number, number] }
  & Record<MetricKey, number>;
export interface Metric {
  key: MetricKey; name: string; title: string; note: string; ci?: "r_at_10_ci" | "p_at_5_ci" | "solved_ci";
  ratio?: boolean; max?: number; lowerIsBetter?: boolean; money?: boolean; seconds?: boolean; modelOnly?: boolean;
}
export interface Segment { task?: string; kind: string; opp?: number; site: string; events: ActionEvent[] }

declare global { interface Window { SESSIONS: Session[] | null; SCORES: ScoreRow[] | null } }
export const SESSIONS = window.SESSIONS ?? [];
export const SCORES = window.SCORES ?? [];

export const clock = (seconds: number) => `${Math.floor(seconds / 60)}:${String(Math.floor(seconds % 60)).padStart(2, "0")}`;
export const minutes = (seconds: number) => seconds < 90 ? `${Math.round(seconds)} s` : `${Math.round(seconds / 60)} min`;

export const MODELS = ["haiku", "sonnet", "opus"] as const;
export const MODEL_NAMES: Record<string, string> = { heuristic: "No model", haiku: "Haiku", sonnet: "Sonnet", opus: "Opus" };
export const INPUTS = ["trace", "screenshots", "both"] as const;
export type Input = typeof INPUTS[number];
export const INPUT_NAMES: Record<Input, string> = { trace: "Action log", screenshots: "Screenshots", both: "Action log + screenshots" };
export const INPUT_COLORS: Record<Input, string> = { trace: "var(--color-s1)", screenshots: "var(--color-s2)", both: "var(--color-s3)" };
const WORKFLOW_COLORS = ["var(--color-s1)", "var(--color-s2)", "var(--color-s3)", "var(--color-s4)", "var(--color-s5)"];
export const workflowColor = (index: number) => WORKFLOW_COLORS[index % WORKFLOW_COLORS.length];
export const runName = (run: string) => {
  const [model, input] = run.split("__");
  return model === "heuristic" ? "Heuristic baseline" : `${MODEL_NAMES[model]} · ${INPUT_NAMES[input as Input]}`;
};

export const METRICS: Metric[] = [
  { key: "solved", name: "Work sessions solved", title: "Work sessions solved", note: "Share of work sessions where the model found every real repeated workflow among its first n proposals (n = how many there are), each covering at least half of the real actions with no time padding, and proposed nothing extra. All or nothing per work session.", ratio: true, ci: "solved_ci" },
  { key: "r_at_10", name: "Recall@10", title: "Workflow recall", note: "Share of the real repeated workflows that appear in the model's top 10 proposals.", ratio: true, ci: "r_at_10_ci" },
  { key: "p_at_5", name: "Precision@5", title: "Workflow precision", note: "Share of the model's top 5 proposals that are real repeated workflows.", ratio: true, ci: "p_at_5_ci" },
  { key: "instance_f1", name: "Occurrence F1", title: "Occurrence F1", note: "How well the model pins down each occurrence of a workflow it found (action-level F1).", ratio: true },
  { key: "grounding_iou", name: "Evidence overlap", title: "Evidence overlap", note: "Overlap (IoU) between the actions the model cites as evidence and the real ones.", ratio: true },
  { key: "fp_negative", name: "Distractor false positives", title: "Distractor false positives", note: "Share of one-off tasks and look-alike distractions wrongly proposed as workflows.", ratio: true, lowerIsBetter: true },
  { key: "reconstruction", name: "Step reconstruction (1–5)", title: "Step reconstruction", note: "How faithfully the proposed steps match the real workflow, rated 1–5 by an LLM judge (Sonnet).", max: 5 },
  { key: "feasibility", name: "Automation feasibility (1–5)", title: "Automation feasibility", note: "How actionable the proposal is as an automation spec, rated 1–5 by an LLM judge (Sonnet).", max: 5 },
  { key: "mean_cost_usd", name: "Cost per session ($)", title: "Cost per work session", note: "API-equivalent cost of analysing one work session.", lowerIsBetter: true, money: true, modelOnly: true },
  { key: "median_seconds", name: "Time per call (s)", title: "Time per call", note: "Median wall time of one model call.", lowerIsBetter: true, seconds: true, modelOnly: true },
];
export const formatMetric = (metric: Metric, value: number | null | undefined) => value == null ? "–"
  : metric.money ? `$${value.toFixed(2)}` : metric.seconds ? `${Math.round(value)}s` : value.toFixed(2);

export const SOURCES = ["Agent generated", ...new Set(SESSIONS.map(session => session.source))].filter((source, index, all) => all.indexOf(source) === index);
export const USE_CASES = [...new Set(SESSIONS.flatMap(session => session.use_cases ?? []))].sort();
// Multitask runs mix the workflows with one-off tasks and distractions; single-task runs do one task at a time.
export const isInterleaved = (session: Session) => session.tier === "hard";
const durationMinutes = (session: Session) => session.duration / 60;
export const DURATION_BOUNDS: [number, number] = SESSIONS.length
  ? [Math.floor(Math.min(...SESSIONS.map(durationMinutes))), Math.ceil(Math.max(...SESSIONS.map(durationMinutes)))]
  : [0, 0];

export const DETECTORS = [...new Set(SESSIONS.flatMap(session => Object.keys(session.preds)))].sort((a, b) => {
  const order: string[] = ["heuristic", ...MODELS], inputs: string[] = [...INPUTS];
  return order.indexOf(a.split("__")[0]) - order.indexOf(b.split("__")[0])
    || inputs.indexOf(a.split("__")[1]) - inputs.indexOf(b.split("__")[1]);
});

// Consecutive actions on the same task and workflow form one stretch of work in the feed.
export function segmentsOf(session: Session): Segment[] {
  const segments: Segment[] = [];
  for (const event of session.events) {
    const last = segments.at(-1);
    if (last && last.task === event.task && last.kind === event.kind) { last.events.push(event); continue; }
    segments.push({ task: event.task, kind: event.kind ?? "unlabelled", opp: event.opp, site: event.site, events: [event] });
  }
  return segments;
}

type Tab = "performance" | "explorer";
type Facet = "useCases" | "sources" | "interleaved" | "duration";

// How a detector treated one action: inside a workflow it proposed, and was that the right one?
export type Verdict = { kind: "correct" | "wrong" | "missed" | "false_alarm"; proposed?: string };
export function verdictFor(session: Session, run: string, event: ActionEvent): Verdict | null {
  const predictions = session.preds[run];
  if (!predictions) return null;
  // ponytail: first covering proposal wins; overlapping proposals are rare in the data.
  const covering = predictions.find(prediction => prediction.spans.some(instance =>
    instance.some(([start, end]) => event.t >= start - 0.01 && event.t <= end + 0.01)));
  const isWorkflow = event.kind === "workflow";
  // Ambiguous: a repeat that never completed twice. Scoring ignores it, so flagging it or not is fine.
  if (event.kind === "ambiguous" && covering?.match == null) return { kind: "correct", proposed: covering?.name };
  if (!covering) return { kind: isWorkflow ? "missed" : "correct" };
  if (isWorkflow && covering.match === event.opp) return { kind: "correct", proposed: covering.name };
  return { kind: isWorkflow ? "wrong" : "false_alarm", proposed: covering.name };
}

class Store {
  // Both come from the URL hash (see readHash), so links and the Back button work.
  tab: Tab = "performance";
  // performance
  model: string = MODELS.filter(model => SCORES.some(row => row.model === model)).at(-1) ?? "sonnet";
  metricKey: MetricKey = "solved";
  tooltip: { row: ScoreRow; x: number; y: number } | null = null;
  // explorer
  useCases = new Set<string>();
  sources = new Set<string>();
  interleaved = new Set<boolean>();
  duration: [number, number] = [...DURATION_BOUNDS];
  runId: string | null = null;
  detector = DETECTORS.includes("sonnet__both") ? "sonnet__both" : DETECTORS[0];
  lightbox: ActionEvent | null = null;

  constructor() {
    makeAutoObservable(this);
    this.readHash();
    addEventListener("hashchange", () => this.readHash());
  }
  // #performance, #explorer (the run list), #explorer/h001 (one run), #explorer/generate.
  readHash() {
    const runId = location.hash.split("/")[1] ?? null;
    if (runId !== this.runId) scrollTo(0, 0);
    this.tab = location.hash.startsWith("#explorer") ? "explorer" : "performance";
    this.runId = runId;
  }

  setTab(tab: Tab) { location.hash = tab; }
  openRun(id: string | null) { location.hash = id ? `explorer/${id}` : "explorer"; }
  set<K extends keyof this>(key: K, value: this[K]) { this[key] = value; }
  toggle<T>(set: Set<T>, value: T) { set.has(value) ? set.delete(value) : set.add(value); }
  clearFacets() { this.useCases.clear(); this.sources.clear(); this.interleaved.clear(); this.duration = [...DURATION_BOUNDS]; }
  // Held-out test set only (evaluation/split.json): new human multitask sessions plus agent sessions.
  get tierRows() { return SCORES.filter(row => row.tier === "test"); }
  get tierModels() { return MODELS.filter(model => this.tierRows.some(row => row.model === model)); }
  get metric() { return METRICS.find(metric => metric.key === this.metricKey)!; }
  get bars() {
    const { metric } = this;
    const baseline = metric.modelOnly ? [] : this.tierRows.filter(row => row.model === "heuristic");
    return [...this.tierRows.filter(row => row.model === this.model), ...baseline]
      .sort((a, b) => metric.lowerIsBetter ? a[metric.key] - b[metric.key] : b[metric.key] - a[metric.key]);
  }

  // A run passes when it matches every facet; `except` skips one so its own counts stay useful.
  matches(session: Session, except?: Facet) {
    const [low, high] = this.duration;
    return (except === "useCases" || !this.useCases.size || session.use_cases?.some(useCase => this.useCases.has(useCase)))
      && (except === "sources" || !this.sources.size || this.sources.has(session.source))
      && (except === "interleaved" || !this.interleaved.size || this.interleaved.has(isInterleaved(session)))
      && (except === "duration" || (durationMinutes(session) >= low && durationMinutes(session) <= high));
  }
  count(facet: Facet, predicate: (session: Session) => boolean) { return SESSIONS.filter(session => this.matches(session, facet) && predicate(session)).length; }
  get runs() { return SESSIONS.filter(session => this.matches(session)); }
  get facetsActive() {
    return this.useCases.size > 0 || this.sources.size > 0 || this.interleaved.size > 0
      || this.duration[0] !== DURATION_BOUNDS[0] || this.duration[1] !== DURATION_BOUNDS[1];
  }
  // The feed's per-action columns compare inputs on the one model that was run.
  get feedRuns() {
    return [["sonnet__trace", "Action log"], ["sonnet__both", "Action log + screenshots"]] as const;
  }
  get run() { return SESSIONS.find(session => session.id === this.runId); }
}

export const store = new Store();
