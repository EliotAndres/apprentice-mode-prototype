import { observer } from "mobx-react-lite";
import { Fragment, useEffect, useRef, type CSSProperties, type ReactNode } from "react";
import {
  store, SESSIONS, SOURCES, isInterleaved, verdictFor, type Verdict, USE_CASES, DURATION_BOUNDS, DETECTORS, clock, minutes, runName, segmentsOf, workflowColor,
  type ActionEvent, type Session,
} from "./store";
import { Card, Icon, SectionHead, Select, Swatch } from "./ui";

// Mind2Web screenshots are 1280x800; live recordings keep the real viewport height.
const sizeOf = (event: ActionEvent) => event.size ?? [1280, 800];
const frameStyle = (event: ActionEvent): CSSProperties => ({ aspectRatio: `${sizeOf(event)[0]} / ${sizeOf(event)[1]}` });
const boxStyle = (event: ActionEvent): CSSProperties => {
  if (!event.bbox) return { display: "none" };
  const [x, y, width, height] = event.bbox, [frameWidth, frameHeight] = sizeOf(event);
  return { left: `${100 * x / frameWidth}%`, top: `${100 * y / frameHeight}%`, width: `${100 * width / frameWidth}%`, height: `${100 * height / frameHeight}%` };
};

// ---------- facets ----------

const FacetGroup = ({ title, children }: { title: string; children: ReactNode }) =>
  <div className="border-b border-line py-4 last:border-b-0">
    <h3 className="mb-2 text-[13px] font-semibold">{title}</h3>
    {children}
  </div>;

const Check = ({ label, count, checked, onChange }: { label: string; count: number; checked: boolean; onChange: () => void }) =>
  <label className={`flex items-center gap-2 py-1 text-[13px] ${count || checked ? "cursor-pointer" : "text-faint"}`}>
    <input type="checkbox" className="size-3.5 accent-black" checked={checked} disabled={!count && !checked} onChange={onChange} />
    <span className="flex-1">{label}</span>
    <span className="text-xs tabular-nums text-faint">{count}</span>
  </label>;

const DURATION_BINS = 12;
const DurationFacet = observer(() => {
  const [min, max] = DURATION_BOUNDS, [low, high] = store.duration;
  const binWidth = (max - min) / DURATION_BINS || 1;
  const bins = Array.from({ length: DURATION_BINS }, (_, index) => {
    const from = min + index * binWidth, to = from + binWidth;
    const inBin = (session: Session) => session.duration / 60 >= from && (session.duration / 60 < to || index === DURATION_BINS - 1);
    return { from, to, count: store.count("duration", inBin) };
  });
  const tallest = Math.max(1, ...bins.map(bin => bin.count));
  return <FacetGroup title="Duration">
    <div className="flex h-10 items-end gap-0.5" aria-hidden>
      {bins.map(bin => <div key={bin.from} className={`flex-1 rounded-t-[2px] ${bin.to > low && bin.from < high ? "bg-black" : "bg-neg"}`}
        style={{ height: `${bin.count ? Math.max(8, bin.count / tallest * 100) : 0}%` }} />)}
    </div>
    <div className="relative h-4">
      <div className="absolute inset-x-0 top-1/2 h-0.5 -translate-y-1/2 rounded bg-neg" />
      <div className="absolute top-1/2 h-0.5 -translate-y-1/2 rounded bg-black"
        style={{ left: `${(low - min) / (max - min || 1) * 100}%`, right: `${(max - high) / (max - min || 1) * 100}%` }} />
      <input type="range" aria-label="Shortest duration, minutes" className="range-thumb" min={min} max={max} value={low}
        onChange={event => store.set("duration", [Math.min(Number(event.target.value), high), high])} />
      <input type="range" aria-label="Longest duration, minutes" className="range-thumb" min={min} max={max} value={high}
        onChange={event => store.set("duration", [low, Math.max(Number(event.target.value), low)])} />
    </div>
    <div className="mt-1 flex justify-between text-xs tabular-nums text-muted"><span>{low} min</span><span>{high} min</span></div>
  </FacetGroup>;
});

