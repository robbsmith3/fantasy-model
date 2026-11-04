# ruff: noqa: E501
"""Should forward projections drive the bid model? (DEVLOG 2026-11-03)

The bid model rates players on past scoring ("quality"), and fits only players somebody claimed.
Walk-forward on 2026 legs 3-7, rating players by each variant in place of quality (roster need
is rebuilt on the same rating), optionally training participation on the whole waiver pool.
Projections come from before each week's games but after that week's waiver run, except `own`
(our Tuesday model). Read-only; OWN_PREDS comes from context_features.py.
Run: uv run python scripts/experiments/bid_projection.py OWN_PREDS.parquet
"""

import json
import sys

import duckdb
import numpy as np
import polars as pl

from fantasy_model import bids, config, value

LEGS, TOP, SIMS = [3, 4, 5, 6, 7], 15, 5000
BEFORE = "2026-11-01"  # Sleeper/ESPN snapshots from before week 8's Sunday games
SEASON = 2026


def projections(con, scoring, own_path):
    """{name: {(leg, player): points}}: the next two weeks' mean, ignoring byes (0 or missing)."""
    long = []
    for s in ("sleeper", "espn"):
        long.append(value.source_points(con, scoring, s, list(range(2, 9)), SEASON, BEFORE).select(
            "player_id", pl.col("week").cast(pl.Int64), "points"
        ))  # fmt: skip
    wk = pl.concat(long).group_by("player_id", "week").agg(pl.col("points").mean())
    by = {(p, w): v for p, w, v in wk.iter_rows()}
    players = {p for p, _ in by}
    cons = {}
    for leg in range(1, 8):
        for p in players:
            vals = [by[(p, w)] for w in (leg + 1, leg + 2) if w <= 8 and by.get((p, w), 0) > 0]
            if vals:
                cons[(leg, p)] = sum(vals) / len(vals)
    g2s = dict(con.execute(
        "SELECT gsis_id, sleeper_id FROM player_id_map"
        " WHERE gsis_id IS NOT NULL AND sleeper_id IS NOT NULL"
    ).fetchall())  # fmt: skip
    own = {}
    for g, w, v in pl.read_parquet(own_path).select("player_id", "week", "points").iter_rows():
        if g in g2s and w >= 2 and v > 0:
            own[(w - 1, g2s[g])] = v
    return cons, own


def rosters_at(con, lid, leg):
    rows = con.execute(
        "SELECT team_id, player_points FROM team_week WHERE platform = 'sleeper' AND league_id = ?"
        " AND season = ? AND week = ?",
        [lid, SEASON, leg],
    ).fetchall()
    return {t: list((json.loads(p) if isinstance(p, str) else p or {}).keys()) for t, p in rows}


def base_quality(con, leg, prior):
    agg = bids.weekly_ppr(con, SEASON).filter(pl.col("week") <= leg).group_by("player_id").agg(
        pl.col("pts").sum(), pl.len().alias("g")
    )  # fmt: skip
    q = {p: bids.quality(prior.get(p), s, n) for p, s, n in agg.iter_rows()}
    for p, v in prior.items():
        q.setdefault(p, bids.quality(v, 0.0, 0))
    return q


