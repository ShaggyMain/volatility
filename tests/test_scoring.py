from scoring import normalize_probabilities, weighted_score


def test_probabilities_sum_to_one():
    values = normalize_probabilities(0.7, 0.1, 0.2)
    assert abs(sum(values) - 1.0) < 1e-9


def test_weighted_score():
    result = weighted_score({"a": 80, "b": 60}, {"a": 0.75, "b": 0.25})
    assert result == 75
