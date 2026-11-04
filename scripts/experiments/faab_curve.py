# ruff: noqa: E501
"""How this week's plan moves with the FAAB value curve's assumptions (DEVLOG 2026-11-03).
Run: uv run python scripts/experiments/faab_curve.py"""

import time

import duckdb
import numpy as np
import polars as pl

from fantasy_model import config, faab_value, recommend, survival

pl.Config.set_tbl_cols(12), pl.Config.set_tbl_width_chars(170), pl.Config.set_fmt_str_lengths(22)
con = duckdb.connect(str(config.DB_PATH), read_only=True)
lid = con.execute(
    "SELECT league_id FROM league WHERE platform = 'sleeper' AND season = 2026"
).fetchall()[0][0]
ctx = recommend.load_context(con, "sleeper", lid)
st = recommend.league_state(con, ctx)
t0 = time.time()
a = faab_value.build_auctions(st.vals, ctx, st.gain_weeks, st.model, st.quality)
print(f"auctions built {time.time() - t0:.0f}s")
mu = survival.extend_horizon(st.proj, len(ctx.alive) - 1)
faab = np.array([float(ctx.faab[t]) for t in ctx.alive])
n_sims = 20000
noise = st.noise[:n_sims]
draws = faab_value._draws(a, n_sims, mu.shape[1])
me = st.me_i

print(f"existing simulation P(win) {st.base['p_win'][me]:.3f}")
print("measured pickup fade (next / previous):",
      np.round(faab_value.pickup_fade(st.vals, ctx.slots, st.gain_weeks, ctx.alive), 2))  # fmt: skip
keep = {}
SCENARIOS = {
    "main": (faab_value.FADE, faab_value.LATE_PRICE),
    "late prices as model": (faab_value.FADE, 1.0),
    "late prices 0.15": (faab_value.FADE, 0.15),
    "fade none": (np.ones(50), faab_value.LATE_PRICE),
    "fade 0.5": (0.5 ** np.arange(50), faab_value.LATE_PRICE),
}
for name, (fd, lp) in SCENARIOS.items():
    t0 = time.time()
    c = faab_value.value_curve(mu, st.sigma, noise, a, faab, me, st.start, fd, draws, lp)
    keep[name] = c
    print(f"\n{name} ({time.time() - t0:.0f}s)")
    print(
        "  " + "  ".join(f"${b}:{v:.3f}" for b, v in zip(c["balances"], c["values"], strict=True))
    )
    r, sc = c["rules"][-1]
    run = faab_value.play_season(mu, st.sigma, noise, a, faab, me, r, st.start, draws, fd, sc, lp)
    print("  rival spend per round:", np.round(run["spend"]).astype(int).tolist(),
          f"; auctions we win {run['my_wins']:.1f}; our money left {run['money_end'][me]:.0f}")  # fmt: skip

other = {}
for sd in (101, 202):
    nz = survival.common_noise(len(faab), mu.shape[1], n_sims, seed=sd)
    other[sd] = faab_value.value_curve(mu, st.sigma, nz, a, faab, me, st.start,
                                       draws=faab_value._draws(a, n_sims, mu.shape[1], seed=sd))  # fmt: skip
    print(f"\nother random draws {sd}:", "  ".join(
        f"${b}:{v:.3f}" for b, v in zip(other[sd]["balances"], other[sd]["values"], strict=True)))  # fmt: skip

f = int(faab[me])
base = recommend.recommend(con, ctx, curve=keep["main"]["array"])
print("\ncost of spending from $500 (win probability):")
for b in (25, 50, 100, 200, 300, 400, 450, 500):
    print(f"  ${b}: " + " ".join(
        f"{d}: {k['array'][f] - k['array'][f - b]:.2%}" for d, k in keep.items()))  # fmt: skip

cols = ["player", "pos", "bid", "p_win_claim", "d_p_win", "deny", "market_median"]
for d, k in {**keep, **{f"seed {x}": v for x, v in other.items()}}.items():
    new = recommend.recommend(con, ctx, curve=k["array"])
    print(f"PLAN curve {d}"), print(new["plan"].drop("player_id", "drop"))
    print(new["sheet"].select([*cols, "walk_away"]))

# Is "player value + money value" a fair shortcut? Play the season with both changed.
print("\nshortcut check, main: additive vs simulating roster and money together")
sheet = base["sheet"]
gains_by = {}
from fantasy_model import fit  # noqa: E402

for name in ("Amon-Ra St. Brown", "Bijan Robinson"):
    row = sheet.filter(pl.col("player") == name).row(0, named=True)
    g, _ = fit.claim_gain(st.vals, ctx.me, ctx.slots, st.gain_weeks, row["player_id"])
    bumped = mu.copy()
    bumped[me, st.start :] += float(np.mean(g))
    best = {}
    small, small_draws = noise[:6000], tuple(x[:6000] for x in draws)
    arr = faab_value.value_curve(mu, st.sigma, small, a, faab, me, st.start, draws=small_draws)[
        "array"
    ]
    base_v = arr[f]
    for bid in (150, 275, 375, 450):
        best[bid] = faab_value.value_curve(
            bumped,
            st.sigma,
            small,
            a,
            np.where(np.arange(len(faab)) == me, faab - bid, faab),
            me,
            st.start,
            draws=small_draws,
        )["values"][-1]  # fmt: skip
    for bid, v in best.items():
        print(f"  {name} ${bid}: direct {v - base_v:+.2%}, additive "
              f"{row['d_p_win'] - (arr[f] - arr[f - bid]):+.2%}")  # fmt: skip
