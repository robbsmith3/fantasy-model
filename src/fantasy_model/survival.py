"""Survival: Monte Carlo of the remaining chops. Each week every alive team scores
N(projected lineup, sigma) and the lowest is eliminated (verified on weeks 1-7 of our Sleeper
league). A pickup's value = how much it raises my survival / win probability."""

from __future__ import annotations

import numpy as np

DEFAULT_SIGMA = 22.7  # pooled within-team weekly SD, Sleeper league weeks 1-7 of 2026
# Team strength drifts as starters get hurt, lose roles or break out, so a projection k weeks
# out is less certain than next week's. Starter (mu >= 9) projection change, 2019-25, byes
# excluded: var ~= 7.3 + 3.0k per player. x9 starters x0.7 (bench absorbs part of each loss):
# team drift variance ~65 one week ahead, +19 per extra week (sd 8 -> 13 by week 6).
DRIFT_V1, DRIFT_STEP = 65.0, 19.0


def score_sigma(con, platform: str, league_id: str, max_week: int) -> float:
    """Pooled within-team SD of weekly scores (weeks with points > 0, up to max_week)."""
    rows = con.execute(
        """SELECT team_id, points FROM team_week
           WHERE platform = ? AND league_id = ? AND points > 0 AND week <= ?""",
        [platform, league_id, max_week],
    ).fetchall()
    by_team: dict[str, list[float]] = {}
    for t, p in rows:
        by_team.setdefault(t, []).append(p)
    groups = [np.asarray(v) for v in by_team.values() if len(v) >= 3]
    dof = sum(len(g) - 1 for g in groups)
    if dof < 10:
        return DEFAULT_SIGMA
    return float(np.sqrt(sum(((g - g.mean()) ** 2).sum() for g in groups) / dof))


def simulate(
    mu: np.ndarray,
    sigma: float,
    n_sims: int = 20000,
    seed: int = 0,
    noise: np.ndarray | None = None,
    drift: bool = True,
) -> dict[str, np.ndarray]:
    """Play out the chops. mu: (teams, weeks) projected scores, one column per remaining chop.
    Column 0 is the nearest week; with `drift`, later columns get a team-strength random walk.

    Returns per-team arrays: p_chop (teams, weeks) = P(eliminated that week), p_win, exp_weeks
    (expected chops survived). Pass the same `noise` (n_sims, teams, weeks) to compare scenarios
    with common random numbers, so small mu changes give low-variance differences.
    """
    n_teams, n_weeks = mu.shape
    if noise is None:
        noise = np.random.default_rng(seed).standard_normal((n_sims, n_teams, n_weeks))
    scores = mu[None, :, :] + sigma * noise
    if drift and n_weeks > 1:
        # Fixed seed (not `seed`) so two calls with the same `noise` share drift paths too.
        steps = np.random.default_rng(7919).standard_normal(noise.shape)
        var = np.array([0.0, DRIFT_V1] + [DRIFT_STEP] * (n_weeks - 2))[:n_weeks]
        scores = scores + np.cumsum(steps * np.sqrt(var)[None, None, :], axis=2)
    alive = np.ones((noise.shape[0], n_teams), dtype=bool)
    chop_week = np.full((noise.shape[0], n_teams), n_weeks)  # n_weeks = never chopped
    rows = np.arange(noise.shape[0])
    for w in range(n_weeks):
        still = alive.sum(axis=1) > 1
        loser = np.where(alive, scores[:, :, w], np.inf).argmin(axis=1)
        alive[rows[still], loser[still]] = False
        chop_week[rows[still], loser[still]] = w
    p_chop = np.stack([(chop_week == w).mean(axis=0) for w in range(n_weeks)], axis=1)
    return {
        "p_chop": p_chop,
        "p_win": (alive & (alive.sum(axis=1, keepdims=True) == 1)).mean(axis=0),
        "exp_weeks": chop_week.mean(axis=0),
    }


def common_noise(n_teams: int, n_weeks: int, n_sims: int = 20000, seed: int = 0) -> np.ndarray:
    return np.random.default_rng(seed).standard_normal((n_sims, n_teams, n_weeks))


def extend_horizon(projected: np.ndarray, n_weeks: int) -> np.ndarray:
    """Projections cover a few weeks; beyond that, assume each team's average projected week."""
    if projected.shape[1] >= n_weeks:
        return projected[:, :n_weeks]
    tail = np.repeat(projected.mean(axis=1, keepdims=True), n_weeks - projected.shape[1], axis=1)
    return np.concatenate([projected, tail], axis=1)


def pickup_delta(
    mu: np.ndarray,
    team_idx: int,
    gains: np.ndarray,
    sigma: float,
    noise: np.ndarray,
    start: int = 1,
) -> dict[str, float]:
    """Change in one team's P(win) and expected chops survived if its scores rise by `gains`
    (projected weeks, extended by their mean) from week index `start` on. Same noise both runs."""
    n_weeks = mu.shape[1]
    span = n_weeks - start
    g = np.asarray(gains, dtype=float)
    g = g[:span] if len(g) >= span else np.concatenate([g, np.full(span - len(g), g.mean())])
    base = simulate(mu, sigma, noise=noise)
    bumped = mu.copy()
    bumped[team_idx, start:] += g
    new = simulate(bumped, sigma, noise=noise)
    return {
        "d_p_win": float(new["p_win"][team_idx] - base["p_win"][team_idx]),
        "d_exp_weeks": float(new["exp_weeks"][team_idx] - base["exp_weeks"][team_idx]),
    }
