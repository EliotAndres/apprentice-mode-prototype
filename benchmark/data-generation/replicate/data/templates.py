"""Group Mind2Web travel tasks into workflow templates, per website.

A *template* is one repeatable procedure on one site: the same form or flow,
filled with different parameters ("check in with confirmation number N and last
name L"). Tasks that share a template become instances of one ground-truth
opportunity when chained into a session; singletons are one-off negatives.

The grouping is done by an LLM (one call per site, cached) from the task text
and the action sequence, then sanity-checked structurally: members of a
template must share most of their operation/element-role skeleton.
"""
from __future__ import annotations

import argparse
import json
import re
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from difflib import SequenceMatcher
from pathlib import Path

from replicate.llm import complete

PROMPT = """You are building ground truth for a workflow-discovery benchmark.

Below are tasks recorded on the website "{site}". Each has the task text and the
UI actions taken. Group them into WORKFLOW TEMPLATES: two tasks share a template
only if a single parameterised automation could do both, i.e. the same procedure
(same form or flow on this site) with different input values. Same intent but a
clearly different procedure (e.g. flight search vs flight status) = different
templates. Do not force groups; a task unlike the others is its own template.

For each template give:
- "template_id": snake_case, e.g. "check_flight_status_by_route"
- "name": short human name
- "description": one sentence, parameterised, e.g. "Look up the status of a flight between {{origin}} and {{destination}} on {{date}}"
- "parameters": list of slot names
- "steps": the generic procedure, 3-8 short imperative steps
- "members": list of {{"id": task id, "values": [{{"slot": ..., "value": ...}}]}}

Every task id must appear in exactly one template.

TASKS:
{tasks}
"""


_STRING = {"type": "string"}
SCHEMA = {"type": "object", "required": ["templates"], "properties": {"templates": {"type": "array", "items": {
    "type": "object", "required": ["template_id", "name", "description", "parameters", "steps", "members"],
    "properties": {
        "template_id": _STRING, "name": _STRING, "description": _STRING,
        "parameters": {"type": "array", "items": _STRING}, "steps": {"type": "array", "items": _STRING},
        "members": {"type": "array", "items": {"type": "object", "required": ["id", "values"], "properties": {
            "id": _STRING, "values": {"type": "array", "items": {"type": "object", "required": ["slot", "value"],
                                                                 "properties": {"slot": _STRING, "value": _STRING}}}}}},
    }}}}}


def skeleton(task: dict) -> list[str]:
    """Operation + element-role skeleton, e.g. ['CLICK:button', 'TYPE:combobox', ...]."""
    skeleton_steps = []
    for action in task["actions"]:
        role_match = re.match(r"\[(\w+)\]", action["repr"] or "")
        skeleton_steps.append(f"{action['operation']['op']}:{role_match.group(1) if role_match else '?'}")
    return skeleton_steps


def skeleton_similarity(first_task: dict, second_task: dict) -> float:
    return SequenceMatcher(None, skeleton(first_task), skeleton(second_task)).ratio()


def group_site(site: str, site_tasks: list[dict], model: str) -> list[dict]:
    task_lines = []
    for task in site_tasks:
        action_list = "; ".join(action_repr for action_repr in task["action_reprs"])
        task_lines.append(f"- id={task['annotation_id'][:8]} task: {task['task']}\n  actions: {action_list}")
    reply = complete(PROMPT.format(site=site, tasks="\n".join(task_lines)), model=model, schema=SCHEMA)
    templates = reply["data"]["templates"]
    full_id_by_short_id = {task["annotation_id"][:8]: task["annotation_id"] for task in site_tasks}
    assigned_task_ids = set()
    for template in templates:
        template["template_id"] = f"{site}:{template['template_id']}"
        template["website"] = site
        members = []
        for member in template["members"]:
            full_task_id = full_id_by_short_id.get(str(member["id"])[:8])
            if full_task_id and full_task_id not in assigned_task_ids:
                assigned_task_ids.add(full_task_id)
                members.append({"id": full_task_id, "values": {slot_value["slot"]: slot_value["value"]
                                                               for slot_value in member.get("values", [])}})
        template["members"] = members
    for task in site_tasks:  # anything the model dropped becomes its own singleton
        if task["annotation_id"] not in assigned_task_ids:
            templates.append({"template_id": f"{site}:unassigned_{task['annotation_id'][:8]}", "website": site,
                              "name": task["task"], "description": task["task"], "parameters": [], "steps": [],
                              "members": [{"id": task["annotation_id"], "values": {}}]})
    return [template for template in templates if template["members"]]


def build(index_path: Path, output_path: Path, model: str, domain: str = "Travel") -> None:
    tasks = [json.loads(line) for line in index_path.open()]
    task_by_id = {task["annotation_id"]: task for task in tasks}
    tasks_by_site = defaultdict(list)
    for task in tasks:
        if task["domain"] == domain:
            tasks_by_site[task["website"]].append(task)
    all_templates = []
    sites = sorted(tasks_by_site)
    with ThreadPoolExecutor(6) as executor:
        templates_per_site = list(executor.map(lambda site: group_site(site, tasks_by_site[site], model), sites))
    for site, site_templates in zip(sites, templates_per_site):
        for template in site_templates:
            member_tasks = [task_by_id[member["id"]] for member in template["members"]]
            pairwise_similarities = [skeleton_similarity(first_task, second_task)
                                     for position, first_task in enumerate(member_tasks)
                                     for second_task in member_tasks[position + 1:]]
            template["skeleton_similarity"] = round(min(pairwise_similarities), 3) if pairwise_similarities else None
        all_templates += site_templates
        repeated_templates = [template for template in site_templates if len(template["members"]) >= 2]
        print(f"{site}: {len(tasks_by_site[site])} tasks -> {len(site_templates)} templates, "
              f"{len(repeated_templates)} repeated", flush=True)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps({"model": model, "templates": all_templates}, indent=1))
    repeated_templates = [template for template in all_templates if len(template["members"]) >= 2]
    print(f"{len(all_templates)} templates, {len(repeated_templates)} with >=2 instances "
          f"({sum(len(template['members']) for template in repeated_templates)} tasks)")


if __name__ == "__main__":
    argument_parser = argparse.ArgumentParser()
    argument_parser.add_argument("--index", type=Path, default=Path("data/index/tasks.jsonl"))
    argument_parser.add_argument("--out", type=Path, default=Path("data/index/templates.json"))
    argument_parser.add_argument("--model", default="sonnet")
    arguments = argument_parser.parse_args()
    build(arguments.index, arguments.out, arguments.model)
