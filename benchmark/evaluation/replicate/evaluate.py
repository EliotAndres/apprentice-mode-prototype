"""Run candidate models over the benchmark, score them, judge matched workflows.

    python -m replicate.evaluate --models heuristic,haiku,sonnet,opus --conditions trace,screenshots,both

Writes results/predictions/<model>__<condition>/<session>.json, results/scores.json
and results/results.md. LLM calls are cached in data/llm_cache, so a re-run with
REPLICATE_OFFLINE=1 rebuilds every number without calling a model.
"""
from __future__ import annotations

import argparse
import csv
import json
import random
import zlib
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from replicate.judge import judge_session
from replicate.models import CONDITIONS, predict
from replicate.score import METRICS, aggregate, score_session

PRIMARY_JUDGE, SECOND_JUDGE = "sonnet", "opus"
SPLIT = json.loads((Path(__file__).parent.parent / "split.json").read_text())  # see PROTOCOL.md
# Table group -> which session scores it averages over. "all" pools human multitask and agent sessions.
GROUPS = {
    "easy": lambda row: row["tier"] == "easy", "hard": lambda row: row["tier"] == "hard",
    "agent": lambda row: row["tier"] == "agent", "all": lambda row: row["tier"] in ("hard", "agent"),
    "dev": lambda row: row["session"] in SPLIT["dev"], "test": lambda row: row["session"] in SPLIT["test"],
    "test_human": lambda row: row["session"] in SPLIT["test"] and row["tier"] == "hard",
    "test_agent": lambda row: row["session"] in SPLIT["test"] and row["tier"] == "agent",
}


def run_one(bundle_path: Path, model: str, condition: str, results_directory: Path) -> dict:
    prediction_path = results_directory / "predictions" / f"{model}__{condition}" / f"{bundle_path.name}.json"
    if prediction_path.exists():
        return json.loads(prediction_path.read_text())
    prediction = predict(bundle_path, model, condition)
    prediction_path.parent.mkdir(parents=True, exist_ok=True)
    prediction_path.write_text(json.dumps(prediction, indent=1))
    return prediction


