"""Candidate models: read a session bundle, propose ranked automation opportunities.

Conditions (the browser version of the input ablation):
  trace        the DOM action trace only: time, site, op, element role/text, typed value
  screenshots  contact sheets of the viewport frames (target element boxed, the
               stand-in for a visible cursor), with timestamps; no text trace
  both         trace + contact sheets

Models: `heuristic` (no LLM: gap segmentation + skeleton clustering) and
`haiku` / `sonnet` / `opus` through the claude CLI with structured output.
The ground truth is never read here.
"""
from __future__ import annotations

import json
import os
from difflib import SequenceMatcher
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from replicate.llm import complete

CONDITIONS = ("trace", "screenshots", "both")
SHEET_COLUMNS, SHEET_ROWS, THUMBNAIL_WIDTH, THUMBNAIL_HEIGHT = 2, 3, 760, 475
CAPTION_HEIGHT = 30

_STRING = {"type": "string"}


def _schema(instance: dict) -> dict:
    return {
        "type": "object", "required": ["opportunities"],
        "properties": {"opportunities": {"type": "array", "items": {
            "type": "object",
            "required": ["name", "description", "site", "steps", "parameters", "confidence", "instances"],
            "properties": {
                "name": _STRING, "description": _STRING, "site": _STRING,
                "steps": {"type": "array", "items": _STRING},
                "parameters": {"type": "array", "items": _STRING},
                "confidence": {"type": "number"},
                "instances": {"type": "array", "items": instance},
            }}}}}


_TIME_SPAN = {"type": "object", "required": ["start_t", "end_t"],
              "properties": {"start_t": {"type": "number"}, "end_t": {"type": "number"}}}
SPAN_SCHEMA = _schema({"type": "object", "required": ["start_t", "end_t", "values"],
                       "properties": {**_TIME_SPAN["properties"], "values": _STRING}})
SEGMENTS_SCHEMA = _schema({"type": "object", "required": ["segments", "values"],
                           "properties": {"segments": {"type": "array", "items": _TIME_SPAN}, "values": _STRING}})
ACTIONS_SCHEMA = _schema({"type": "object", "required": ["actions", "values"],
                          "properties": {"actions": {"type": "array", "items": {"type": "integer"}},
                                         "values": _STRING}})

# Prompt changes that can be switched off one by one for ablations (REPLICATE_PROMPT_FEATURES,
# comma-separated, "none" for the original prompt; default all):
#   actions  the trace is numbered and instances are lists of action numbers, not time spans
#   whole    a multi-site task is one workflow; its parts are not reported separately
#   short    short repeats count; check every site for a second use
FEATURES = ("actions", "whole", "short")
RULES = {
    "whole": """Report each workflow at the level of the whole task the person repeated, end to end.
When one task spans several sites (e.g. search a flight, then a hotel, then write up
the plan), that whole chain is ONE workflow: do not also report its parts (the flight
search alone, the hotel search alone) as separate workflows. Report the same
procedure only once.

""",
    "short": """Short procedures count too: a lookup of three or four actions done twice with different
inputs is a workflow. Before answering, go through every site in the session and check
whether the same form or flow was used again later with other values.

""",
}


def prompt_features() -> set[str]:
    value = os.environ.get("REPLICATE_PROMPT_FEATURES", ",".join(FEATURES))
    features = set() if value == "none" else set(value.split(","))
    if features - set(FEATURES):
        raise ValueError(f"unknown prompt features: {features - set(FEATURES)}")
    return features


