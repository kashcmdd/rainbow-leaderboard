import pytest

from app import glicko2


class TestUpdateRating:
    def test_winner_gains_loser_loses(self):
        winner, winner_rd, _ = glicko2.update_rating(1500, 350, 0.06, 1500, 350, 1.0)
        loser, loser_rd, _ = glicko2.update_rating(1500, 350, 0.06, 1500, 350, 0.0)
        assert winner > 1500 > loser
        assert winner_rd < 350 and loser_rd < 350
        assert winner - 1500 == pytest.approx(1500 - loser, abs=1)

    def test_upset_gains_more_than_expected_win(self):
        favourite, _, _ = glicko2.update_rating(2000, 200, 0.06, 1000, 200, 1.0)
        underdog, _, _ = glicko2.update_rating(1000, 200, 0.06, 2000, 200, 1.0)
        assert underdog - 1000 > favourite - 2000

    def test_high_rd_moves_more(self):
        settled, _, _ = glicko2.update_rating(1500, 80, 0.06, 1500, 80, 1.0)
        new, _, _ = glicko2.update_rating(1500, 350, 0.06, 1500, 350, 1.0)
        assert new - 1500 > settled - 1500

    def test_rd_shrinks_with_games(self):
        rating, rd, _ = glicko2.update_rating(1500, 350, 0.06, 1500, 350, 1.0)
        for _ in range(5):
            rating, rd, _ = glicko2.update_rating(rating, rd, 0.06, 1500, 350, 1.0)
        assert rd < 350


class TestInflation:
    def test_no_days_no_change(self):
        assert glicko2.inflate_rd(120, 0) == 120

    def test_rd_grows_with_inactivity(self):
        assert glicko2.inflate_rd(80, 30) > 80

    def test_rd_caps_at_maximum(self):
        assert glicko2.inflate_rd(80, 5000) == glicko2.MAX_RD
