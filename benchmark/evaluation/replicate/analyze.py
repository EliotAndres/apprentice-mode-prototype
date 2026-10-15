"""Statistics for PROTOCOL.md: ablation effects, error taxonomy, input comparison.

Each run directory is an `evaluate --out` directory (predictions/<model>__<condition>/<session>.json).
Predictions are re-scored here, so the ambiguous relabel can be switched off without new calls.

    python -m replicate.analyze ablation <ablation_dir> --sessions dev
    python -m replicate.analyze taxonomy <variant_dir> --sessions dev --condition trace
    python -m replicate.analyze compare <test_dir> --sessions test --a trace --b both
    python -m replicate.analyze audit <audit_dir>     (blind relabels, one <session>.json each)

<ablation_dir>/<variant>/r<n>/ holds one evaluate run per variant and run; <variant_dir> is one of them.
"""
from __future__ import annotations

import argparse
import json
import math
import random
from collections import Counter
from pathlib import Path

from replicate.score import PADDING_SECONDS, SOLVED_IOU, covered, drop_ambiguous, match, score_session

SPLIT = json.loads((Path(__file__).parent.parent / "split.json").read_text())


def ground_truth(session: str, ambiguous: bool = True, bench: Path = Path("data")) -> dict:
    """The session's ground truth; ambiguous=False turns 'ambiguous' negatives back into one-offs."""
    truth = json.loads((bench / "ground_truth" / f"{session}.json").read_text())
    if not ambiguous:
        truth["negatives"] = [{**negative, "kind": "one_off"} if negative.get("kind") == "ambiguous" else negative
                              for negative in truth["negatives"]]
    return truth


def run_scores(run_directory: Path, sessions: list[str], condition: str = "trace", model: str = "sonnet",
               ambiguous: bool = True) -> dict[str, dict]:
    scores = {}
    for session in sessions:
        prediction_path = run_directory / "predictions" / f"{model}__{condition}" / f"{session}.json"
        if prediction_path.exists():
            scores[session] = score_session(json.loads(prediction_path.read_text()), ground_truth(session, ambiguous))
    return scores


def per_session_mean(run_directories: list[Path], sessions: list[str], metric: str = "solved", **kwargs) -> dict:
    """session -> mean of the metric over the runs that scored it."""
    totals: dict[str, list[float]] = {}
    for run_directory in run_directories:
        for session, score in run_scores(run_directory, sessions, **kwargs).items():
            totals.setdefault(session, []).append(score[metric])
    return {session: sum(values) / len(values) for session, values in totals.items()}


def paired_bootstrap(differences: list[float], resample_count: int = 4000, seed: int = 0) -> tuple[float, float]:
    random_generator = random.Random(seed)
    means = sorted(sum(random_generator.choice(differences) for _ in differences) / len(differences)
                   for _ in range(resample_count))
    return means[int(0.025 * resample_count)], means[int(0.975 * resample_count) - 1]


def mcnemar_exact(only_a: int, only_b: int) -> float:
    """Two-sided exact McNemar p-value from the discordant pair counts."""
    discordant = only_a + only_b
    if not discordant:
        return 1.0
    only_a, only_b, discordant = int(only_a), int(only_b), int(discordant)
    tail = sum(math.comb(discordant, k) for k in range(min(only_a, only_b) + 1)) / 2 ** discordant
    return min(1.0, 2 * tail)


def failure_codes(prediction: dict, truth: dict) -> list[str]:
    """Why a session is not solved: one code per problem (empty when solved)."""
    predictions = drop_ambiguous(prediction.get("opportunities", [])[:10], truth, PADDING_SECONDS)  # as scored
    loose = match(predictions, truth)
    strict = {true_index for _, true_index, _ in match(predictions, truth, threshold=SOLVED_IOU, padding_seconds=0)}
    codes = []
    for true_index, opportunity in enumerate(truth["opportunities"]):
        if true_index in strict:
            continue
        if true_index in {index for _, index, _ in loose}:
            codes.append("grounding")  # found, but evidence IoU < 0.5 without padding
        else:
            mean_length = sum(len(instance["seqs"]) for instance in opportunity["instances"]) / len(opportunity["instances"])
            codes.append("missed_short" if mean_length <= 5 else "missed")
    label = {}
    for true_index, opportunity in enumerate(truth["opportunities"]):
        for instance in opportunity["instances"]:
            label.update({seq: ("workflow", true_index) for seq in instance["seqs"]})
    for negative in truth["negatives"]:
        label.update({seq: (negative.get("kind", "one_off"), None) for seq in negative["seqs"]})
    matched_ranks = {rank for rank, _, _ in loose}
    for rank, predicted in enumerate(predictions):
        if rank in matched_ranks:
            continue
        covered_actions = covered(predicted.get("instances", []), truth["action_t"], 0)
        if not covered_actions:
            codes.append("extra_empty")
            continue
        kind = Counter(label.get(seq, ("unlabelled", None))[0] for seq in covered_actions).most_common(1)[0][0]
        codes.append({"workflow": "extra_substep", "ambiguous": "extra_ambiguous",
                      "unlabelled": "extra_unlabelled"}.get(kind, "extra_noise"))
    return codes


