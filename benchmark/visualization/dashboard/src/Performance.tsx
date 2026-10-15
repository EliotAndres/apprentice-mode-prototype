import { observer } from "mobx-react-lite";
import { useRef } from "react";
import { store, SCORES, METRICS, MODELS, MODEL_NAMES, INPUT_NAMES, INPUT_COLORS, formatMetric, type Metric, type ScoreRow } from "./store";
import { Card, Explain, Icon, IconButton, SectionHead, Segmented, Select, Swatch } from "./ui";

const isBaseline = (row: ScoreRow) => row.model === "heuristic";

const Chart = observer(() => {
  const { metric, bars } = store;
  const maxValue = metric.ratio ? 1
    : metric.max ?? Math.max(...SCORES.filter(row => !isBaseline(row)).map(row => row[metric.key])) * 1.1;
  const ticks = metric.ratio ? [0.25, 0.5, 0.75, 1] : metric.max ? [1, 2, 3, 4, 5] : [0.25, 0.5, 0.75, 1].map(f => f * maxValue);
  const showTooltip = (row: ScoreRow, x: number, y: number) => store.set("tooltip", { row, x, y });
  const hideTooltip = () => store.set("tooltip", null);
  return <div className="mt-4">
    {/* No y-axis labels: as in the reference, the value sits inside the bar. */}
    <div className="relative flex h-[300px] items-end gap-2 border-b-[1.5px] border-dotted border-grid px-1 sm:gap-5 sm:px-4">
      {ticks.map(tick => <div key={tick} className="pointer-events-none absolute inset-x-0 border-t-[1.5px] border-dotted border-grid"
        style={{ bottom: `${tick / maxValue * 100}%` }} />)}
      {bars.map(row => {
        const height = Math.max(1, row[metric.key] / maxValue * 100), short = height < 12;
        return <div key={`${row.model}-${row.condition}`} className="relative z-[1] flex h-full min-w-0 flex-1 items-end justify-center">
          <div tabIndex={0}
            className={`flex w-full max-w-32 justify-center rounded-t-[3px] text-[11px] font-medium tabular-nums text-white
              transition-opacity hover:opacity-85 focus:opacity-85 focus:outline-none sm:text-[13px]
              ${short ? "items-start" : "items-center"} ${isBaseline(row) ? "checker [text-shadow:0_0_3px_rgba(0,0,0,.55)]" : ""}`}
            style={{ height: `${height}%`, background: isBaseline(row) ? undefined : INPUT_COLORS[row.condition] }}
            onPointerMove={event => showTooltip(row, event.clientX, event.clientY)} onPointerLeave={hideTooltip}
            onFocus={event => { const box = event.currentTarget.getBoundingClientRect(); showTooltip(row, box.left, box.top); }}
            onBlur={hideTooltip}>
            <span className={short ? "-translate-y-[22px] text-black" : ""}>{formatMetric(metric, row[metric.key])}</span>
          </div>
        </div>;
      })}
    </div>
    <div className="flex gap-2 px-1 pt-2.5 sm:gap-5 sm:px-4">
      {bars.map(row => <div key={`${row.model}-${row.condition}`} className="flex min-w-0 flex-1 justify-center text-[11px] leading-snug sm:text-xs">
        <div className="w-full max-w-32">
          <div className="flex items-start gap-1.5 font-medium">
            {isBaseline(row) ? <span className="checker-sm mt-[3px] hidden size-2.5 flex-none rounded-[2px] sm:block" />
              : <Swatch className="mt-[3px] hidden sm:block" color={INPUT_COLORS[row.condition]} />}
            {isBaseline(row) ? "Heuristic" : INPUT_NAMES[row.condition]}
          </div>
          <div className="font-medium sm:pl-4">{isBaseline(row) ? "no model" : MODEL_NAMES[row.model]}</div>
          <div className="text-faint sm:pl-4">{isBaseline(row) ? "(baseline)" : `($${row.mean_cost_usd.toFixed(2)} per session)`}</div>
        </div>
      </div>)}
    </div>
  </div>;
});

export const Tooltip = observer(() => {
  const ref = useRef<HTMLDivElement>(null);
  if (!store.tooltip) return null;
  const { row, x, y } = store.tooltip, { metric } = store, ci = metric.ci && row[metric.ci];
  const { width = 220, height = 120 } = ref.current?.getBoundingClientRect() ?? {};
  return <div ref={ref} role="status"
    className="pointer-events-none fixed z-30 min-w-[200px] rounded-lg border border-line bg-white px-3 py-2.5 text-xs leading-normal shadow-[0_4px_12px_rgba(0,0,0,.08)]"
    style={{ left: Math.min(x + 14, innerWidth - width - 8), top: Math.max(8, Math.min(y + 14, innerHeight - height - 8)) }}>
    <strong className="text-[15px] font-semibold">{formatMetric(metric, row[metric.key])}</strong> <span className="text-muted">{metric.name}</span>
    {ci && <div className="text-muted">95% CI {ci.map(value => value.toFixed(2)).join("–")}</div>}
    <div className="flex items-center gap-1.5">
      {isBaseline(row) ? "Heuristic baseline (no model)"
        : <><Swatch color={INPUT_COLORS[row.condition]} />{MODEL_NAMES[row.model]} · {INPUT_NAMES[row.condition]}</>}
    </div>
    <div className="text-muted">Recall@10 {row.r_at_10.toFixed(2)} · Precision@5 {row.p_at_5.toFixed(2)}</div>
    {!isBaseline(row) && <div className="text-muted">${row.mean_cost_usd.toFixed(2)} and {Math.round(row.median_seconds)}s per call</div>}
  </div>;
});