const Facets = observer(() =>
  <aside className="self-start lg:sticky lg:top-24">
    <div className="flex items-baseline justify-between">
      <h2 className="font-serif text-xl font-medium">Filters</h2>
      {store.facetsActive && <button className="cursor-pointer text-xs text-muted underline hover:text-black"
        onClick={() => store.clearFacets()}>Clear all</button>}
    </div>
    <p className="text-xs text-faint">{store.runs.length} of {SESSIONS.length} work sessions</p>
    <FacetGroup title="Use case">
      {USE_CASES.map(useCase => <Check key={useCase} label={useCase} checked={store.useCases.has(useCase)}
        count={store.count("useCases", session => session.use_cases.includes(useCase))}
        onChange={() => store.toggle(store.useCases, useCase)} />)}
    </FacetGroup>
    <FacetGroup title="Source">
      {SOURCES.map(source => <Check key={source} label={source} checked={store.sources.has(source)}
        count={store.count("sources", session => session.source === source)}
        onChange={() => store.toggle(store.sources, source)} />)}
    </FacetGroup>
    <FacetGroup title="Interleaved with distractions">
      {[true, false].map(value => <Check key={String(value)} label={value ? "Yes" : "No"} checked={store.interleaved.has(value)}
        count={store.count("interleaved", session => isInterleaved(session) === value)}
        onChange={() => store.toggle(store.interleaved, value)} />)}
    </FacetGroup>
    <DurationFacet />
  </aside>);

// ---------- run summary ----------

const Strip = ({ session }: { session: Session }) => {
  const width = 1000, height = 12;
  return <svg className="mb-2 mt-4 block h-3 w-full" viewBox={`0 0 ${width} ${height}`} preserveAspectRatio="none" role="img"
    aria-label="When each workflow was worked on">
    <rect width={width} height={height} rx={3} fill="var(--color-chip)" />
    {session.events.map((event, index) => {
      const next = session.events[index + 1]?.t ?? event.t + 5;
      return <rect key={event.seq} x={event.t / session.duration * width} height={height}
        width={Math.max(1.5, Math.min(next - event.t, 20) / session.duration * width)}
        fill={event.kind === "workflow" ? workflowColor(event.opp!) : "var(--color-neg)"} />;
    })}
  </svg>;
};

const Badge = ({ good, children }: { good: boolean; children: ReactNode }) =>
  <span className={`whitespace-nowrap rounded-full border border-line px-2 py-px text-[11px] font-medium
    ${good ? "bg-good/12" : "bg-bad/10"}`}><span className={good ? "text-good" : "text-bad"}>{good ? "✓" : "✗"}</span> {children}</span>;

const Workflow = ({ name, description, badge, rail, starts, onJump }: {
  name: string; description: string; badge: ReactNode; rail: CSSProperties; starts: number[]; onJump: (t: number) => void;
}) =>
  <div className="grid grid-cols-[4px_1fr] gap-x-3.5 border-b border-line py-3.5 last:border-b-0">
    <div className="rounded-[2px]" style={rail} />
    <div>
      <h4 className="flex flex-wrap items-baseline gap-x-2.5 gap-y-1 text-sm font-semibold">{name} {badge}</h4>
      <p className="mb-1.5 mt-0.5 text-[13px] text-muted">{description}</p>
      {starts.map((t, index) => <button key={index} onClick={() => onJump(t)}
        className="my-0.5 mr-1 h-6 cursor-pointer rounded border border-line bg-band px-2 text-xs tabular-nums hover:border-secondary">{clock(t)}</button>)}
    </div>
  </div>;

