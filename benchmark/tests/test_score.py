from replicate.score import aggregate, covered, instance_f1, iou, match, score_session

# Eleven actions, one every 10 s. Workflow A = tasks at seqs {0,1} and {4,5};
# workflow B = {2,3} and an interrupted instance {6, 8,9} with distractor action 7
# in the middle; seq 10 is a one-off.
ACTION_TIMES = [0, 10, 20, 30, 40, 50, 60, 70, 80, 90, 100]


def true_instance(*sequence_numbers, seconds=10):
    return {"task_id": f"t{sequence_numbers[0]}", "seqs": list(sequence_numbers),
            "spans": [[ACTION_TIMES[sequence_numbers[0]], ACTION_TIMES[sequence_numbers[-1]]]], "seconds": seconds}


GROUND_TRUTH = {
    "session_id": "t", "action_t": ACTION_TIMES,
    "opportunities": [
        {"opportunity_id": "A", "instances": [true_instance(0, 1), true_instance(4, 5)]},
        {"opportunity_id": "B", "instances": [true_instance(2, 3), true_instance(6, 8, 9, seconds=30)]},
    ],
    "negatives": [{**true_instance(7), "kind": "distractor"}, {**true_instance(10), "kind": "one_off"}],
}


def predicted_opportunity(*time_spans, name="p"):
    return {"name": name, "instances": [{"start_t": span_start, "end_t": span_end, "values": ""}
                                        for span_start, span_end in time_spans]}


def test_covered_and_iou():
    assert covered([{"start_t": 9, "end_t": 21}], ACTION_TIMES, padding_seconds=0) == {1, 2}
    assert covered([{"start_t": 12, "end_t": 12}], ACTION_TIMES, padding_seconds=3) == {1}
    assert iou({1, 2}, {1, 2}) == 1
    assert iou({1, 2}, {3}) == 0
    assert iou({6, 7, 8}, {6, 8}, ignored_actions={7}) == 1  # covering the interruption is free


def test_perfect_prediction_whole_or_split_interruption():
    for workflow_b_prediction in (predicted_opportunity((20, 30), (60, 90)),
                                  predicted_opportunity((20, 30), (60, 60), (80, 90))):
        session_score = score_session(
            {"opportunities": [predicted_opportunity((0, 10), (40, 50)), workflow_b_prediction]}, GROUND_TRUTH)
        assert session_score["p_at_5"] == 1 and session_score["r_at_10"] == 1
        assert session_score["grounding_iou"] == 1
        assert session_score["fp_negative"] == 0 and session_score["time_recall"] == 1
    whole_span_score = score_session({"opportunities": [predicted_opportunity((20, 30), (60, 90))]}, GROUND_TRUTH)
    assert whole_span_score["instance_f1"] == 1


def test_missing_prefix_of_interrupted_instance_still_matches():
    session_score = score_session({"opportunities": [predicted_opportunity((20, 30), (80, 90))]},
                                  GROUND_TRUTH)  # misses seq 6
    assert session_score["n_matched"] == 1 and abs(session_score["grounding_iou"] - 4 / 5) < 1e-9


def test_false_positive_on_negative_and_rank_order():
    prediction = {"opportunities": [predicted_opportunity((70, 70), (100, 100), name="neg"),
                                    predicted_opportunity((0, 10), (40, 50))]}
    session_score = score_session(prediction, GROUND_TRUTH)
    assert session_score["n_matched"] == 1 and session_score["matches"][0]["rank"] == 1
    assert session_score["p_at_5"] == 0.5 and session_score["r_at_10"] == 0.5
    assert session_score["fp_negative"] == 0.5
    assert session_score["time_recall"] == 20 / 60


def test_each_ground_truth_opportunity_matched_once():
    duplicate_predictions = [predicted_opportunity((0, 10), (40, 50)), predicted_opportunity((0, 10), (40, 50))]
    assert len(match(duplicate_predictions, GROUND_TRUTH)) == 1


def test_low_overlap_is_not_a_match():
    one_action_prediction = {"opportunities": [predicted_opportunity((0, 0))]}  # 1 of 4 actions
    assert score_session(one_action_prediction, GROUND_TRUTH)["n_matched"] == 0


def test_merged_workflows_match_only_one():
    session_score = score_session({"opportunities": [predicted_opportunity((0, 50))]}, GROUND_TRUTH)  # A + B lumped
    assert session_score["n_matched"] == 1 and session_score["grounding_iou"] < 1