def cohen_kappa(first: list[bool], second: list[bool]) -> float:
    total = len(first)
    observed = sum(a == b for a, b in zip(first, second)) / total
    expected = (sum(first) / total) * (sum(second) / total) + (1 - sum(first) / total) * (1 - sum(second) / total)
    return 1.0 if expected == 1 else (observed - expected) / (1 - expected)


def audit_agreement(audit: dict, truth: dict) -> dict:
    """Blind relabel vs ground truth: action-level kappa, workflow matches, ambiguous groups (Rule B)."""
    action_count = len(truth["action_t"])
    audited = {seq for group in audit["groups"] if group["repeated_workflow"]
               for occurrence in group["occurrences"] for seq in occurrence["actions"]}
    in_workflow = {seq for opportunity in truth["opportunities"] for instance in opportunity["instances"]
                   for seq in instance["seqs"]}
    ambiguous_groups: dict[str, set[int]] = {}
    for negative in truth["negatives"]:
        if negative.get("kind") == "ambiguous":
            ambiguous_groups.setdefault(negative["task_id"].split(".")[0], set()).update(negative["seqs"])
    in_ambiguous = set().union(*ambiguous_groups.values()) if ambiguous_groups else set()

    def best_group(actions: set[int]) -> tuple[float, dict | None]:
        scored = [(len(actions & group_actions) / len(actions | group_actions), group) for group, group_actions in
                  ((group, {seq for occurrence in group["occurrences"] for seq in occurrence["actions"]})
                   for group in audit["groups"])]
        return max(scored, key=lambda item: item[0], default=(0.0, None))

    workflows = []
    for opportunity in truth["opportunities"]:
        overlap, group = best_group({seq for instance in opportunity["instances"] for seq in instance["seqs"]})
        workflows.append({"name": opportunity["name"], "iou": round(overlap, 2),
                          "agrees": bool(group and group["repeated_workflow"] and overlap >= 0.5)})
    ambiguous = []
    for workflow_id, actions in ambiguous_groups.items():
        overlap, group = best_group(actions)
        ambiguous.append({"workflow": workflow_id, "iou": round(overlap, 2),
                          "auditor_says_repeated": bool(group and group["repeated_workflow"]),
                          "auditor_group": group and group["name"]})
    return {
        "kappa": cohen_kappa([seq in in_workflow for seq in range(action_count)],
                             [seq in audited for seq in range(action_count)]),
        "kappa_ambiguous_as_workflow": cohen_kappa([seq in in_workflow | in_ambiguous for seq in range(action_count)],
                                                   [seq in audited for seq in range(action_count)]),
        "workflows": workflows, "ambiguous": ambiguous,
        "auditor_extra": [group["name"] for group in audit["groups"] if group["repeated_workflow"]
                          and best_group_match(group, truth) < 0.5],
    }


def best_group_match(group: dict, truth: dict) -> float:
    """Highest IoU of an auditor group with any ground-truth workflow or ambiguous attempt group."""
    actions = {seq for occurrence in group["occurrences"] for seq in occurrence["actions"]}
    candidates = [{seq for instance in opportunity["instances"] for seq in instance["seqs"]}
                  for opportunity in truth["opportunities"]]
    by_workflow: dict[str, set[int]] = {}
    for negative in truth["negatives"]:
        if "." in negative["task_id"]:
            by_workflow.setdefault(negative["task_id"].split(".")[0], set()).update(negative["seqs"])
    candidates += list(by_workflow.values())
    return max((len(actions & candidate) / len(actions | candidate) for candidate in candidates), default=0.0)


def sessions_for(name: str) -> list[str]:
    if name in ("test_human", "test_agent"):
        return [session for session in SPLIT["test"] if session.startswith("h") == (name == "test_human")]
    return SPLIT[name] if name in SPLIT else name.split(",")


