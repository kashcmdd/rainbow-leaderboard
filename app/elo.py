import math
from typing import Optional

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
    expected_win = expected_score(rating_winner, rating_loser)
    expected_lose = 1 - expected_win

    k_winner = k_factor(rating_winner, winner_matches, winner_is_new, format)
    k_loser = k_factor(rating_loser, loser_matches, loser_is_new, format)

    margin = margin_multiplier(score_winner, score_loser)

    prog_win = progressive_multiplier(rating_winner, True)
    prog_lose = progressive_multiplier(rating_loser, False)

    delta_winner = round(k_winner * (1.0 - expected_win) * margin * prog_win)
    delta_loser = round(k_loser * (0.0 - expected_lose) * margin * prog_lose)

    # Floor so loser never drops below rating_floor
    max_loss = rating_loser - settings.rating_floor
    delta_loser = min(delta_loser, max_loss)

    # Ensure at least ±1 so no zero-ELO matches
    delta_winner = max(delta_winner, 1)
    if delta_loser < 0:
        delta_loser = min(delta_loser, -1)
    elif max_loss > 0:
        delta_loser = -1

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
