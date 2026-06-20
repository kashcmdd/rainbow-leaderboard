from app.elo import (
    expected_score,
    margin_multiplier,
    k_factor,
    progressive_multiplier,
    calculate_delta,
    calculate_delta_team,
)


class TestExpectedScore:
    def test_equal_ratings(self):
        assert expected_score(1000, 1000) == 0.5

    def test_higher_rated_favored(self):
        assert expected_score(1500, 1000) > 0.5
        assert expected_score(1000, 1500) < 0.5

    def test_big_gap(self):
        assert round(expected_score(2000, 1000), 4) == 0.9968


class TestMarginMultiplier:
    def test_tie(self):
        assert margin_multiplier(10, 10) == 1.0

    def test_blowout(self):
        assert margin_multiplier(10, 0) > 1.0

    def test_zero_total(self):
        assert margin_multiplier(0, 0) == 1.0


class TestKFactor:
    def test_new_player(self):
        assert k_factor(1000, 0, is_new=True) == 64.0

    def test_provisional(self):
        assert k_factor(1000, 5) == 64.0

    def test_established(self):
        assert k_factor(1000, 20) == 32.0

    def test_team_format_splits_k(self):
        k = k_factor(1000, 20, format="5v5")
        assert k == 32.0 * 0.5 * 5
        assert k == 80.0


class TestProgressiveMultiplier:
    def test_no_shift_low_rating(self):
        assert progressive_multiplier(1000, True) == 1.0

    def test_winner_penalty_high_rating(self):
        assert progressive_multiplier(3000, True) == 0.85

    def test_loser_bonus_high_rating(self):
        assert progressive_multiplier(3000, False) == 1.15


class TestCalculateDelta:
    def test_winner_gains_loser_loses(self):
        dw, dl = calculate_delta(1200, 1000, 10, 5, 20, 20, format="1v1")
        assert dw > 0
        assert dl < 0

    def test_underdog_win_bonus(self):
        favorite_w, favorite_l = calculate_delta(2000, 1000, 10, 5, 20, 20, format="1v1")
        underdog_w, underdog_l = calculate_delta(1000, 2000, 10, 5, 20, 20, format="1v1")
        assert underdog_w > favorite_w
        assert underdog_l < favorite_l

    def test_floor_prevents_below_zero(self):
        dw, dl = calculate_delta(1000, 0, 10, 5, 20, 20, format="1v1")
        assert dl >= -0

    def test_min_one_delta(self):
        dw, dl = calculate_delta(1000, 1000, 1, 0, 20, 20, format="1v1")
        assert dw >= 1
        assert dl <= -1

    def test_provisional_earns_more(self):
        new_w, new_l = calculate_delta(1200, 1000, 10, 5, 2, 2, format="1v1")
        old_w, old_l = calculate_delta(1200, 1000, 10, 5, 20, 20, format="1v1")
        assert abs(new_w) > abs(old_w)


class TestCalculateDeltaTeam:
    def test_team_winner_loser(self):
        da, db = calculate_delta_team(1200, 1000, 10, 5, 20, 20, format="5v5")
        assert da > 0
        assert db < 0