PROMPT = """You are watching a recording of one person's browser activity over a work session.
Your job: find the WORKFLOWS WORTH AUTOMATING, i.e. procedures the person performed
two or more times with different inputs (same site, same form or flow, different
parameters), that a browser automation agent could replay given those parameters.

Not every activity is a workflow: one-off tasks and unrelated browsing are noise and
must not be reported. A single occurrence can be interrupted by other activity and
resumed later; report it as one instance spanning from its start to its end.

{rules}{inputs}

Report up to 10 opportunities, ranked by automation value (how often it recurs x how
long it takes x how mechanical it is), most valuable first. For each:
- name, one-sentence parameterised description, site
- steps: the generic procedure as short imperative steps
- parameters: the inputs that change between occurrences
- confidence in [0, 1]
{instances}"""
INSTANCES = """- instances: every occurrence you saw, with start_t / end_t in session seconds
  (the t values shown) and a short note of the parameter values used
"""
INSTANCES_AS_ACTIONS = """- instances: every occurrence you saw, each as the list of its action numbers (the #n
  shown) and a short note of the parameter values used
"""


HARD_PROMPT = """You are watching a recording of one person's browser activity over a work session.
Your job: find the WORKFLOWS WORTH AUTOMATING, i.e. procedures the person performed
two or more times with different inputs (same site, same form or flow, different
parameters), that a browser automation agent could replay given those parameters.

Not every activity is a workflow: one-off tasks and unrelated browsing are noise and
must not be reported, even when they happen on the same site as a workflow.

The person MULTITASKS: several tasks are open at once and they switch between them
mid-task, so one occurrence of a workflow is usually split into several
non-contiguous pieces with unrelated actions (sometimes on the same site) in
between. Pauses do not tell you where a task starts or ends.

{rules}{inputs}

Report up to 10 opportunities, ranked by automation value (how often it recurs x how
long it takes x how mechanical it is), most valuable first. For each:
- name, one-sentence parameterised description, site
- steps: the generic procedure as short imperative steps
- parameters: the inputs that change between occurrences
- confidence in [0, 1]
{instances}"""
HARD_INSTANCES = """- instances: every occurrence you saw. Give each as a list of segments, one per
  contiguous run of that occurrence's actions (start_t = t of the run's first
  action, end_t = t of its last action, as shown). Leave out the actions of other
  tasks in between. Add a short note of the parameter values used.
"""
HARD_INSTANCES_AS_ACTIONS = """- instances: every occurrence you saw, each as the list of its action numbers (the #n
  shown). List only that occurrence's own actions, not the other tasks' actions in
  between. Add a short note of the parameter values used.
"""


def tier_of(bundle_path: Path) -> str:
    return json.loads((bundle_path / "manifest.json").read_text())["source"].get("tier", "easy")


def load_trace(bundle_path: Path) -> list[dict]:
    return [json.loads(line) for line in (bundle_path / "traces" / "dom.jsonl").read_text().splitlines()]


def format_time(seconds: float) -> str:
    return f"{int(seconds // 60):02d}:{seconds % 60:04.1f}"


def render_trace(events: list[dict], numbered: bool = True) -> str:
    trace_lines = []
    for event in events:
        element = event["element"]
        typed_value = f' = "{event["value"]}"' if event["value"] else ""
        number = f'#{event["seq"]} ' if numbered else ""
        trace_lines.append(f'{number}t={event["t"]:.1f} ({format_time(event["t"])}) [{event["site"]}] {event["op"]} '
                           f'{element["role"] or element.get("tag", "")} "{element["text"][:80]}"{typed_value}')
    return "\n".join(trace_lines)


def _load_font(size: int):
    for font_path in ("/System/Library/Fonts/Supplemental/Arial.ttf", "/System/Library/Fonts/Helvetica.ttc",
                      "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"):
        try:
            return ImageFont.truetype(font_path, size)
        except OSError:
            continue
    return ImageFont.load_default()