const RunSummary = observer(({ session, onJump }: { session: Session; onJump: (t: number) => void }) => {
  const { opportunities } = session.gt;
  const predictions = session.preds[store.detector] ?? [];
  const segments = segmentsOf(session);
  const taskCount = new Set(session.events.map(event => event.task)).size;
  const matched = new Set(predictions.map(prediction => prediction.match).filter(match => match != null));
  const wrong = predictions.filter(prediction => prediction.match == null).length;
  return <>
    <div className="flex flex-wrap items-start justify-between gap-4">
      <div>
        <h2 className="font-serif text-xl font-medium leading-tight">Work session {session.id}</h2>
        <p className="mt-0.5 text-[13px] leading-snug text-faint">
          {minutes(session.duration)} · {session.events.length} actions · {taskCount} tasks, switched between {segments.length} times
          {" "}· {opportunities.length} repeated workflows</p>
      </div>
      <div className="flex items-center gap-2 text-xs text-muted">Detector
        <Select label="Detector" options={DETECTORS.map(run => [run, runName(run)] as const)} value={store.detector}
          onChange={run => store.set("detector", run)} />
      </div>
    </div>
    <Strip session={session} />
    <div className="flex flex-wrap gap-x-4 gap-y-1 text-[13px]">
      {opportunities.map((opportunity, index) =>
        <span key={index} className="inline-flex items-center gap-1.5"><Swatch color={workflowColor(index)} />{opportunity.name}</span>)}
      <span className="inline-flex items-center gap-1.5"><Swatch color="var(--color-neg)" />one-off task or distraction</span>
    </div>
    <h4 className="mb-1 mt-6 text-sm font-semibold">
      {predictions.length
        ? `${runName(store.detector)} found ${matched.size} of ${opportunities.length} workflows${wrong ? `, plus ${wrong} wrong` : ""}`
        : `${runName(store.detector)} has no predictions for this work session`}</h4>
    {predictions.map((prediction, index) =>
      <Workflow key={`p${index}`} name={prediction.name} description={prediction.description}
        badge={<Badge good={prediction.match != null}>{prediction.match != null ? "real" : "not a real workflow"}</Badge>}
        rail={{ background: prediction.match != null ? workflowColor(prediction.match) : "var(--color-neg)" }}
        starts={prediction.spans.map(spans => spans[0][0])} onJump={onJump} />)}
    {opportunities.map((opportunity, index) => !matched.has(index) &&
      <Workflow key={`m${index}`} name={opportunity.name} description={opportunity.description}
        badge={<Badge good={false}>missed</Badge>} rail={{ borderLeft: `4px dotted ${workflowColor(index)}` }}
        starts={opportunity.instances.map(instance => instance.spans[0][0])} onJump={onJump} />)}
  </>;
});

// ---------- recorded demonstration ----------

const ACTION_ICONS: Record<string, string> = {
  CLICK: '<path d="M4 4l7.07 17 2.51-7.39L21 11.07z"/><path d="m13.6 13.6 5.4 5.4"/>',
  TYPE: '<rect x="2" y="6" width="20" height="12" rx="2"/><path d="M6 10h.01M10 10h.01M14 10h.01M18 10h.01M7 14h10"/>',
  SELECT: '<path d="M8 6h13M8 12h13M8 18h13M3 6h.01M3 12h.01M3 18h.01"/>',
  ENTER: '<path d="M9 10 4 15l5 5"/><path d="M20 4v7a4 4 0 0 1-4 4H4"/>',
  SWITCH: '<path d="M5 12h14"/><path d="m12 5 7 7-7 7"/>',
};
ACTION_ICONS.KEY = ACTION_ICONS.TYPE;

function describeAction(event: ActionEvent) {
  const target = event.text || event.role;
  if (event.op === "TYPE") return <>Typed <b className="font-semibold">“{event.value}”</b> into {target}</>;
  if (event.op === "SELECT") return <>Selected <b className="font-semibold">“{event.value}”</b> in {target}</>;
  if (event.op === "ENTER") return <>Pressed <b className="font-semibold">Enter</b></>;
  if (event.op === "KEY") return <>Pressed <b className="font-semibold">{event.value}</b></>;
  return <>Clicked <b className="font-semibold">{target}</b></>;
}

