"""Score ranked opportunity predictions against chained ground truth.

Evidence is scored at the ACTION level. A predicted instance, either one
[start_t, end_t] span or a list of `segments` spans, covers every action whose
timestamp falls inside it, widened by PAD seconds on each side. A ground-truth
instance is its set of actions.

Easy tier (gt["dontcare_inside_instances"] true): actions of other tasks that fall
inside an interrupted instance (between its first and last action) are
don't-care, so a model may report it as one span or as its pieces. Hard tier
(false): tasks are interleaved action by action and models report segments, so
every covered action counts and lumping a region together is penalised.

    evidence IoU(P, G) = |cov(P) & G| / |(cov(P) | G) - dontcare(G)|

Predictions are matched to ground-truth opportunities greedily in rank order,
each to the unmatched opportunity with the highest evidence IoU, if >= MATCH_IOU.

Per session:
  p_at_5        matched predictions in the top 5 / min(5, #predictions)
  r_at_10       ground-truth opportunities matched by the top 10 / #ground truth
  grounding_iou mean evidence IoU over matched pairs
  instance_f1   per matched pair, F1 of predicted vs true instances (IoU >= 0.5), averaged
  fp_negative   share of top-10 predictions that match nothing and whose covered
                actions are mostly (>50%) one-off or distractor actions
  time_recall   share of ground-truth workflow time held by matched opportunities
                (indicative only: timing is synthetic)
  param_recall  per matched pair, share of the true values that vary between runs
                (city, dates, names) found in the predicted instances' values text, averaged
Negatives of kind "ambiguous" (a workflow attempted twice or more but completed fewer
than twice, e.g. stopped by a bot wall) are neither workflows nor noise: an unmatched
proposal whose actions are mostly ambiguous is dropped before scoring.

  solved        1 if the top n predictions (n = #ground truth) match every ground-truth
                opportunity at evidence IoU >= SOLVED_IOU with no padding, and the model
                proposed no more than n opportunities; else 0. All or nothing per session.
"""
from __future__ import annotations

MATCH_IOU = 0.3
INSTANCE_IOU = 0.5
PADDING_SECONDS = 3.0
SOLVED_IOU = 0.5


def covered(predicted_instances: list[dict], action_times: list[float],
            padding_seconds: float = PADDING_SECONDS) -> set[int]:
    """Actions inside any predicted [start_t - padding, end_t + padding] span or segment."""
    covered_actions: set[int] = set()
    for predicted_instance in predicted_instances:
        for time_span in predicted_instance.get("segments") or [predicted_instance]:
            span_start, span_end = sorted((float(time_span["start_t"]), float(time_span["end_t"])))
            covered_actions.update(sequence_number for sequence_number, action_time in enumerate(action_times)
                                   if span_start - padding_seconds <= action_time <= span_end + padding_seconds)
    return covered_actions


def dontcare(true_instance: dict, enabled: bool = True) -> set[int]:
    """Other tasks' actions between an instance's first and last action."""
    if not enabled:
        return set()
    instance_actions = true_instance["seqs"]
    return set(range(min(instance_actions), max(instance_actions) + 1)) - set(instance_actions)


def iou(covered_actions: set[int], true_actions: set[int], ignored_actions: set[int] = frozenset()) -> float:
    union = (covered_actions | true_actions) - (ignored_actions - true_actions)
    return len(covered_actions & true_actions) / len(union) if union else 0.0


def opportunity_action_sets(opportunity: dict, dontcare_enabled: bool = True) -> tuple[set[int], set[int]]:
    """(true actions, ignored actions) for a ground-truth opportunity."""
    true_actions = {sequence_number for true_instance in opportunity["instances"]
                    for sequence_number in true_instance["seqs"]}
    ignored_actions = set().union(*(dontcare(true_instance, dontcare_enabled)
                                    for true_instance in opportunity["instances"])) - true_actions
    return true_actions, ignored_actions