def main() -> None:
    argument_parser = argparse.ArgumentParser()
    argument_parser.add_argument("--bench", type=Path, default=Path("data"))
    argument_parser.add_argument("--out", type=Path, default=Path("data/results"))
    argument_parser.add_argument("--index", type=Path, default=Path("data/index/tasks.jsonl"))
    argument_parser.add_argument("--models", default="heuristic,haiku,sonnet,opus")
    argument_parser.add_argument("--conditions", default=",".join(CONDITIONS))
    argument_parser.add_argument("--sessions", default="", help="comma-separated ids; default all")
    argument_parser.add_argument("--workers", type=int, default=4)
    argument_parser.add_argument("--no-judge", action="store_true")
    arguments = argument_parser.parse_args()
    model_names = arguments.models.split(",")

    def conditions_for(model: str) -> list[str]:
        return ["trace"] if model == "heuristic" else arguments.conditions.split(",")

    # Sessions come from the ground truth, so offline re-scoring works without the bundles.
    bundle_paths = [arguments.bench / "sessions" / ground_truth_path.stem
                    for ground_truth_path in sorted((arguments.bench / "ground_truth").glob("*.json"))]
    if arguments.sessions:
        bundle_paths = [bundle_path for bundle_path in bundle_paths
                        if bundle_path.name in arguments.sessions.split(",")]
    runs = [(bundle_path, model, condition) for model in model_names for condition in conditions_for(model)
            for bundle_path in bundle_paths]

    def prediction_job(run: tuple[Path, str, str]):
        bundle_path, model, condition = run
        try:
            return run, run_one(bundle_path, model, condition, arguments.out)
        except LookupError:  # offline and never run (e.g. a model not run on new sessions): leave it out
            return run, None
        except Exception as error:  # keep going; a failed run scores as empty
            print(f"FAILED {model}/{condition}/{bundle_path.name}: {error}", flush=True)
            return run, {"opportunities": [], "_meta": {"error": str(error)}}

    with ThreadPoolExecutor(arguments.workers) as executor:
        predictions = {run: prediction for run, prediction in executor.map(prediction_job, runs)
                       if prediction is not None}

    tasks = ({task["annotation_id"]: task for task in map(json.loads, arguments.index.open())}
             if not arguments.no_judge else {})

    def judge_job(scored_run: tuple) -> list[dict]:
        (bundle_path, model, condition), session_score, ground_truth = scored_run
        run_labels = {"model": model, "condition": condition, "session": bundle_path.name}
        prediction = predictions[bundle_path, model, condition]
        # Second judge on a deterministic ~1-in-5 sample, for an agreement figure.
        second_judge_sample = [
            matched_pair for matched_pair in session_score["matches"]
            if zlib.crc32(f"{model}{condition}{bundle_path.name}{matched_pair['opportunity_id']}".encode()) % 5 == 0]
        try:
            judgements = [{**run_labels, "judge": PRIMARY_JUDGE, **judgement} for judgement in judge_session(
                prediction, ground_truth, session_score["matches"], tasks, PRIMARY_JUDGE)]
            judgements += [{**run_labels, "judge": SECOND_JUDGE, **judgement} for judgement in judge_session(
                prediction, ground_truth, second_judge_sample, tasks, SECOND_JUDGE)]
        except LookupError as error:  # offline and not judged yet: report it, keep the other numbers
            print(f"judge skipped for {model}/{condition}/{bundle_path.name}: {error}", flush=True)
            return []
        return judgements

    per_session_scores, scored_runs, judgements = [], [], []
    for (bundle_path, model, condition), prediction in predictions.items():
        ground_truth = json.loads((arguments.bench / "ground_truth" / f"{bundle_path.name}.json").read_text())
        session_score = score_session(prediction, ground_truth)
        # Live (agent-recorded) ground truth has no tier; it gets its own table.
        per_session_scores.append({"tier": ground_truth.get("tier") or "agent", "model": model, "condition": condition,
                                   "session": bundle_path.name, **session_score,
                                   "error": prediction.get("_meta", {}).get("error")})
        scored_runs.append(((bundle_path, model, condition), session_score, ground_truth))
    if not arguments.no_judge:
        with ThreadPoolExecutor(arguments.workers) as executor:
            for run_judgements in executor.map(judge_job, scored_runs):
                judgements += run_judgements

    table = []
    for tier, in_group in GROUPS.items():
        group_sessions = {row["session"] for row in per_session_scores if in_group(row)}
        for model in model_names:
            for condition in conditions_for(model):
                rows = [session_score for session_score in per_session_scores if in_group(session_score)
                        and session_score["model"] == model and session_score["condition"] == condition]
                # Only models run on every session of the group, so averages stay comparable.
                if not rows or {row["session"] for row in rows} != group_sessions:
                    continue
                table_row = aggregate(rows)
                primary_judgements = [judgement for judgement in judgements
                                      if judgement["model"] == model and judgement["condition"] == condition
                                      and judgement["judge"] == PRIMARY_JUDGE
                                      and judgement["session"] in group_sessions]
                for score_name in ("reconstruction", "feasibility"):
                    table_row[score_name] = (sum(judgement[score_name] for judgement in primary_judgements)
                                             / len(primary_judgements) if primary_judgements else None)
                table_row["failed_runs"] = sum(bool(row["error"]) for row in rows)
                table_row.update(bootstrap(rows))
                run_metadata = [predictions[bundle_path, model, condition].get("_meta", {})
                                for bundle_path in bundle_paths if (bundle_path, model, condition) in predictions
                                and bundle_path.name in group_sessions]
                call_seconds = sorted(metadata.get("seconds") or 0 for metadata in run_metadata)
                table_row["median_seconds"] = call_seconds[len(call_seconds) // 2] if call_seconds else None
                table_row["mean_cost_usd"] = (sum(metadata.get("cost_usd") or 0 for metadata in run_metadata)
                                              / len(run_metadata) if run_metadata else None)
                table.append({"tier": tier, "model": model, "condition": condition, **table_row})

    arguments.out.mkdir(parents=True, exist_ok=True)
    (arguments.out / "scores.json").write_text(json.dumps(
        {"table": table, "per_session": per_session_scores, "judgements": judgements}, indent=1))
    write_agreement(judgements, arguments.out)
    results_markdown = render_table(table, judgements)
    (arguments.out / "results.md").write_text(results_markdown)
    print(results_markdown)


def bootstrap(session_scores: list[dict], resample_count: int = 4000, seed: int = 0) -> dict:
    """95% percentile intervals for R@10, P@5 and the solved rate, resampling sessions."""
    random_generator = random.Random(seed)
    intervals = {}
    for metric in ("r_at_10", "p_at_5", "solved"):
        resampled_means = sorted(
            sum(random_generator.choice(session_scores)[metric] for _ in session_scores) / len(session_scores)
            for _ in range(resample_count))
        intervals[f"{metric}_ci"] = [resampled_means[int(0.025 * resample_count)],
                                     resampled_means[int(0.975 * resample_count) - 1]]
    return intervals


def _judgement_key(judgement: dict) -> tuple:
    return judgement["model"], judgement["condition"], judgement["session"], judgement["opportunity_id"]


def _judge_pairs(judgements: list[dict]) -> list[tuple[dict, dict]]:
    """(primary, second) judgements of the same item."""
    primary_by_key = {_judgement_key(judgement): judgement for judgement in judgements
                      if judgement["judge"] == PRIMARY_JUDGE}
    second_by_key = {_judgement_key(judgement): judgement for judgement in judgements
                     if judgement["judge"] == SECOND_JUDGE}
    return [(primary_by_key[key], second_by_key[key]) for key in sorted(primary_by_key) if key in second_by_key]


def write_agreement(judgements: list[dict], results_directory: Path) -> None:
    """Pair the two judges' scores per item, and write a sample sheet for a human pass."""
    judge_pairs = _judge_pairs(judgements)
    with (results_directory / "judge_agreement_sample.csv").open("w", newline="") as csv_file:
        csv_writer = csv.writer(csv_file)
        csv_writer.writerow(["model", "condition", "session", "opportunity_id", "gt_description", "pred_name",
                             f"{PRIMARY_JUDGE}_reconstruction", f"{SECOND_JUDGE}_reconstruction",
                             f"{PRIMARY_JUDGE}_feasibility", f"{SECOND_JUDGE}_feasibility",
                             f"{PRIMARY_JUDGE}_rationale", "human_reconstruction", "human_feasibility"])
        for primary, second in judge_pairs[::max(1, len(judge_pairs) // 25)][:25]:
            csv_writer.writerow([*_judgement_key(primary), primary["gt_description"], primary["pred_name"],
                                 primary["reconstruction"], second["reconstruction"],
                                 primary["feasibility"], second["feasibility"], primary["rationale"], "", ""])


def agreement(judgements: list[dict]) -> dict:
    judge_pairs = _judge_pairs(judgements)
    if not judge_pairs:
        return {}
    summary = {"n": len(judge_pairs)}
    for score_name in ("reconstruction", "feasibility"):
        summary[f"{score_name}_within1"] = (sum(abs(primary[score_name] - second[score_name]) <= 1
                                                for primary, second in judge_pairs) / len(judge_pairs))
        summary[f"{score_name}_exact"] = (sum(primary[score_name] == second[score_name]
                                              for primary, second in judge_pairs) / len(judge_pairs))
    return summary


def render_table(table: list[dict], judgements: list[dict]) -> str:
    header = ["model", "input", "P@5", "R@10", "R@10 95% CI", "evidence IoU", "inst F1", "FP neg", "time rec*", "param rec", "solved",
              "recon", "feas", "s/session", "$/session"]

    def two_decimals(value):
        return "-" if value is None else f"{value:.2f}"

    lines, current_tier = [], None
    for table_row in table:
        if table_row["tier"] != current_tier:
            current_tier = table_row["tier"]
            lines += [f"{chr(10) if lines else ''}### {current_tier} tier ({table_row['sessions']} sessions)\n",
                      "| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
        recall_interval = f"{table_row['r_at_10_ci'][0]:.2f}-{table_row['r_at_10_ci'][1]:.2f}"
        cells = [table_row["model"], table_row["condition"], two_decimals(table_row["p_at_5"]),
                 two_decimals(table_row["r_at_10"]), recall_interval,
                 *(two_decimals(table_row[metric]) for metric in METRICS[2:]),
                 two_decimals(table_row["reconstruction"]), two_decimals(table_row["feasibility"]),
                 f"{table_row['median_seconds']:.0f}", two_decimals(table_row["mean_cost_usd"])]
        lines.append("| " + " | ".join(cells) + " |")
    judge_agreement = agreement(judgements)
    agreement_note = (
        f"\nJudge: {PRIMARY_JUDGE} (1-5 scales); second judge {SECOND_JUDGE} on the same {judge_agreement['n']} "
        f"items agrees within 1 point on {judge_agreement['reconstruction_within1']:.0%} (reconstruction) and "
        f"{judge_agreement['feasibility_within1']:.0%} (feasibility), exactly on "
        f"{judge_agreement['reconstruction_exact']:.0%} / {judge_agreement['feasibility_exact']:.0%}.\n"
        if judge_agreement else "")
    return ("\n".join(lines) + "\n\nMacro-averaged over sessions; 'all' pools the hard (human multitask) and agent tiers. "
            "CI: session bootstrap. *time recall uses synthetic timing; indicative only. Cost is the CLI-reported "
            "API-equivalent cost per session, latency the median wall time per call.\n" + agreement_note)


if __name__ == "__main__":
    main()