// Every feed row shares these columns: icon, action, time | ground truth | one cell per Opus input.
const ROW = "grid grid-cols-[14px_minmax(0,1fr)_36px_170px_repeat(3,80px)] gap-x-2.5";
const TRUTH_CELL = "flex min-w-0 items-center gap-1.5 self-stretch border-l border-line pl-2.5";
const MODEL_CELL = "flex items-center justify-center self-stretch bg-strip";
const FIRST_MODEL_CELL = "border-l border-line";

const VERDICTS: Record<Verdict["kind"], { mark: string; className: string; say: (proposed?: string) => string }> = {
  correct: { mark: "✓", className: "text-good", say: proposed => proposed ? `put in “${proposed}”` : "correctly left out" },
  wrong: { mark: "✗", className: "text-bad", say: proposed => `put in the wrong workflow, “${proposed}”` },
  false_alarm: { mark: "✗", className: "text-bad", say: proposed => `wrongly flagged as “${proposed}”` },
  missed: { mark: "○", className: "text-bad", say: () => "missed" },
};

const truthOf = (session: Session, event: ActionEvent) => event.kind === "workflow"
  ? { label: session.gt.opportunities[event.opp!].name, color: workflowColor(event.opp!) }
  : { label: { distractor: "Distraction", ambiguous: "Abandoned repeat (not scored)" }[event.kind ?? ""] ?? "One-off task",
      color: "var(--color-neg)" };

const ModelCells = observer(({ session, event }: { session: Session; event?: ActionEvent }) =>
  <>{store.feedRuns.map(([run, name], index) => {
    const verdict = event && verdictFor(session, run, event), look = verdict && VERDICTS[verdict.kind];
    return <div key={run} className={`${MODEL_CELL} ${index === 0 ? FIRST_MODEL_CELL : ""}`}
      title={verdict ? `${name}: ${look!.say(verdict.proposed)}` : undefined}>
      {event && (look ? <span className={`text-sm font-semibold ${look.className}`}>{look.mark}</span> : <span className="text-faint">–</span>)}
    </div>;
  })}</>);

const Step = ({ session, event, icon, time, onClick, children }: {
  session: Session; event?: ActionEvent; icon: string; time: number; onClick?: () => void; children: ReactNode;
}) => {
  const truth = event && truthOf(session, event);
  return <div onClick={onClick} className={`${ROW} border-b border-line text-[13px] leading-snug ${onClick ? "cursor-zoom-in hover:bg-chip" : ""}`}>
    <Icon className="size-3.5 self-center text-muted" strokeWidth={1.8} path={ACTION_ICONS[icon] ?? ACTION_ICONS.CLICK} />
    <div className="min-w-0 py-2">{children}</div>
    <div className="self-center text-right text-xs tabular-nums text-faint">{clock(time)}</div>
    <div className={TRUTH_CELL}>{truth && <><Swatch color={truth.color} /><span className="truncate text-xs" title={truth.label}>{truth.label}</span></>}</div>
    <ModelCells session={session} event={event} />
  </div>;
};

const FeedHeader = observer(() =>
  <div className="sticky top-[72px] z-[2] bg-white text-xs text-muted">
    <div className={`${ROW} pt-2`}>
      <div className="col-span-3" />
      <div className={`${TRUTH_CELL} font-semibold text-black`}>Ground truth</div>
      <div className={`${MODEL_CELL} ${FIRST_MODEL_CELL} col-span-3 font-semibold text-black`}>Opus prediction</div>
    </div>
    <div className={`${ROW} border-b border-line-strong pb-1.5`}>
      <div className="col-span-2">Action</div>
      <div className="text-right">Time</div>
      <div className={TRUTH_CELL}>Workflow</div>
      {store.feedRuns.map(([run, name], index) =>
        <div key={run} className={`${MODEL_CELL} ${index === 0 ? FIRST_MODEL_CELL : ""}`}>{name}</div>)}
    </div>
  </div>);