def test_instance_f1_partial():
    f1_score = instance_f1(predicted_opportunity((0, 10)), GROUND_TRUTH["opportunities"][0],
                           ACTION_TIMES)  # 1 of 2 found, precision 1
    assert abs(f1_score - 2 / 3) < 1e-9


def test_empty_prediction_and_aggregate():
    empty_score = score_session({"opportunities": []}, GROUND_TRUTH)
    assert empty_score["p_at_5"] == 0 and empty_score["r_at_10"] == 0
    half_score = score_session({"opportunities": [predicted_opportunity((0, 10), (40, 50))]}, GROUND_TRUTH)
    averages = aggregate([empty_score, half_score])
    assert averages["r_at_10"] == 0.25
    assert averages["grounding_iou"] == 1.0  # grounding only over sessions with a match


def test_hard_tier_counts_interleaved_actions_and_accepts_segments():
    hard_ground_truth = {**GROUND_TRUTH, "dontcare_inside_instances": False}
    lumped_prediction = predicted_opportunity((20, 30), (60, 90))  # covers distractor action 7 inside B
    segmented_prediction = {"name": "p", "instances": [
        {"start_t": 20, "end_t": 30, "values": ""},
        {"segments": [{"start_t": 60, "end_t": 60}, {"start_t": 80, "end_t": 90}], "values": ""}]}
    # Easy tier: action 7 is don't-care. Hard tier: it counts against the lumped span.
    assert score_session({"opportunities": [lumped_prediction]}, GROUND_TRUTH)["grounding_iou"] == 1
    hard_lumped_score = score_session({"opportunities": [lumped_prediction]}, hard_ground_truth)
    assert abs(hard_lumped_score["grounding_iou"] - 5 / 6) < 1e-9
    hard_segmented_score = score_session({"opportunities": [segmented_prediction]}, hard_ground_truth)
    assert hard_segmented_score["grounding_iou"] == 1 and hard_segmented_score["instance_f1"] == 1


def test_parameter_recall_counts_only_varying_values():
    from replicate.score import parameter_recall
    true_opportunity = {"instances": [{"values": {"city": "Lisbon", "guests": "2"}},
                                      {"values": {"city": "Porto", "guests": "2"}}]}
    prediction = {"instances": [{"values": "city=lisbon"}, {"values": "Faro, 2 guests"}]}
    assert parameter_recall(prediction, true_opportunity) == 0.5  # guests never varies, so it isn't counted
    assert parameter_recall(prediction, {"instances": [{"values": {}}, {"values": {}}]}) is None


def test_solved_needs_every_workflow_tight_and_nothing_extra():
    workflow_a, workflow_b = predicted_opportunity((0, 10), (40, 50)), predicted_opportunity((20, 30), (60, 90))
    assert score_session({"opportunities": [workflow_a, workflow_b]}, GROUND_TRUTH)["solved"] == 1
    assert score_session({"opportunities": [workflow_a]}, GROUND_TRUTH)["solved"] == 0  # one missed
    extra = predicted_opportunity((100, 100), name="one-off")
    assert score_session({"opportunities": [workflow_a, workflow_b, extra]}, GROUND_TRUTH)["solved"] == 0
    # Spans that only reach the actions through the 3 s padding count for R@10, not for solved.
    loose_a = predicted_opportunity((2, 8), (42, 48))
    loose_score = score_session({"opportunities": [loose_a, workflow_b]}, GROUND_TRUTH)
    assert loose_score["r_at_10"] == 1 and loose_score["solved"] == 0


def test_proposal_on_ambiguous_attempts_is_ignored():
    ground_truth = {**GROUND_TRUTH, "negatives": [{**true_instance(7), "kind": "ambiguous"},
                                                   {**true_instance(10), "kind": "one_off"}]}
    perfect = [predicted_opportunity((0, 10), (40, 50)), predicted_opportunity((20, 30), (60, 90))]
    with_ambiguous = score_session({"opportunities": [*perfect, predicted_opportunity((70, 70), name="amb")]},
                                   ground_truth, padding_seconds=0)
    assert with_ambiguous["n_pred"] == 2 and with_ambiguous["solved"] == 1
    with_one_off = score_session({"opportunities": [*perfect, predicted_opportunity((100, 100), name="neg")]},
                                 ground_truth, padding_seconds=0)
    assert with_one_off["n_pred"] == 3 and with_one_off["solved"] == 0 and with_one_off["fp_negative"] > 0


def test_action_numbers_become_segments():
    from replicate.models import segments_of
    times = dict(enumerate(ACTION_TIMES))
    assert segments_of([9, 6, 8, 99], times) == [{"start_t": 60, "end_t": 60}, {"start_t": 80, "end_t": 90}]