def main(own_path):
    con = duckdb.connect(str(config.DB_PATH), read_only=True)
    lid = con.execute(
        "SELECT league_id FROM league WHERE platform = 'sleeper' AND season = ?", [SEASON]
    ).fetchall()[0][0]
    scoring = value.league_scoring(con, "sleeper", lid)
    slots = bids.league_slots(con, "sleeper", lid)
    pos = bids.positions(con)
    alive = bids.alive_by_leg(con, "sleeper", lid, SEASON)
    bt = bids.bid_table(con, "sleeper", lid, SEASON).filter(pl.col("leg") <= max(LEGS))
    cons, own = projections(con, scoring, own_path)
    prior = dict(
        bids.weekly_ppr(con, SEASON - 1).group_by("player_id").agg(pl.col("pts").mean()).iter_rows()
    )
    legs = range(1, max(LEGS) + 1)
    baseq = {lg: base_quality(con, lg, prior) for lg in legs}
    rost = {lg: rosters_at(con, lid, lg) for lg in legs}
    main_at = dict(bids.main_runs(bt).select("leg", "processed_at").unique().iter_rows())
    runs = bids.main_runs(bt)

    # Waiver pool at each leg: projected players not on a surviving roster (the chopped team's
    # roster has just been released). Pickups since the last run aren't visible, a small gap.
    pool = {}
    for lg in legs:
        owned = {p for t, ids in rost[lg].items() if t in alive[lg] for p in ids}
        ids = {p for (w, p) in cons if w == lg} | {p for (w, p) in own if w == lg}
        pool[lg] = {p for p in ids if p not in owned and pos.get(p, "DEF") != "DEF"}
    cov = []
    for lg in LEGS:
        c = runs.filter((pl.col("leg") == lg) & (pl.col("bid") > 0))["player_id"].unique().to_list()
        c = [p for p in c if pos.get(p, "DEF") != "DEF"]
        cov.append((lg, len(c), sum(p in pool[lg] for p in c), len(pool[lg])))
    print("legs: claimed players, of which in reconstructed pool, pool size:", cov)

    def rating(kind, lg, p):
        q = baseq[lg].get(p)
        if pos.get(p, "DEF") == "DEF":
            return q
        if kind == "base":
            return q
        pj = (cons if kind in ("proj", "blend") else own).get((lg, p))
        if pj is None:
            return q
        return pj if kind in ("proj", "own") else (pj + (q if q is not None else pj)) / 2

    variants = {"base": ("base", False), "proj": ("proj", False), "blend": ("blend", False),
                "own": ("own", False), "base+pool": ("base", True), "proj+pool": ("proj", True),
                "blend+pool": ("blend", True)}  # fmt: skip
    results = {}
    for name, (kind, use_pool) in variants.items():
        needs, rated = {}, {}
        tab = bt.with_columns(
            pl.struct("leg", "player_id").map_elements(
                lambda r, kind=kind: rating(kind, r["leg"], r["player_id"]),
                return_dtype=pl.Float64,
            ).alias("quality")
        )  # fmt: skip
        for lg in legs:
            rs = {
                t: [(p, pos.get(p, "DEF"), rating(kind, lg, p) or 0.0) for p in ids]
                for t, ids in rost[lg].items()
                if ids
            }
            everyone = set(bt.filter(pl.col("leg") == lg)["player_id"].to_list()) | pool[lg]
            for p in everyone:
                r = rating(kind, lg, p)
                rated[(lg, p)] = r
                for t, ro in rs.items():
                    needs[(lg, t, p)] = bids.need(ro, pos.get(p), r, slots)
        train = tab
        if use_pool:
            extra = []
            for lg in legs:
                seen = set(tab.filter(pl.col("leg") == lg)["player_id"].to_list())
                t0 = sorted(alive[lg])[0]
                extra += [
                    {"leg": lg, "team_id": t0, "player_id": p, "bid": 0, "quality": rated[(lg, p)],
                     "processed_at": main_at[lg], "won": False,
                     "transaction_id": f"pool{lg}{p}"}
                    for p in pool[lg] - seen
                ]  # fmt: skip
            add = pl.DataFrame(extra, infer_schema_length=None).with_columns(
                pl.col("leg").cast(tab["leg"].dtype), pl.col("bid").cast(tab["bid"].dtype),
                pl.col("processed_at").cast(tab["processed_at"].dtype),
                pl.col("transaction_id").cast(tab["transaction_id"].dtype),
                pl.col("player_id").cast(tab["player_id"].dtype),
                pl.col("team_id").cast(tab["team_id"].dtype),
            )  # fmt: skip
            train = pl.concat([tab, add], how="diagonal_relaxed")
        auctions, ranks, calib = [], [], []
        for leg in LEGS:
            model = bids.fit(train, alive, legs=list(range(1, leg)), calibrated=False, needs=needs)
            run = runs.filter(pl.col("leg") == leg)
            mp = run["processed_at"][0]
            spent = dict(bt.filter(pl.col("won") & (pl.col("processed_at") < mp))
                         .group_by("team_id").agg(pl.col("bid").sum()).iter_rows())  # fmt: skip
            opps = [(t, 1000 - spent.get(t, 0), 0.5) for t in sorted(alive[leg])]
            win = dict(run.group_by("player_id").agg(pl.col("bid").max()).iter_rows())

            def sim(p, n=SIMS, opps=opps, leg=leg, model=model, needs=needs, rated=rated):
                nd = [needs.get((leg, t, p), 0.0) for t, _, _ in opps]
                return bids.max_opponent_bids(
                    model, rated.get((leg, p)), opps, 0.0, leg, n, needs=nd
                )  # noqa: E501

            for p, w in win.items():
                if w >= 20:
                    mx = sim(p, 20000)
                    lo, med, hi = np.quantile(mx, [0.1, 0.5, 0.9])
                    auctions.append(
                        (leg, p, w, bids.crps(mx, w), lo <= w <= hi, med - w, med, hi - lo)
                    )  # noqa: E501
            scored = []
            for p in pool[leg]:
                if pos.get(p) == "QB":
                    continue
                mx = sim(p)
                scored.append(
                    (
                        p,
                        float((mx > 0).mean()),
                        float(np.median(mx[mx > 0])) if (mx > 0).any() else 0.0,
                    )
                )  # noqa: E501
            scored.sort(key=lambda x: (-x[1], -x[2]))
            claimed = {p for p, w in win.items() if w > 0}
            top = scored[:TOP]
            hits = sum(p in claimed for p, _, _ in top)
            contested = {p for p, w in win.items() if w >= 20}
            actual_pool = [p for p, _, _ in scored if p in claimed]
            ranks.append(
                {
                    "leg": leg,
                    "hits": hits,
                    "contested_in_top": sum(p in contested for p, _, _ in top),
                    "contested": len(contested & set(x[0] for x in scored)),
                    "claimed_in_pool": len(actual_pool),
                    "pool": len(scored),
                    "expected": sum(x[1] for x in scored),
                }
            )  # noqa: E501,fmt: skip
            calib += [(x[1], x[0] in claimed) for x in scored]
        a = np.array([x[2:6] + (x[7],) for x in auctions], dtype=float)
        results[name] = {"auctions": auctions, "ranks": ranks, "calib": calib}
        print(f"{name:11} n {len(a)} CRPS {a[:, 1].mean():6.1f} cover80 {a[:, 2].mean():.2f} "
              f"bias {a[:, 3].mean():+6.1f} width {a[:, 4].mean():5.0f} | top{TOP} claimed "
              f"{sum(r['hits'] for r in ranks)}/{TOP * len(ranks)}, contested caught "
              f"{sum(r['contested_in_top'] for r in ranks)}/{sum(r['contested'] for r in ranks)} | "
              f"expected claims {np.mean([r['expected'] for r in ranks]):.0f} vs actual "
              f"{np.mean([r['claimed_in_pool'] for r in ranks]):.0f}", flush=True)  # fmt: skip
    print("\nCRPS by leg")
    for name, r in results.items():
        print(
            f"  {name:11}",
            {
                lg: round(float(np.mean([x[3] for x in r["auctions"] if x[0] == lg])), 1)
                for lg in LEGS
            },
        )  # noqa: E501
    base = {(x[0], x[1]): x for x in results["base"]["auctions"]}
    rng = np.random.default_rng(0)
    print("\nCRPS change vs base, 95% bootstrap by auction")
    for name, r in results.items():
        if name == "base":
            continue
        d = np.array([x[3] - base[(x[0], x[1])][3] for x in r["auctions"]])
        v = d[rng.integers(0, len(d), (5000, len(d)))].mean(1)
        print(
            f"  {name:11} {d.mean():+6.1f} [{np.quantile(v, 0.025):+6.1f}, {np.quantile(v, 0.975):+6.1f}]  better in {(d < 0).sum()}/{len(d)}"
        )  # noqa: E501
    print("\nchance-claimed calibration: bin -> players, mean predicted, actually claimed")
    for name in ("base", "proj", "blend", "base+pool", "proj+pool", "blend+pool"):
        c = np.array(results[name]["calib"], dtype=float)
        out = []
        for lo, hi in ((0, 0.1), (0.1, 0.25), (0.25, 0.5), (0.5, 0.75), (0.75, 1.01)):
            m = (c[:, 0] >= lo) & (c[:, 0] < hi)
            if m.sum():
                out.append(
                    f"{lo:.0%}-{min(hi, 1):.0%}: {m.sum()} / {c[m, 0].mean():.0%} / {c[m, 1].mean():.0%}"
                )  # noqa: E501
        print(f"  {name:11}", " | ".join(out))
    name_of = dict(
        con.execute(
            "SELECT sleeper_id, name FROM player_id_map WHERE sleeper_id IS NOT NULL"
        ).fetchall()
    )  # noqa: E501
    print("\nbiggest base misses (actual, then each variant's median)")
    big = sorted(results["base"]["auctions"], key=lambda x: -abs(x[5]))[:10]
    for x in big:
        row = {
            n: round(next((y[6] for y in results[n]["auctions"] if y[:2] == x[:2]), -1))
            for n in ("base", "proj", "blend", "own", "blend+pool")
        }  # noqa: E501
        print(f"  leg {x[0]} {name_of.get(x[1], x[1])[:22]:22} actual {x[2]:4}", row)


if __name__ == "__main__":
    main(sys.argv[1])