const Feed = ({ session }: { session: Session }) => {
  const open = (event: ActionEvent) => store.set("lightbox", event);
  return <div className="overflow-x-auto lg:overflow-visible"><div className="min-w-[680px]">
    <FeedHeader />
    {segmentsOf(session).map((segment, index, segments) => {
      const first = segment.events[0], last = segment.events.at(-1)!, previous = segments[index - 1];
      const pause = previous ? first.t - previous.events.at(-1)!.t : 0;
      return <Fragment key={first.seq}>
        {pause > 120 && <div className="py-3 text-center text-[11px] tracking-wider text-faint">· · · {minutes(pause)} later · · ·</div>}
        {previous && previous.site !== segment.site &&
          <Step session={session} icon="SWITCH" time={first.t}>Switched to <b className="font-semibold">{segment.site}</b></Step>}
        <div className={ROW}>
          <div className="col-span-3 scroll-mt-32 pb-2 pt-5" data-start={first.t} data-end={last.t}>
            <div className="mb-2 flex justify-between gap-3 text-xs">
              <span className="whitespace-nowrap text-faint">Screenshot · {clock(first.t)}</span>
              <span className="truncate text-right text-secondary" title={segment.task}>{segment.task}</span>
            </div>
            <div className="relative max-w-[480px] cursor-zoom-in overflow-hidden rounded-lg border border-line bg-band" style={frameStyle(first)} onClick={() => open(first)}>
              <img loading="lazy" src={first.img} alt="" className="block size-full object-cover" />
              <div className="absolute rounded-[3px] border-2 border-bad" style={boxStyle(first)} />
            </div>
          </div>
          <div className={TRUTH_CELL} />
          <ModelCells session={session} />
        </div>
        {segment.events.map(event =>
          <Step key={event.seq} session={session} event={event} icon={event.op} time={event.t} onClick={() => open(event)}>{describeAction(event)}</Step>)}
      </Fragment>;
    })}
  </div></div>;
};

export const Lightbox = observer(() => {
  useEffect(() => {
    const close = (event: KeyboardEvent) => event.key === "Escape" && store.set("lightbox", null);
    addEventListener("keydown", close);
    return () => removeEventListener("keydown", close);
  }, []);
  const event = store.lightbox;
  if (!event) return null;
  return <div className="fixed inset-0 z-40 flex flex-col items-center justify-center gap-2.5 bg-black/80 p-4"
    onClick={() => store.set("lightbox", null)}>
    <div className="relative w-full max-w-[1100px]" style={frameStyle(event)}>
      <img src={event.img} alt="" className="size-full rounded-md object-contain" />
      <div className="absolute rounded-[3px] border-[3px] border-bad" style={boxStyle(event)} />
    </div>
    <div className="max-w-[1100px] text-center text-[13px] text-white">
      {clock(event.t)} · {event.op}{event.value ? ` “${event.value}”` : ""} · {event.text || event.role} (click or Esc to close)
    </div>
  </div>;
});

// ---------- tab ----------

const RunList = observer(() =>
  <div className="grid gap-8 lg:grid-cols-[220px_minmax(0,1fr)]">
    <Facets />
    <div className="min-w-0">
      {!store.runs.length ? <Card className="py-10 text-center text-muted">
        <p>No work sessions match these filters.</p>
        <button onClick={() => store.openRun(GENERATE)}
          className="mt-4 h-9 cursor-pointer rounded-full bg-black px-4 text-[13px] font-medium text-white hover:bg-secondary">
          Generate synthetic work session</button>
      </Card> :
        <ul className="divide-y divide-line overflow-hidden rounded-lg border border-line-strong bg-white">
          {store.runs.map(session => <li key={session.id}>
            <button onClick={() => store.openRun(session.id)}
              className="group block w-full cursor-pointer px-3.5 py-4 text-left hover:bg-strip sm:px-5">
              <div className="flex items-baseline justify-between gap-3">
                <h3 className="font-serif text-xl font-medium">Work session {session.id}</h3>
                <span className="text-xs text-muted group-hover:text-black">Open →</span>
              </div>
              <p className="mt-0.5 text-[13px] text-faint">
                {minutes(session.duration)} · {[`${session.events.length} actions`, ...session.use_cases, session.source].join(" · ")}</p>
              <Strip session={session} />
              <div className="flex flex-wrap gap-x-4 gap-y-1 text-[13px]">
                {session.gt.opportunities.map((opportunity, index) =>
                  <span key={index} className="inline-flex items-center gap-1.5"><Swatch color={workflowColor(index)} />{opportunity.name}</span>)}
              </div>
            </button>
          </li>)}
        </ul>}
    </div>
  </div>);

