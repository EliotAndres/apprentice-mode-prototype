"""Chain Mind2Web tasks into long synthetic work sessions with known ground truth.

A session mixes:
- 3-5 repeated workflows: 2-6 tasks drawn from one same-site template
  (each such cluster is one ground-truth opportunity, its tasks the evidence),
- 3-5 travel one-offs (singleton templates, half from the same sites as the
  workflows, as hard negatives),
- 2-4 distractors from non-travel domains,
interleaved, with 1-2 workflow instances split by an interruption.

Two tiers, built from the SAME task selection per seed so they compare directly:

- easy (s001...): tasks run one after another with a pause between them, and
  1-2 instances are interrupted once. Task boundaries are visible as pauses.
- hard (h001...): the user multitasks. 2-3 tasks are open at once (tabs) and
  the user switches between them mid-task, so every instance is broken into
  chunks interleaved with unrelated tasks. One-offs on a workflow's own site
  are opened next to that workflow, and pauses are drawn from the same
  distribution whether or not the user switched task, so timing gives no
  boundary signal.

Mind2Web has no timing, so timing is synthetic (sampled per action and for
pauses) and marked as such in the manifest. Output is a plan: the ordered list
of task segments with per-action timestamps plus the ground truth. export.py
joins it with the fetched screenshots and DOM to write bundles.
"""
from __future__ import annotations

import argparse
import json
import math
import random
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

from replicate.data.templates import skeleton_similarity

DISTRACTOR_DOMAINS = ("Shopping", "Entertainment")


def lognormal(random_generator: random.Random, median: float, sigma: float) -> float:
    return random_generator.lognormvariate(math.log(median), sigma)


def action_seconds(random_generator: random.Random, action: dict) -> float:
    operation, typed_value = action["operation"]["op"], action["operation"].get("value", "")
    if operation == "TYPE":
        return 1.0 + 0.2 * len(typed_value) * lognormal(random_generator, 1.0, 0.3)
    return lognormal(random_generator, 3.0 if operation == "SELECT" else 2.5, 0.5)


def coherent_members(template: dict, tasks: dict[str, dict], used_task_ids: set[str],
                     minimum_similarity: float = 0.5) -> list[dict]:
    """Members whose action skeleton is close to the template's medoid.

    The LLM grouping is by intent; this keeps only instances that also share a
    procedure, so each ground-truth workflow is one automation, not a theme.
    """
    available_members = [member for member in template["members"] if member["id"] not in used_task_ids]
    if len(available_members) < 2:
        return []
    similarity = {(first["id"], second["id"]): skeleton_similarity(tasks[first["id"]], tasks[second["id"]])
                  for first in available_members for second in available_members}
    medoid = max(available_members,
                 key=lambda candidate: sum(similarity[candidate["id"], other["id"]] for other in available_members))
    return [member for member in available_members if similarity[medoid["id"], member["id"]] >= minimum_similarity]


def pick_workflows(random_generator: random.Random, templates: list[dict], tasks: dict[str, dict],
                   used_task_ids: set[str], workflow_count: int) -> list[tuple[dict, list[dict]]]:
    candidate_templates = [template for template in templates
                           if template["parameters"] and len(coherent_members(template, tasks, used_task_ids)) >= 2]
    random_generator.shuffle(candidate_templates)
    chosen_workflows, workflows_per_site = [], Counter()
    for template in sorted(candidate_templates, key=lambda template: workflows_per_site[template["website"]]):
        if len(chosen_workflows) == workflow_count:
            break
        if workflows_per_site[template["website"]] >= 1 and random_generator.random() < 0.8:
            continue  # spread workflows over sites
        members = coherent_members(template, tasks, used_task_ids)
        if len(members) < 2:
            continue
        instance_count = random_generator.randint(2, min(6, len(members)))
        chosen_instances = random_generator.sample(members, instance_count)
        used_task_ids.update(member["id"] for member in chosen_instances)
        workflows_per_site[template["website"]] += 1
        chosen_workflows.append((template, chosen_instances))
    return chosen_workflows