def match(predicted_opportunities: list[dict], ground_truth: dict, threshold: float = MATCH_IOU,
          padding_seconds: float = PADDING_SECONDS) -> list[tuple[int, int, float]]:
    """Greedy rank-order matching -> [(prediction_rank, ground_truth_index, iou)]."""
    dontcare_enabled = ground_truth.get("dontcare_inside_instances", True)
    action_sets = [opportunity_action_sets(opportunity, dontcare_enabled)
                   for opportunity in ground_truth["opportunities"]]
    matched_ground_truth: set[int] = set()
    matched_pairs = []
    for prediction_rank, predicted_opportunity in enumerate(predicted_opportunities):
        covered_actions = covered(predicted_opportunity.get("instances", []), ground_truth["action_t"],
                                  padding_seconds)
        best_iou, best_index = max(((iou(covered_actions, *action_sets[index]), index)
                                    for index in range(len(action_sets)) if index not in matched_ground_truth),
                                   default=(0.0, -1))
        if best_index >= 0 and best_iou >= threshold:
            matched_ground_truth.add(best_index)
            matched_pairs.append((prediction_rank, best_index, best_iou))
    return matched_pairs


def instance_f1(predicted_opportunity: dict, true_opportunity: dict, action_times: list[float],
                padding_seconds: float = PADDING_SECONDS, dontcare_enabled: bool = True) -> float:
    predicted_action_sets = [covered([predicted_instance], action_times, padding_seconds)
                             for predicted_instance in predicted_opportunity.get("instances", [])]
    true_action_sets = [(set(true_instance["seqs"]), dontcare(true_instance, dontcare_enabled))
                        for true_instance in true_opportunity["instances"]]
    matched_true_instances: set[int] = set()
    true_positives = 0
    for predicted_actions in predicted_action_sets:
        best_iou, best_index = max(((iou(predicted_actions, *true_action_sets[index]), index)
                                    for index in range(len(true_action_sets)) if index not in matched_true_instances),
                                   default=(0.0, -1))
        if best_index >= 0 and best_iou >= INSTANCE_IOU:
            matched_true_instances.add(best_index)
            true_positives += 1
    if not true_positives:
        return 0.0
    precision = true_positives / len(predicted_action_sets)
    recall = true_positives / len(true_action_sets)
    return 2 * precision * recall / (precision + recall)


def parameter_recall(predicted_opportunity: dict, true_opportunity: dict) -> float | None:
    """Share of varying true values named in the prediction; None when no value varies.
    ponytail: case-insensitive substring over the whole opportunity, not per matched instance."""
    run_values = [true_instance.get("values") or {} for true_instance in true_opportunity["instances"]]
    varying_keys = {key for values in run_values for key in values
                    if len({str(other.get(key, "")).lower() for other in run_values}) > 1}
    expected_values = [str(values[key]).lower() for values in run_values for key in varying_keys
                       if str(values.get(key, "")).strip()]
    if not expected_values:
        return None
    predicted_text = " ".join(str(predicted_instance.get("values", ""))
                              for predicted_instance in predicted_opportunity.get("instances", [])).lower()
    return sum(value in predicted_text for value in expected_values) / len(expected_values)


def drop_ambiguous(predicted_opportunities: list[dict], ground_truth: dict, padding_seconds: float) -> list[dict]:
    """Remove unmatched proposals whose covered actions are mostly ambiguous."""
    ambiguous_actions = {sequence_number for negative in ground_truth["negatives"]
                         if negative.get("kind") == "ambiguous" for sequence_number in negative["seqs"]}
    if not ambiguous_actions:
        return predicted_opportunities
    matched_ranks = {prediction_rank for prediction_rank, _, _ in
                     match(predicted_opportunities, ground_truth, padding_seconds=padding_seconds)}
    kept = []
    for prediction_rank, predicted_opportunity in enumerate(predicted_opportunities):
        covered_actions = covered(predicted_opportunity.get("instances", []), ground_truth["action_t"],
                                  padding_seconds)
        if (prediction_rank in matched_ranks or not covered_actions
                or len(covered_actions & ambiguous_actions) / len(covered_actions) <= 0.5):
            kept.append(predicted_opportunity)
    return kept