const RunDetail = ({ session }: { session: Session }) => {
  const demo = useRef<HTMLDetailsElement>(null);
  const jumpTo = (t: number) => {
    const heads = [...demo.current!.querySelectorAll<HTMLElement>("[data-start]")];
    const head = heads.find(h => Number(h.dataset.start) <= t + 0.01 && t - 0.01 <= Number(h.dataset.end))
      ?? heads.find(h => Number(h.dataset.start) >= t);
    demo.current!.open = true;
    head?.scrollIntoView({ behavior: "smooth", block: "start" });
  };
  return <>
    <button onClick={() => store.openRun(null)} className="mb-4 cursor-pointer text-[13px] text-muted hover:text-black">← All work sessions</button>
    <Card className="pb-3 pt-5">
      <RunSummary session={session} onJump={jumpTo} />
    </Card>
    <details ref={demo} open className="group mt-6 rounded-lg border border-line bg-white">
      <summary className="flex cursor-pointer list-none items-center gap-3 rounded-t-lg border-b-2 border-s1 px-3.5 py-4 font-serif text-[22px] font-medium sm:px-5">
        <Icon className="size-7 flex-none" strokeWidth={1.5} path='<path d="M3 7V5a2 2 0 0 1 2-2h2M17 3h2a2 2 0 0 1 2 2v2M21 17v2a2 2 0 0 1-2 2h-2M7 21H5a2 2 0 0 1-2-2v-2"/><path d="M12 8c-3 0-5 2.5-6 4 1 1.5 3 4 6 4s5-2.5 6-4c-1-1.5-3-4-6-4z"/><circle cx="12" cy="12" r="1.6"/>' />
        <span className="whitespace-nowrap">Recorded demonstration</span>
        <span className="whitespace-nowrap font-sans text-[13px] font-normal text-faint">{minutes(session.duration)}</span>
        <Icon className="ml-auto size-5 flex-none text-muted transition-transform group-open:rotate-180" path='<path d="m6 9 6 6 6-6"/>' />
      </summary>
      <div className="px-3.5 pb-6 pt-2 sm:px-5">
        <p className="mb-2 text-xs text-muted">Sonnet given the action log, or the action log plus screenshots.
          <span className="ml-1 font-semibold text-good">✓</span> right workflow, or correctly left out
          <span className="ml-2 font-semibold text-bad">✗</span> wrong workflow or false alarm
          <span className="ml-2 font-semibold text-bad">○</span> missed · hover a mark for details</p>
        <Feed session={session} />
      </div>
    </details>
  </>;
};

// Placeholder behind the empty list's "Generate synthetic workflow" button; reachable at #explorer/generate.
const GENERATE = "generate";
const GeneratePage = () => <>
  <button onClick={() => store.openRun(null)} className="mb-4 cursor-pointer text-[13px] text-muted hover:text-black">← All work sessions</button>
  <Card className="py-10 text-center">
    <h3 className="font-serif text-xl font-medium">Not implemented yet!</h3>
    <p className="mx-auto mt-2 max-w-[60ch] text-secondary">
      I (Eliot) didn't have time to create a backend to run the agent orchestration.</p>
  </Card>
</>;

export const Explorer = observer(() =>
  <section>
    <SectionHead>Dataset explorer</SectionHead>
    <p className="mb-8 max-w-[60ch] text-base">Recorded work sessions in the browser, with repeated workflows mixed in with one-off tasks and
      look-alike distractions. In multitask work sessions the worker juggles 2–3 tasks at once.</p>
    {store.runId === GENERATE ? <GeneratePage /> : store.run ? <RunDetail session={store.run} /> : <RunList />}
  </section>);
