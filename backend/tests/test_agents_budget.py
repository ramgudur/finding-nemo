from app.agents import budget


def test_scaled_interval_stays_at_base_with_one_app():
    assert budget.scaled_interval(60.0, 1, "critic", 40.0) == 60.0


def test_scaled_interval_stretches_with_more_apps():
    one = budget.scaled_interval(60.0, 1, "critic", 40.0)
    five = budget.scaled_interval(60.0, 5, "critic", 40.0)
    assert five > one


def test_scaled_interval_never_shrinks_below_base():
    # a huge rpm means the share easily covers many apps at the base interval
    assert budget.scaled_interval(60.0, 5, "critic", 1_000_000.0) == 60.0


def test_scaled_interval_matches_the_share_formula():
    # 5 apps * 4 calls/episode, critic's share is 30% of a 40 rpm bucket
    n_apps, rpm, share, est_calls = 5, 40.0, budget.SCHEDULED_SHARE["critic"], budget.EST_CALLS["critic"]
    expected = 60.0 * n_apps * est_calls / (share * rpm)
    assert budget.scaled_interval(10.0, n_apps, "critic", rpm) == expected


def test_scaled_interval_degenerate_inputs_fall_back_to_base():
    assert budget.scaled_interval(60.0, 0, "critic", 40.0) == 60.0
    assert budget.scaled_interval(60.0, 5, "critic", 0.0) == 60.0
    assert budget.scaled_interval(60.0, -1, "critic", 40.0) == 60.0


def test_scaled_interval_unknown_agent_uses_the_default_share():
    # falls back to share=0.2 (the function's documented default) rather than raising
    n_apps, rpm, est_calls = 3, 40.0, budget.EST_CALLS.get("unknown-agent", 4)
    expected = 60.0 * n_apps * est_calls / (0.2 * rpm)
    assert budget.scaled_interval(10.0, n_apps, "unknown-agent", rpm) == expected
