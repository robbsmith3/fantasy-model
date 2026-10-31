"""Player-level score simulation: each player's outcome is drawn from the empirical distribution
of actual/projected for similar projections, teammates' passing-game outcomes move together,
and a team's score is the sum of the starters its manager would set from projections.

Calibration (see `ffm validate-sim`): historical shapes come from our model's season-level
walk-forward predictions 2019-2025 (games actually played; 54k player-weeks). Their spread
matched 2026 consensus misses within ~10% for projections >= 9 pts. DNP is a separate mass
point, rates measured on the 2026 consensus (which already prices known injuries).
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import polars as pl
from scipy.stats import norm

from fantasy_model import config

MU_EDGES = [2, 5, 8, 11, 14, 17, 20]  # projection bins per position
MIN_BIN = 150  # bins thinner than this borrow from the position's pooled distribution
# P(did not play | projection): 2026 consensus wks 1-7 (2.8% at 5-9 pts, ~0.3% above).
DNP = [(5.0, 0.06), (9.0, 0.028), (float("inf"), 0.003)]
# Correlation of a pass-catcher's outcome with his QB's (2019-25: QB-WR 0.29, QB-TE 0.24).
PASS_LOAD = {"QB": 1.0, "WR": 0.29, "TE": 0.24}
BANK_PATH = config.DATA_DIR / "outcome_bank.parquet"


def build_bank(con, seasons=range(2019, 2026)) -> pl.DataFrame:
    """(position, bin, ratio) rows: actual/projected for played games, ratios normalized to mean
    1 within each bin so the draw's mean equals the projection."""
    from fantasy_model import projection_model as pm

    rows = pm.build_rows(con, list(range(min(seasons) - 1, max(seasons) + 1)))
    out = []
    for s in seasons:
        m = pm.fit(rows.filter(pl.col("season") < s))
        te = rows.filter(pl.col("season") == s)
        out.append(
            te.select("position", "actual", "played").with_columns(
                pl.Series("mu", pm.predict(m, te))
            )
        )
    h = pl.concat(out).filter(pl.col("played") & (pl.col("mu") >= MU_EDGES[0]))
    h = h.with_columns(
        _bin(pl.col("mu")).alias("bin"), (pl.col("actual") / pl.col("mu")).alias("ratio")
    )
    return h.with_columns(pl.col("ratio") / pl.col("ratio").mean().over("position", "bin")).select(
        "position", "bin", "ratio"
    )


def _bin(mu):
    expr = pl.lit(0)
    for i, e in enumerate(MU_EDGES[1:], start=1):
        expr = pl.when(mu >= e).then(pl.lit(i)).otherwise(expr)
    return expr


def load_bank(con, path: Path = BANK_PATH, rebuild: bool = False) -> dict:
    """{(position, bin): sorted ratios}. Cached on disk; rebuild after a new season."""
    if rebuild or not path.exists():
        bank = build_bank(con)
        path.parent.mkdir(parents=True, exist_ok=True)
        bank.write_parquet(path)
    bank = pl.read_parquet(path)
    out = {}
    for (pos,), g in bank.group_by("position"):
        pooled = np.sort(g["ratio"].to_numpy())
        for b in range(len(MU_EDGES)):
            r = g.filter(pl.col("bin") == b)["ratio"].to_numpy()
            out[(pos, b)] = np.sort(r) if len(r) >= MIN_BIN else pooled
    return out


def dnp_prob(mu: float) -> float:
    return next(p for hi, p in DNP if mu < hi)


def mu_bin(mu: float) -> int:
    return sum(mu >= e for e in MU_EDGES[1:])


def sample_players(
    players: pl.DataFrame, bank: dict, n_sims: int, rng: np.random.Generator
) -> np.ndarray:
    """(n_sims, n_players) outcomes. players: position, nfl_team, mu (projected points).
    Gaussian copula: QB/WR/TE load on their NFL team's passing factor; DEF and anything without
    a bank draws ~ projection with 50% CV."""
    teams = {t: i for i, t in enumerate(players["nfl_team"].fill_null("?").unique().to_list())}
    factor = rng.standard_normal((n_sims, len(teams)))
    out = np.zeros((n_sims, players.height))
    for j, (pos, team, mu) in enumerate(players.select("position", "nfl_team", "mu").iter_rows()):
        if mu is None or mu <= 0:
            continue
        a = PASS_LOAD.get(pos, 0.0)
        z = a * factor[:, teams[team or "?"]] + np.sqrt(1 - a * a) * rng.standard_normal(n_sims)
        u = norm.cdf(z)
        ratios = bank.get((pos, mu_bin(mu)))
        if ratios is None:  # DEF etc.: no history bank yet
            draw = np.maximum(mu * (1 + 0.5 * norm.ppf(u)), 0.0)
        else:
            idx = np.minimum((u * len(ratios)).astype(int), len(ratios) - 1)
            draw = mu * ratios[idx]
        played = rng.random(n_sims) >= dnp_prob(mu)
        out[:, j] = np.where(played, draw, 0.0)
    return out


def simulate_lineups(
    players: pl.DataFrame,
    lineups: dict[str, list[str]],
    bank: dict,
    n_sims: int = 20000,
    seed: int = 0,
) -> dict[str, np.ndarray]:
    """{team: (n_sims,) total of its starters}. players: player_id, position, nfl_team, mu.
    A player started by two teams (impossible in one league) is drawn once."""
    rng = np.random.default_rng(seed)
    starters = sorted({p for ids in lineups.values() for p in ids})
    sub = players.filter(pl.col("player_id").is_in(starters))
    draws = sample_players(sub, bank, n_sims, rng)
    col = {pid: j for j, pid in enumerate(sub["player_id"].to_list())}
    return {
        t: draws[:, [col[p] for p in ids if p in col]].sum(axis=1) for t, ids in lineups.items()
    }