def score_session(prediction: dict, ground_truth: dict, padding_seconds: float = PADDING_SECONDS) -> dict:
    predicted_opportunities = drop_ambiguous(prediction.get("opportunities", [])[:10], ground_truth, padding_seconds)
    true_opportunities = ground_truth["opportunities"]
    dontcare_enabled = ground_truth.get("dontcare_inside_instances", True)
    matched_pairs = match(predicted_opportunities, ground_truth, padding_seconds=padding_seconds)
    matched_ranks = {prediction_rank for prediction_rank, _, _ in matched_pairs}
    negative_actions = {sequence_number for negative in ground_truth["negatives"]
                        if negative.get("kind") != "ambiguous" for sequence_number in negative["seqs"]}
    false_positives_on_negatives = 0
    for prediction_rank, predicted_opportunity in enumerate(predicted_opportunities):
        covered_actions = covered(predicted_opportunity.get("instances", []), ground_truth["action_t"],
                                  padding_seconds)
        if (prediction_rank not in matched_ranks and covered_actions
                and len(covered_actions & negative_actions) / len(covered_actions) > 0.5):
            false_positives_on_negatives += 1
    seconds_per_opportunity = [sum(true_instance.get("seconds", 0) for true_instance in opportunity["instances"])
                               for opportunity in true_opportunities]
    instance_f1_scores = [instance_f1(predicted_opportunities[prediction_rank], true_opportunities[true_index],
                                      ground_truth["action_t"], padding_seconds, dontcare_enabled)
                          for prediction_rank, true_index, _ in matched_pairs]
    parameter_recalls = [recall for prediction_rank, true_index, _ in matched_pairs
                         if (recall := parameter_recall(predicted_opportunities[prediction_rank],
                                                        true_opportunities[true_index])) is not None]
    return {
        "n_pred": len(predicted_opportunities), "n_gt": len(true_opportunities), "n_matched": len(matched_pairs),
        "p_at_5": (sum(prediction_rank < 5 for prediction_rank in matched_ranks)
                   / min(5, len(predicted_opportunities))) if predicted_opportunities else 0.0,
        "r_at_10": len(matched_pairs) / len(true_opportunities) if true_opportunities else 0.0,
        "grounding_iou": (sum(pair_iou for _, _, pair_iou in matched_pairs) / len(matched_pairs)
                          if matched_pairs else 0.0),
        "instance_f1": sum(instance_f1_scores) / len(instance_f1_scores) if instance_f1_scores else 0.0,
        "fp_negative": (false_positives_on_negatives / len(predicted_opportunities)
                        if predicted_opportunities else 0.0),
        "time_recall": (sum(seconds_per_opportunity[true_index] for _, true_index, _ in matched_pairs)
                        / sum(seconds_per_opportunity) if sum(seconds_per_opportunity) else 0.0),
        "param_recall": (sum(parameter_recalls) / len(parameter_recalls) if parameter_recalls else 0.0),
        "solved": float(len(predicted_opportunities) <= len(true_opportunities) and len(
            match(predicted_opportunities, ground_truth, threshold=SOLVED_IOU, padding_seconds=0)) == len(true_opportunities)),
        "matches": [{"rank": prediction_rank, "opportunity_id": true_opportunities[true_index]["opportunity_id"],
                     "iou": round(pair_iou, 3)} for prediction_rank, true_index, pair_iou in matched_pairs],
    }


METRICS = ("p_at_5", "r_at_10", "grounding_iou", "instance_f1", "fp_negative", "time_recall", "param_recall", "solved")


def aggregate(session_scores: list[dict]) -> dict:
    """Macro-average over sessions; grounding/instance F1 averaged over sessions with a match."""
    averages = {}
    for metric in METRICS:
        values = [session_score[metric] for session_score in session_scores
                  if metric not in ("grounding_iou", "instance_f1", "param_recall") or session_score["n_matched"]]
        averages[metric] = sum(values) / len(values) if values else 0.0
    averages["sessions"] = len(session_scores)
    return averages