def interleave(random_generator: random.Random, items: list[dict],
               tasks: dict[str, dict]) -> tuple[list[dict], list[dict], float]:
    """Hard tier: advance 2-3 open tasks in random alternation, one action at a time.

    Returns (segments, events, end time). A segment is a maximal run of
    consecutive actions of one task.
    """
    # Open each same-site one-off right after a workflow instance on its site.
    items = list(items)
    for one_off_item in [item for item in items if item["role"] == "one_off"]:
        site = tasks[one_off_item["task_id"]]["website"]
        host_positions = [position for position, item in enumerate(items)
                          if item["role"] == "workflow" and tasks[item["task_id"]]["website"] == site]
        if host_positions:
            items.remove(one_off_item)
            host_positions = [position for position, item in enumerate(items)
                              if item["role"] == "workflow" and tasks[item["task_id"]]["website"] == site]
            items.insert(random_generator.choice(host_positions) + 1, one_off_item)

    waiting_items, open_tasks, current_task = list(items), [], None
    clock, next_sequence_number, events, segments = 0.0, 0, [], []
    while waiting_items or open_tasks:
        while waiting_items and (len(open_tasks) < 2 or (len(open_tasks) < 3 and random_generator.random() < 0.3)):
            open_tasks.append({"item": waiting_items.pop(0), "next": 0})
        if current_task is None or random_generator.random() > 0.6:  # stay on the current task ~60% of the time
            current_task = random_generator.choice(open_tasks)
        task = tasks[current_task["item"]["task_id"]]
        action_index = current_task["next"]
        action_duration = action_seconds(random_generator, task["actions"][action_index]) + random_generator.uniform(1, 4)
        if random_generator.random() < 0.012:
            action_duration += random_generator.uniform(120, 480)  # breaks land anywhere, mid-task included
        clock += action_duration
        events.append({"seq": next_sequence_number, "t": round(clock, 2), "task_id": task["annotation_id"],
                       "action_index": action_index})
        if segments and segments[-1]["item"] is current_task["item"]:
            segment = segments[-1]
        else:
            segment = {"item": current_task["item"], "actions": [], "seqs": [],
                       "start": round(clock - action_duration, 2), "resumed": action_index > 0}
            segments.append(segment)
        segment["actions"].append(action_index)
        segment["seqs"].append(next_sequence_number)
        segment["end"] = round(clock, 2)
        next_sequence_number += 1
        current_task["next"] += 1
        if current_task["next"] == len(task["actions"]):
            open_tasks.remove(current_task)
            current_task = None
    return segments, events, clock


def sequential(random_generator: random.Random, items: list[dict],
               tasks: dict[str, dict]) -> tuple[list[dict], list[dict], float]:
    """Easy tier: one task after another with pauses; 1-2 instances interrupted once."""
    # Segments: an interrupted instance is split around the next non-workflow item.
    segments = [{"item": item, "actions": list(range(len(tasks[item["task_id"]]["actions"])))} for item in items]
    for _ in range(random_generator.randint(1, 2)):
        candidate_positions = [
            position for position in range(len(segments) - 1)
            if segments[position]["item"]["role"] == "workflow"
            and len(segments[position]["actions"]) >= 6 and segments[position]["actions"][0] == 0
            and len(segments[position]["actions"]) == len(tasks[segments[position]["item"]["task_id"]]["actions"])]
        if not candidate_positions:
            break
        interrupted_position = random_generator.choice(candidate_positions)
        interrupting_position = next((position for position in range(interrupted_position + 1, len(segments))
                                      if segments[position]["item"]["role"] != "workflow"), None)
        if interrupting_position is None:
            continue
        interrupted_actions = segments[interrupted_position]["actions"]
        cut_point = random_generator.randint(2, len(interrupted_actions) - 2)
        head_segment = {**segments[interrupted_position], "actions": interrupted_actions[:cut_point]}
        tail_segment = {**segments[interrupted_position], "actions": interrupted_actions[cut_point:], "resumed": True}
        interrupting_segment = segments.pop(interrupting_position)
        segments[interrupted_position:interrupted_position + 1] = [head_segment, interrupting_segment, tail_segment]

    # Synthetic timing.
    clock, next_sequence_number, events = 0.0, 0, []
    for segment_position, segment in enumerate(segments):
        if segment_position:
            pause = lognormal(random_generator, 15 if segment.get("resumed") else 25, 0.6)
            if random_generator.random() < 0.12:
                pause += random_generator.uniform(180, 720)  # coffee, meeting, phone
            clock += pause
        segment["start"] = round(clock, 2)
        task = tasks[segment["item"]["task_id"]]
        for position_in_segment, action_index in enumerate(segment["actions"]):
            # Draw order matters for reproducibility: action time first, then the lead-in.
            action_duration = action_seconds(random_generator, task["actions"][action_index])
            lead_in = random_generator.uniform(4, 15) if position_in_segment == 0 else 0
            clock += action_duration + lead_in
            events.append({"seq": next_sequence_number, "t": round(clock, 2), "task_id": task["annotation_id"],
                           "action_index": action_index})
            next_sequence_number += 1
        clock += random_generator.uniform(1.5, 4)
        segment["end"] = round(clock, 2)
        segment["seqs"] = [event["seq"] for event in events if event["task_id"] == task["annotation_id"]
                           and event["action_index"] in segment["actions"]]
    return segments, events, clock


