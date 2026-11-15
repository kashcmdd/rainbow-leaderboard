"""Glicko-2 rating updates.

Reference: Mark E. Glickman, "Example of the Glicko-2 system".
Ratings use the same display scale as before (0–3000-ish); internally they are
converted to the Glicko-2 scale around 1500.

Only win/loss outcomes are used. Inactivity is modelled by increasing the
rating deviation, not by decaying the rating.
"""

import math

SCALE = 173.7178
DEFAULT_RD = 350.0
MIN_RD = 30.0
MAX_RD = 350.0
DEFAULT_VOLATILITY = 0.06
TAU = 0.5


def _g(phi: float) -> float:
    return 1.0 / math.sqrt(1.0 + 3.0 * phi * phi / (math.pi * math.pi))


def _expected(mu: float, mu_j: float, phi_j: float) -> float:
    return 1.0 / (1.0 + math.exp(-_g(phi_j) * (mu - mu_j)))


def _new_volatility(phi: float, sigma: float, v: float, delta: float,
                    tau: float = TAU, epsilon: float = 1e-6) -> float:
    a = math.log(sigma * sigma)

    def f(x: float) -> float:
        ex = math.exp(x)
        denom = phi * phi + v + ex
        return (ex * (delta * delta - phi * phi - v - ex)) / (2 * denom * denom) - (x - a) / (tau * tau)

    A = a
    if delta * delta > phi * phi + v:
        B = math.log(delta * delta - phi * phi - v)
    else:
        k = 1
        while k <= 100 and f(a - k * tau) < 0:
            k += 1
        B = a - k * tau

    fa, fb = f(A), f(B)
    while abs(B - A) > epsilon:
        C = A + (A - B) * fa / (fb - fa)
        fc = f(C)
        if fc * fb <= 0:
            A, fa = B, fb
        else:
            fa /= 2.0
        B, fb = C, fc

    return math.exp(A / 2.0)


def update_rating(
    rating: float,
    rd: float,
    volatility: float,
    opponent_rating: float,
    opponent_rd: float,
    score: float,
    tau: float = TAU,
) -> tuple[float, float, float]:
    """Return (rating, rd, volatility) after one result.

    ``score`` is 1.0 for a win and 0.0 for a loss. Ratings are in the display
    scale (e.g. 500, 2000), not the internal 1500-centred scale.
    """
    mu = (rating - 1500.0) / SCALE
    phi = max(rd, MIN_RD) / SCALE
    mu_j = (opponent_rating - 1500.0) / SCALE
    phi_j = max(opponent_rd, MIN_RD) / SCALE

    g = _g(phi_j)
    expected = _expected(mu, mu_j, phi_j)
    expected = min(max(expected, 1e-9), 1.0 - 1e-9)
    v = 1.0 / max(g * g * expected * (1.0 - expected), 1e-12)
    delta = v * g * (score - expected)

    sigma = _new_volatility(phi, max(volatility, 1e-6), v, delta, tau)
    phi_star = math.sqrt(phi * phi + sigma * sigma)
    phi_new = 1.0 / math.sqrt(1.0 / (phi_star * phi_star) + 1.0 / v)
    mu_new = mu + phi_new * phi_new * g * (score - expected)

    new_rating = 1500.0 + SCALE * mu_new
    new_rd = min(MAX_RD, max(MIN_RD, SCALE * phi_new))
    return new_rating, new_rd, sigma


def inflate_rd(rd: float, days: int) -> float:
    """Grow RD while a player is inactive; the rating itself stays put."""
    if days <= 0:
        return min(MAX_RD, max(MIN_RD, rd))
    c = math.sqrt((MAX_RD * MAX_RD - MIN_RD * MIN_RD) / 365.0)
    phi = max(rd, MIN_RD) / SCALE
    phi_new = math.sqrt(phi * phi + (c / SCALE) ** 2 * days)
    return min(MAX_RD, max(MIN_RD, SCALE * phi_new))