def main() -> None:
    argument_parser = argparse.ArgumentParser()
    subcommands = argument_parser.add_subparsers(dest="command", required=True)
    for name in ("ablation", "taxonomy", "compare", "audit"):
        subparser = subcommands.add_parser(name)
        subparser.add_argument("directory", type=Path)
        subparser.add_argument("--sessions", default="dev")
        subparser.add_argument("--model", default="sonnet")
        subparser.add_argument("--condition", default="trace")
        subparser.add_argument("--a", default="trace")
        subparser.add_argument("--b", default="both")
        subparser.add_argument("--no-ambiguous", action="store_true", help="score without the ambiguous relabel")
    arguments = argument_parser.parse_args()
    sessions = sessions_for(arguments.sessions)
    ambiguous = not arguments.no_ambiguous
    options = dict(model=arguments.model, ambiguous=ambiguous)

    if arguments.command == "ablation":
        variants = sorted(path.name for path in arguments.directory.iterdir() if path.is_dir())
        runs_of = {variant: sorted((arguments.directory / variant).glob("r*")) for variant in variants}
        for metric in ("solved", "r_at_10", "p_at_5", "instance_f1"):
            baseline = per_session_mean(runs_of["none"], sessions, metric, condition=arguments.condition, **options)
            print(f"\n{metric} (mean of {len(runs_of['none'])} runs per session; delta vs none, paired bootstrap 95% CI)")
            for variant in variants:
                values = per_session_mean(runs_of[variant], sessions, metric, condition=arguments.condition, **options)
                shared = sorted(set(values) & set(baseline))
                mean = sum(values[session] for session in shared) / len(shared)
                differences = [values[session] - baseline[session] for session in shared]
                low, high = paired_bootstrap(differences)
                print(f"  {variant:22s} {mean:.2f}  delta {sum(differences) / len(differences):+.2f} [{low:+.2f}, {high:+.2f}]"
                      f"  n={len(shared)} sessions x {len(runs_of[variant])} runs")

    elif arguments.command == "taxonomy":
        runs = sorted(arguments.directory.glob("r*")) or [arguments.directory]
        counts, session_count = Counter(), 0
        for run_directory in runs:
            for session in sessions:
                path = run_directory / "predictions" / f"{arguments.model}__{arguments.condition}" / f"{session}.json"
                if path.exists():
                    session_count += 1
                    counts.update(failure_codes(json.loads(path.read_text()), ground_truth(session, ambiguous)))
        print(f"{arguments.condition}: {session_count} session-runs")
        for code, count in counts.most_common():
            print(f"  {code:18s} {count:3d}  ({count / session_count:.2f} per session-run)")

    elif arguments.command == "audit":
        kappas, workflow_agreement, ambiguous_votes = [], [], []
        for audit_path in sorted(arguments.directory.glob("*.json")):
            audit = json.loads(audit_path.read_text())
            result = audit_agreement(audit, ground_truth(audit["session"]))
            kappas.append(result["kappa"])
            workflow_agreement += [workflow["agrees"] for workflow in result["workflows"]]
            ambiguous_votes += [item["auditor_says_repeated"] for item in result["ambiguous"]]
            print(f"{audit['session']}: kappa {result['kappa']:.2f} (ambiguous as workflow: "
                  f"{result['kappa_ambiguous_as_workflow']:.2f})")
            for workflow in result["workflows"]:
                print(f"   GT workflow {'agree   ' if workflow['agrees'] else 'DISAGREE'} iou {workflow['iou']:.2f}  {workflow['name']}")
            for item in result["ambiguous"]:
                print(f"   ambiguous {item['workflow']:10s} auditor repeated={item['auditor_says_repeated']} "
                      f"iou {item['iou']:.2f} ({item['auditor_group']})")
            for name in result["auditor_extra"]:
                print(f"   auditor-only repeated workflow: {name}")
        print(f"\nmean kappa {sum(kappas) / len(kappas):.2f} over {len(kappas)} sessions; GT workflows confirmed "
              f"{sum(workflow_agreement)}/{len(workflow_agreement)}; ambiguous groups called repeated "
              f"{sum(ambiguous_votes)}/{len(ambiguous_votes)} (Rule B keeps the label if >= half)")

    else:
        runs = sorted(arguments.directory.glob("r*"))
        only_a = only_b = both_solved = neither = 0
        per_session = {}
        for run_directory in runs:
            scores_a = run_scores(run_directory, sessions, arguments.a, **options)
            scores_b = run_scores(run_directory, sessions, arguments.b, **options)
            for session in set(scores_a) & set(scores_b):
                solved_a, solved_b = scores_a[session]["solved"], scores_b[session]["solved"]
                only_a += solved_a and not solved_b
                only_b += solved_b and not solved_a
                both_solved += solved_a and solved_b
                neither += not solved_a and not solved_b
                per_session.setdefault(session, []).append(solved_a - solved_b)
        differences = [sum(values) / len(values) for values in per_session.values()]
        low, high = paired_bootstrap(differences)
        print(f"{arguments.a} vs {arguments.b} over {only_a + only_b + both_solved + neither} (session, run) pairs:")
        print(f"  both solved {both_solved}, only {arguments.a} {only_a}, only {arguments.b} {only_b}, neither {neither}")
        print(f"  exact McNemar p = {mcnemar_exact(only_a, only_b):.4f}")
        print(f"  per-session solved difference ({arguments.a} - {arguments.b}) {sum(differences) / len(differences):+.2f}"
              f" [{low:+.2f}, {high:+.2f}] (paired bootstrap, {len(differences)} sessions)")


if __name__ == "__main__":
    main()
