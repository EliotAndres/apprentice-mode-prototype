from replicate.analyze import failure_codes, mcnemar_exact
from test_score import GROUND_TRUTH, predicted_opportunity


def test_mcnemar_exact():
    assert mcnemar_exact(0, 6) == 2 / 64
    assert mcnemar_exact(3, 3) == 1.0 and mcnemar_exact(0, 0) == 1.0


def test_failure_codes():
    perfect = [predicted_opportunity((0, 10), (40, 50)), predicted_opportunity((20, 30), (60, 90))]
    assert failure_codes({"opportunities": perfect}, GROUND_TRUTH) == []
    assert failure_codes({"opportunities": perfect[:1]}, GROUND_TRUTH) == ["missed_short"]
    assert failure_codes({"opportunities": [*perfect, predicted_opportunity((100, 100))]}, GROUND_TRUTH) == ["extra_noise"]


def test_cohen_kappa():
    from replicate.analyze import cohen_kappa
    assert cohen_kappa([True, False, True, False], [True, False, True, False]) == 1.0
    assert cohen_kappa([True, True, False, False], [True, False, True, False]) == 0.0


def test_failure_codes_substep():
    perfect = [predicted_opportunity((0, 10), (40, 50)), predicted_opportunity((20, 30), (60, 90))]
    part_of_a = predicted_opportunity((0, 0), (40, 40))
    assert failure_codes({"opportunities": [*perfect, part_of_a]}, GROUND_TRUTH) == ["extra_substep"]