def compose(seed: int, templates: list[dict], tasks: dict[str, dict], used_task_ids: set[str],
            tier: str = "easy") -> dict:
    random_generator = random.Random(seed)
    workflows = pick_workflows(random_generator, templates, tasks, used_task_ids, random_generator.randint(3, 5))
    workflow_sites = {template["website"] for template, _ in workflows}

    singleton_task_ids = [template["members"][0]["id"] for template in templates if len(template["members"]) == 1]
    singleton_task_ids = [task_id for task_id in singleton_task_ids if task_id not in used_task_ids]
    same_site_singletons = [task_id for task_id in singleton_task_ids if tasks[task_id]["website"] in workflow_sites]
    one_off_count = random_generator.randint(3, 5)
    one_off_ids = random_generator.sample(same_site_singletons,
                                          min(len(same_site_singletons), one_off_count // 2 + 1))
    one_off_ids += random_generator.sample([task_id for task_id in singleton_task_ids if task_id not in one_off_ids],
                                           one_off_count - len(one_off_ids))
    used_task_ids.update(one_off_ids)

    distractor_pool = [task_id for task_id, task in tasks.items()
                       if task["domain"] in DISTRACTOR_DOMAINS and task_id not in used_task_ids]
    distractor_ids = random_generator.sample(distractor_pool, random_generator.randint(2, 4))
    used_task_ids.update(distractor_ids)

    items = [{"task_id": member["id"], "role": "workflow", "template": template, "values": member["values"]}
             for template, chosen_instances in workflows for member in chosen_instances]
    items += [{"task_id": task_id, "role": "one_off"} for task_id in one_off_ids]
    items += [{"task_id": task_id, "role": "distractor"} for task_id in distractor_ids]
    random_generator.shuffle(items)
    # Avoid long back-to-back runs of one template: a couple of swaps is enough.
    for _ in range(50):
        adjacent_repeats = [position for position in range(1, len(items)) if items[position].get("template")
                            and items[position].get("template") is items[position - 1].get("template")]
        if not adjacent_repeats:
            break
        first_position, swap_position = adjacent_repeats[0], random_generator.randrange(len(items))
        items[first_position], items[swap_position] = items[swap_position], items[first_position]

    if tier == "hard":
        segments, events, session_end = interleave(random.Random(seed + 100_000), items, tasks)
    else:
        segments, events, session_end = sequential(random_generator, items, tasks)

    time_of_sequence_number = {event["seq"]: event["t"] for event in events}

    def instance(task_id: str) -> dict:
        task_segments = [segment for segment in segments if segment["item"]["task_id"] == task_id]
        task = tasks[task_id]
        # Evidence spans are what a recording can show: first to last action of each
        # segment. The synthetic lead-in before the first action is not observable.
        return {"task_id": task_id, "task": task["task"], "site": task["website"],
                "spans": [[time_of_sequence_number[segment["seqs"][0]], time_of_sequence_number[segment["seqs"][-1]]]
                          for segment in task_segments],
                "seqs": [sequence_number for segment in task_segments for sequence_number in segment["seqs"]],
                "seconds": round(sum(segment["end"] - segment["start"] for segment in task_segments), 1)}

    session_id = f"{'h' if tier == 'hard' else 's'}{seed:03d}"
    opportunities = []
    for opportunity_number, (template, chosen_instances) in enumerate(workflows):
        instances = [{**instance(member["id"]), "values": member["values"]} for member in chosen_instances]
        instances.sort(key=lambda workflow_instance: workflow_instance["spans"][0][0])
        opportunities.append({
            "opportunity_id": f"{session_id}-op{opportunity_number}", "template_id": template["template_id"],
            "site": template["website"], "name": template["name"], "description": template["description"],
            "parameters": template["parameters"], "steps": template["steps"],
            "observed_seconds": round(sum(workflow_instance["seconds"] for workflow_instance in instances), 1),
            "instances": instances})
    negatives = [{**instance(item["task_id"]), "kind": item["role"]} for item in items if item["role"] != "workflow"]
    started_at = datetime(2026, 9, 1, 9, 0, tzinfo=timezone.utc) + timedelta(days=seed)
    return {
        "session_id": session_id, "seed": seed, "tier": tier, "started_at": started_at.isoformat(),
        "duration_s": round(session_end, 2),
        "events": events,
        "segments": [{"task_id": segment["item"]["task_id"], "role": segment["item"]["role"],
                      "start": segment["start"], "end": segment["end"], "actions": segment["actions"],
                      "resumed": bool(segment.get("resumed"))}
                     for segment in segments],
        "ground_truth": {"session_id": session_id, "tier": tier,
                         # Easy: a model may report an interrupted instance as one span, so other
                         # tasks' actions inside it are don't-care. Hard: instances are reported as
                         # segments, so every covered action counts.
                         "dontcare_inside_instances": tier == "easy",
                         "action_t": [event["t"] for event in events],
                         "opportunities": opportunities, "negatives": negatives},
    }


def main() -> None:
    argument_parser = argparse.ArgumentParser()
    argument_parser.add_argument("--index", type=Path, default=Path("data/index/tasks.jsonl"))
    argument_parser.add_argument("--templates", type=Path, default=Path("data/index/templates.json"))
    argument_parser.add_argument("--out", type=Path, default=Path("data/plans"))
    argument_parser.add_argument("--sessions", type=int, default=8)
    argument_parser.add_argument("--seed", type=int, default=1)
    argument_parser.add_argument("--tiers", default="easy,hard")
    arguments = argument_parser.parse_args()
    tasks = {task["annotation_id"]: task for task in map(json.loads, arguments.index.open())}
    templates = json.loads(arguments.templates.read_text())["templates"]
    arguments.out.mkdir(parents=True, exist_ok=True)
    for tier in arguments.tiers.split(","):
        used_task_ids: set[str] = set()  # tasks are unique within a tier; the tiers share them by design
        for seed in range(arguments.seed, arguments.seed + arguments.sessions):
            report(compose(seed, templates, tasks, used_task_ids, tier), arguments.out)


def report(plan: dict, output_directory: Path) -> None:
    (output_directory / f"{plan['session_id']}.json").write_text(json.dumps(plan, indent=1))
    ground_truth = plan["ground_truth"]
    instance_counts = "/".join(str(len(opportunity["instances"])) for opportunity in ground_truth["opportunities"])
    print(f"{plan['session_id']}: {len(plan['events'])} actions, {plan['duration_s'] / 60:.0f} min, "
          f"{len(ground_truth['opportunities'])} workflows ({instance_counts} instances), "
          f"{len(ground_truth['negatives'])} negatives, {len(plan['segments'])} segments, "
          f"{sum(segment['resumed'] for segment in plan['segments'])} resumptions")


if __name__ == "__main__":
    main()