def contact_sheets(bundle_path: Path, events: list[dict], output_directory: Path) -> list[Path]:
    """Tile frames 2x3 per sheet, each captioned with t and boxed on the target element."""
    output_directory.mkdir(parents=True, exist_ok=True)
    frames_per_sheet = SHEET_COLUMNS * SHEET_ROWS
    caption_font = _load_font(22)
    sheet_paths = []
    for first_event_index in range(0, len(events), frames_per_sheet):
        sheet_path = output_directory / f"sheet_{first_event_index // frames_per_sheet:03d}.jpg"
        sheet_paths.append(sheet_path)
        if sheet_path.exists():
            continue
        sheet = Image.new("RGB", (SHEET_COLUMNS * THUMBNAIL_WIDTH, SHEET_ROWS * (THUMBNAIL_HEIGHT + CAPTION_HEIGHT)),
                          "white")
        sheet_drawer = ImageDraw.Draw(sheet)
        sheet_events = events[first_event_index:first_event_index + frames_per_sheet]
        for position_on_sheet, event in enumerate(sheet_events):
            frame = Image.open(bundle_path / event["screenshot"]).convert("RGB")
            if event["element"].get("bbox"):
                box_x, box_y, box_width, box_height = event["element"]["bbox"]
                ImageDraw.Draw(frame).rectangle([box_x, box_y, box_x + box_width, box_y + box_height],
                                                outline=(230, 0, 0), width=5)
            frame = frame.resize((THUMBNAIL_WIDTH, THUMBNAIL_HEIGHT))
            cell_x = (position_on_sheet % SHEET_COLUMNS) * THUMBNAIL_WIDTH
            cell_y = (position_on_sheet // SHEET_COLUMNS) * (THUMBNAIL_HEIGHT + CAPTION_HEIGHT)
            caption = f"t={event['t']:.1f}s ({format_time(event['t'])})  frame #{event['seq']}"
            sheet_drawer.text((cell_x + 6, cell_y + 3), caption, fill="black", font=caption_font)
            sheet.paste(frame, (cell_x, cell_y + CAPTION_HEIGHT))
            sheet_drawer.rectangle([cell_x, cell_y + CAPTION_HEIGHT, cell_x + THUMBNAIL_WIDTH - 1,
                                    cell_y + CAPTION_HEIGHT + THUMBNAIL_HEIGHT - 1], outline=(120, 120, 120))
        sheet.save(sheet_path, quality=80)
    return sheet_paths


def llm_predict(bundle_path: Path, model: str, condition: str, work_directory: Path) -> dict:
    events = load_trace(bundle_path)
    features = prompt_features()
    as_actions = "actions" in features
    input_sections, sheet_paths = [], []
    if condition in ("trace", "both"):
        number_note = "#n = action number, " if as_actions else ""
        input_sections.append(f"ACTION TRACE (one line per user action; {number_note}t = seconds since session start, the "
                              "element is the one acted on, typed values after '='):\n"
                              + render_trace(events, numbered=as_actions))
    if condition in ("screenshots", "both"):
        sheet_paths = contact_sheets(bundle_path, events, work_directory / "sheets" / bundle_path.name)
        caption_note = "time t and action number (frame #n)" if as_actions else "time t"
        input_sections.append(
            f"SCREENSHOTS: the {len(sheet_paths)} attached contact sheets show the browser viewport just "
            f"before each user action, in order, {SHEET_COLUMNS * SHEET_ROWS} frames per sheet, each "
            f"captioned with its {caption_note}. The red box marks the element the user acted on.")
    is_hard_tier = tier_of(bundle_path) == "hard"
    if is_hard_tier:
        prompt_template, instances, schema = HARD_PROMPT, HARD_INSTANCES, SEGMENTS_SCHEMA
    else:
        prompt_template, instances, schema = PROMPT, INSTANCES, SPAN_SCHEMA
    if as_actions:
        instances = HARD_INSTANCES_AS_ACTIONS if is_hard_tier else INSTANCES_AS_ACTIONS
        schema = ACTIONS_SCHEMA
    rules = "".join(RULES[feature] for feature in ("whole", "short") if feature in features)
    prompt = prompt_template.format(rules=rules, inputs="\n\n".join(input_sections), instances=instances)
    reply = complete(prompt, model=model, schema=schema, images=sheet_paths)
    if as_actions:
        time_by_seq = {event["seq"]: event["t"] for event in events}
        for opportunity in reply["data"]["opportunities"]:
            for instance in opportunity["instances"]:
                instance["segments"] = segments_of(instance.get("actions", []), time_by_seq)
    return {**reply["data"], "_meta": {field: reply[field] for field in ("model", "cost_usd", "seconds", "cached")},
            "_features": sorted(features)}


def segments_of(action_numbers: list[int], time_by_seq: dict[int, float]) -> list[dict]:
    """Action numbers -> one {start_t, end_t} per run of consecutive actions (the scorer's input)."""
    runs: list[list[int]] = []
    for seq in sorted(set(action_numbers) & time_by_seq.keys()):
        if runs and seq == runs[-1][-1] + 1:
            runs[-1].append(seq)
        else:
            runs.append([seq])
    return [{"start_t": time_by_seq[run[0]], "end_t": time_by_seq[run[-1]]} for run in runs]


# --- heuristic baseline -------------------------------------------------------

def segment(events: list[dict], maximum_gap_seconds: float = 12.0) -> list[list[dict]]:
    """Split the trace into episodes at pauses or site changes."""
    episodes: list[list[dict]] = []
    for event in events:
        previous_event = episodes[-1][-1] if episodes else None
        if (previous_event and event["site"] == previous_event["site"]
                and event["t"] - previous_event["t"] < maximum_gap_seconds):
            episodes[-1].append(event)
        else:
            episodes.append([event])
    return episodes


def _episode_skeleton(episode: list[dict]) -> list[str]:
    return [f"{event['op']}:{event['element']['role']}" for event in episode]


def _best_similarity(episode: list[dict], cluster: list[list[dict]]) -> float:
    return max(SequenceMatcher(None, _episode_skeleton(episode), _episode_skeleton(member)).ratio()
               for member in cluster)


def heuristic_predict(bundle_path: Path, similarity_threshold: float = 0.5) -> dict:
    episodes = [episode for episode in segment(load_trace(bundle_path)) if len(episode) >= 2]
    clusters: list[list[list[dict]]] = []
    for episode in episodes:  # greedy single-link clustering within a site
        same_site_clusters = [cluster for cluster in clusters if cluster[0][0]["site"] == episode[0]["site"]]
        closest_cluster = max(same_site_clusters, key=lambda cluster: _best_similarity(episode, cluster), default=None)
        if closest_cluster and _best_similarity(episode, closest_cluster) >= similarity_threshold:
            closest_cluster.append(episode)
        else:
            clusters.append([episode])
    opportunities = []
    for cluster in clusters:
        if len(cluster) < 2:
            continue
        time_spent = sum(episode[-1]["t"] - episode[0]["t"] + 5 for episode in cluster)
        site = cluster[0][0]["site"]
        steps = [f"{event['op']} {event['element']['role']} \"{event['element']['text'][:40]}\""
                 for event in cluster[0]]
        opportunities.append({
            "name": f"Repeated {len(cluster[0])}-step flow on {site}",
            "description": f"Repeated sequence on {site} starting with {steps[0]}", "site": site,
            "steps": steps, "parameters": [], "confidence": min(1.0, 0.3 + 0.1 * len(cluster)),
            "instances": [{"start_t": episode[0]["t"], "end_t": episode[-1]["t"],
                           "values": ", ".join(event["value"] for event in episode if event["value"])}
                          for episode in cluster],
            "_value": time_spent})
    opportunities.sort(key=lambda opportunity: -opportunity.pop("_value"))
    return {"opportunities": opportunities[:10], "_meta": {"model": "heuristic"}}


def predict(bundle_path: Path, model: str, condition: str, work_directory: Path = Path("data/work")) -> dict:
    if model == "heuristic":
        return heuristic_predict(bundle_path)
    return llm_predict(bundle_path, model, condition, work_directory)
