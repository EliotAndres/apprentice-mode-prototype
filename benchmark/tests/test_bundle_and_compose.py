import copy
import json
from pathlib import Path

import pytest

from replicate.check_bundle import check_bundle, check_ground_truth
from replicate.compose import compose
from replicate.export import parse_repr

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SAMPLE_BUNDLE = PROJECT_ROOT / "data" / "sessions" / "s001"
SAMPLE_GROUND_TRUTH = PROJECT_ROOT / "data" / "ground_truth" / "s001.json"
TASK_INDEX = PROJECT_ROOT / "data" / "index" / "tasks.jsonl"
TEMPLATES = PROJECT_ROOT / "data" / "index" / "templates.json"


def load_tasks_and_templates() -> tuple[dict, list]:
    tasks = {task["annotation_id"]: task for task in map(json.loads, TASK_INDEX.open())}
    return tasks, json.loads(TEMPLATES.read_text())["templates"]


def all_owned_actions(ground_truth: dict) -> list[int]:
    owned_actions = [sequence_number for opportunity in ground_truth["opportunities"]
                     for workflow_instance in opportunity["instances"] for sequence_number in workflow_instance["seqs"]]
    owned_actions += [sequence_number for negative in ground_truth["negatives"] for sequence_number in negative["seqs"]]
    return sorted(owned_actions)


def median(values: list[float]) -> float:
    return sorted(values)[len(values) // 2]


def test_parse_repr():
    assert parse_repr("[combobox]  Enter pick up city -> TYPE: Brooklyn Central") == \
        {"role": "combobox", "text": "Enter pick up city"}
    assert parse_repr("[button]   -> CLICK") == {"role": "button", "text": ""}


@pytest.mark.skipif(not SAMPLE_BUNDLE.exists(), reason="sample bundle not present")
def test_sample_bundle_is_valid():
    assert check_bundle(SAMPLE_BUNDLE) == []
    assert check_ground_truth(json.loads(SAMPLE_GROUND_TRUTH.read_text()), SAMPLE_BUNDLE) == []


@pytest.mark.skipif(not SAMPLE_BUNDLE.exists(), reason="sample bundle not present")
def test_ground_truth_checker_catches_errors():
    ground_truth = json.loads(SAMPLE_GROUND_TRUTH.read_text())
    single_instance_workflow = copy.deepcopy(ground_truth)
    first_opportunity = single_instance_workflow["opportunities"][0]
    first_opportunity["instances"] = first_opportunity["instances"][:1]
    assert any("minItems" in error or "fewer than 2" in error
               for error in check_ground_truth(single_instance_workflow, SAMPLE_BUNDLE))
    doubly_owned_action = copy.deepcopy(ground_truth)
    stolen_action = doubly_owned_action["opportunities"][0]["instances"][0]["seqs"][0]
    doubly_owned_action["negatives"][0]["seqs"].append(stolen_action)
    assert any("claimed by" in error for error in check_ground_truth(doubly_owned_action, SAMPLE_BUNDLE))


@pytest.mark.skipif(not TEMPLATES.exists(), reason="index not built")
def test_compose_is_deterministic_and_consistent():
    tasks, templates = load_tasks_and_templates()
    first_plan, second_plan = compose(7, templates, tasks, set()), compose(7, templates, tasks, set())
    assert first_plan == second_plan
    ground_truth = first_plan["ground_truth"]
    assert 3 <= len(ground_truth["opportunities"]) <= 5
    assert all_owned_actions(ground_truth) == list(range(len(first_plan["events"])))  # each action owned once
    action_times = [event["t"] for event in first_plan["events"]]
    assert action_times == sorted(action_times)
    for opportunity in ground_truth["opportunities"]:
        instance_task_ids = {workflow_instance["task_id"] for workflow_instance in opportunity["instances"]}
        assert len(instance_task_ids) >= 2
        assert len({tasks[task_id]["website"] for task_id in instance_task_ids}) == 1  # one site per workflow


@pytest.mark.skipif(not TEMPLATES.exists(), reason="index not built")
def test_hard_tier_interleaves_the_same_tasks():
    tasks, templates = load_tasks_and_templates()
    easy_plan = compose(3, templates, tasks, set())
    hard_plan = compose(3, templates, tasks, set(), tier="hard")
    assert hard_plan["session_id"] == "h003" and not hard_plan["ground_truth"]["dontcare_inside_instances"]

    def task_ids_of(plan: dict) -> list[str]:
        return sorted(event["task_id"] for event in plan["events"])

    assert task_ids_of(easy_plan) == task_ids_of(hard_plan)  # same content, only the order differs
    ground_truth = hard_plan["ground_truth"]
    assert all_owned_actions(ground_truth) == list(range(len(hard_plan["events"])))
    task_count = len({event["task_id"] for event in hard_plan["events"]})
    assert len(hard_plan["segments"]) > 1.5 * task_count  # instances really are broken up
    pieces_per_instance = [len(workflow_instance["spans"]) for opportunity in ground_truth["opportunities"]
                           for workflow_instance in opportunity["instances"]]
    assert sum(piece_count > 1 for piece_count in pieces_per_instance) >= len(pieces_per_instance) / 2
    # Pauses carry no boundary signal: gaps at task switches look like gaps within a task.
    events = hard_plan["events"]
    gaps = [(next_event["t"] - event["t"], event["task_id"] != next_event["task_id"])
            for event, next_event in zip(events, events[1:])]
    median_gap_at_switch = median([gap for gap, is_switch in gaps if is_switch])
    median_gap_within_task = median([gap for gap, is_switch in gaps if not is_switch])
    assert 0.5 < median_gap_at_switch / median_gap_within_task < 2
