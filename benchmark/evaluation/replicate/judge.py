"""LLM judge for workflow reconstruction and automation feasibility.

For every matched (prediction, ground-truth) pair, the judge sees the ground-truth
template (description, parameters, generic steps, and the real action sequence of
one instance) and the predicted workflow, and scores 1-5:
  reconstruction  same procedure, steps in the right order, parameters identified
  feasibility     could an automation be built from the predicted spec alone
A second judge model on the same items gives an inter-judge agreement figure;
results/judge_agreement_sample.csv is the sheet for a human spot-check.
"""
from __future__ import annotations

import json

from replicate.llm import complete

SCHEMA = {"type": "object", "required": ["reconstruction", "feasibility", "rationale"], "properties": {
    "reconstruction": {"type": "integer", "minimum": 1, "maximum": 5},
    "feasibility": {"type": "integer", "minimum": 1, "maximum": 5},
    "rationale": {"type": "string"}}}

PROMPT = """You grade a workflow-discovery system. It watched a user's browser session and
proposed a repeated workflow to automate. Compare it to the TRUE workflow.

TRUE WORKFLOW (site: {site})
Description: {true_description}
Parameters: {true_parameters}
Generic steps:
{true_steps}
Actual actions of one occurrence:
{true_actions}

PROPOSED WORKFLOW
Name: {proposed_name}
Description: {proposed_description}
Parameters: {proposed_parameters}
Steps:
{proposed_steps}

Score each 1-5:
- reconstruction: 5 = same procedure with the right steps in order and the right
  parameters; 3 = right goal but steps or parameters partly wrong or vague;
  1 = a different procedure.
- feasibility: 5 = a browser automation could be built from the proposed spec alone
  (concrete steps, every varying input named as a parameter); 1 = too vague or wrong
  to automate.
Give a one-sentence rationale.
"""


def judge_item(true_opportunity: dict, predicted_opportunity: dict, tasks: dict, model: str) -> dict:
    example_instance = true_opportunity["instances"][0]
    # Live sessions have no Mind2Web task; their generic steps are already concrete.
    example_actions = tasks.get(example_instance["task_id"], {}).get("action_reprs") or ["(not recorded; see the steps)"]
    prompt = PROMPT.format(
        site=true_opportunity.get("site", ""),
        true_description=true_opportunity["description"],
        true_parameters=", ".join(true_opportunity.get("parameters", [])),
        true_steps="\n".join(f"- {step}" for step in true_opportunity.get("steps", [])),
        true_actions="\n".join(f"- {action}" for action in example_actions),
        proposed_name=predicted_opportunity["name"],
        proposed_description=predicted_opportunity["description"],
        proposed_parameters=", ".join(predicted_opportunity.get("parameters", [])),
        proposed_steps="\n".join(f"- {step}" for step in predicted_opportunity.get("steps", [])))
    return complete(prompt, model=model, schema=SCHEMA)["data"]


def judge_session(prediction: dict, ground_truth: dict, matches: list[dict], tasks: dict, model: str) -> list[dict]:
    opportunity_by_id = {opportunity["opportunity_id"]: opportunity for opportunity in ground_truth["opportunities"]}
    judgements = []
    for matched_pair in matches:
        predicted_opportunity = prediction["opportunities"][matched_pair["rank"]]
        true_opportunity = opportunity_by_id[matched_pair["opportunity_id"]]
        judgements.append({**matched_pair, **judge_item(true_opportunity, predicted_opportunity, tasks, model),
                           "pred_name": predicted_opportunity["name"],
                           "gt_description": true_opportunity["description"]})
    return judgements


if __name__ == "__main__":
    print(json.dumps(SCHEMA))
