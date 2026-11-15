import math

from app.config import settings


def expected_score(rating_a: int, rating_b: int) -> float:
    return 1.0 / (1.0 + math.pow(10, (rating_b - rating_a) / 400.0))


def margin_multiplier(score_a: int, score_b: int, weight: float = settings.margin_weight) -> float:
    total = score_a + score_b
    if total == 0:
        return 1.0
    margin = abs(score_a - score_b) / total
    return 1.0 + (margin * weight)


def k_factor(rating: int, matches_played: int, is_new: bool = False, format: str = "1v1") -> float:
    if is_new or matches_played < settings.provisional_matches:
        base = float(settings.new_player_k)
    else:
        base = float(settings.base_k)

    fmt_mult = settings.format_k_multipliers.get(format, 1.0)
    players_per_team = int(format.split("v")[0]) if "v" in format else 1
    return base * fmt_mult * players_per_team


def progressive_multiplier(rating: int, is_winner: bool) -> float:
    # Gentle rank-based adjustment — caps at ±15% at Radiant
    shift = max(0, (rating - 1500) / 1500)
    if is_winner:
        return 1.0 - 0.15 * shift   # at 3000: 0.85
    else:
        return 1.0 + 0.15 * shift   # at 3000: 1.15


def calculate_delta(
    rating_winner: int,
    rating_loser: int,
    score_winner: int,
    score_loser: int,
    winner_matches: int,
    loser_matches: int,
    winner_is_new: bool = False,
    loser_is_new: bool = False,
    format: str = "1v1",
) -> tuple[int, int]:
    """Winner gain and loser loss for one match.

    Ratings are conserved: the loser drops by exactly the winner's gain, except
    when the floor clips the loss (a player at 0 cannot go negative, so that
    match creates a little rating at the bottom of the ladder).
    """
    expected_win = expected_score(rating_winner, rating_loser)
    k_winner = k_factor(rating_winner, winner_matches, winner_is_new, format)
    margin = margin_multiplier(score_winner, score_loser)
    prog_win = progressive_multiplier(rating_winner, True)

    delta_winner = max(round(k_winner * (1.0 - expected_win) * margin * prog_win), 1)
    delta_loser = -delta_winner

    # Floor so loser never drops below rating_floor
    max_loss = rating_loser - settings.rating_floor
    if delta_loser < -max_loss:
        delta_loser = -max_loss

    return delta_winner, delta_loser


def calculate_delta_team(
    team_a_avg: int,
    team_b_avg: int,
    score_a: int,
    score_b: int,
    team_a_matches: int,
    team_b_matches: int,
    team_a_new: bool = False,
    team_b_new: bool = False,
    format: str = "5v5",
) -> tuple[int, int]:
    if score_a == score_b:
        return 0, 0

    if score_a > score_b:
        return calculate_delta(team_a_avg, team_b_avg, score_a, score_b,
                               team_a_matches, team_b_matches, team_a_new, team_b_new, format)
    else:
        delta_b, delta_a = calculate_delta(team_b_avg, team_a_avg, score_b, score_a,
                                           team_b_matches, team_a_matches, team_b_new, team_a_new, format)
        return delta_a, delta_b