const ScoreTable = observer(() => {
  const rows = store.tierRows;
  const best = (metric: Metric) => {
    const values = rows.filter(row => !metric.modelOnly || !isBaseline(row)).map(row => row[metric.key]);
    return metric.lowerIsBetter ? Math.min(...values) : Math.max(...values);
  };
  const cell = "whitespace-nowrap border-b border-line px-3 py-2.5 text-right first:text-left [&:nth-child(2)]:text-left";
  return <table className="w-full border-collapse text-[13px] tabular-nums">
    <thead><tr>{["Model", "Input", ...METRICS.map(metric => metric.name)].map(name =>
      <th key={name} className={`${cell} bg-chip text-xs font-medium text-muted`}>{name}</th>)}</tr></thead>
    <tbody>{[...rows].sort((a, b) => b.r_at_10 - a.r_at_10 || b.p_at_5 - a.p_at_5).map(row =>
      <tr key={`${row.model}-${row.condition}`} className="hover:bg-chip [&:last-child>td]:border-b-0">
        <td className={cell}>{isBaseline(row) ? "Heuristic baseline" : MODEL_NAMES[row.model]}</td>
        <td className={cell}>{isBaseline(row) ? "Action log"
          : <span className="inline-flex items-center gap-1.5"><Swatch color={INPUT_COLORS[row.condition]} />{INPUT_NAMES[row.condition]}</span>}</td>
        {METRICS.map(metric => {
          const notApplicable = metric.modelOnly && isBaseline(row);
          return <td key={metric.key} className={`${cell} ${!notApplicable && row[metric.key] === best(metric) ? "font-semibold" : ""}`}>
            {notApplicable ? "–" : formatMetric(metric, row[metric.key])}</td>;
        })}
      </tr>)}</tbody>
  </table>;
});

export const Performance = observer(() => {
  const { metric } = store;
  const sessionCount = store.tierRows[0]?.sessions ?? 0;
  const allResults = useRef<HTMLDetailsElement>(null);
  return <section>
    <SectionHead>Performance</SectionHead>
    <p className="mb-8 max-w-[60ch] text-base">Measures how well each model extracts tasks from multi-step workflows. Does not measure whether the model picks high-value or riskless tasks.</p>
    <Card className="pb-1 pt-4 sm:pb-2 sm:pt-5">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <h2 className="font-serif text-xl font-medium leading-tight">{metric.title}: {MODEL_NAMES[store.model]}</h2>
          <p className="mt-0.5 max-w-[62ch] text-[13px] leading-snug text-faint">
            {metric.name} by input on the {sessionCount} held-out test sessions (human and agent-generated) · {metric.lowerIsBetter ? "Lower" : "Higher"} is better</p>
        </div>
        <div className="flex flex-col items-start gap-2 sm:items-end">
          <div className="flex gap-1.5">
            <IconButton label="All results as a table"
              onClick={() => { allResults.current!.open = true; allResults.current!.scrollIntoView({ behavior: "smooth" }); }}
              path='<rect x="3" y="3" width="18" height="18" rx="2"/><path d="M3 9h18M3 15h18M12 3v18"/>' />
          </div>
          <div className="flex items-center gap-2 text-xs text-muted">Model
            <Segmented options={store.tierModels.map(model => [model, MODEL_NAMES[model]] as const)} value={store.model as typeof MODELS[number]} onChange={model => store.set("model", model)} />
          </div>
          <div className="flex items-center gap-2 text-xs text-muted">Metric
            <Select label="Metric" options={METRICS.map(metric => [metric.key, metric.name] as const)} value={store.metricKey}
              onChange={key => store.set("metricKey", key)} />
          </div>
        </div>
      </div>
      <div className="ml-1 mt-5 flex items-center gap-2 text-[13px]">
        <span className="checker-sm h-3 w-4 rounded-[2px]" />Heuristic baseline: no model, shown for reference
      </div>
      <Chart />
      <Explain title="What This Metric Means" className="mb-3 mt-9">
        <p className="mb-3 max-w-[75ch] text-[13px] text-secondary">
          {metric.note}{metric.ci && " Hover a bar for its 95% confidence interval (session bootstrap)."}</p>
      </Explain>
    </Card>
    <details ref={allResults} className="group mt-12">
      <summary className="mb-3 flex w-fit cursor-pointer list-none items-center gap-2 font-serif text-[22px] font-medium">
        All results
        <Icon className="size-5 text-muted transition-transform group-open:rotate-180" path='<path d="m6 9 6 6 6-6"/>' />
      </summary>
      <div className="overflow-x-auto rounded-lg border border-line-strong bg-white py-1"><ScoreTable /></div>
      <p className="mt-2.5 text-xs text-faint">Held-out test set: {sessionCount} work sessions (8 new human multitask, 9 agent-generated), run 1 of 3; 3-run means and significance in evaluation/REPORT.md. See evaluation/PROTOCOL.md. Best value per column in bold. Judge scores are 1–5, rated by Sonnet.
        Cost is the API-equivalent cost per work session; time is the median wall time per call.</p>
    </details>
  </section>;
});
