# ruff: noqa: E501
"""Does the 2025 season improve the bid model? And what did late-season FAAB buy? (DEVLOG 2026-11-03)

Walk-forward: each target run is forecast from strictly earlier runs (2025 runs all precede 2026).
Managers carry over between seasons by Sleeper user id. Read-only.
Run: uv run python scripts/experiments/bid_history.py
"""

import json
import math

import duckdb
import numpy as np
import polars as pl

from fantasy_model import bids, config

L25, L26 = "1271671009367318528", "1389333016622436352"
EPS = 0.001  # a 2025 leg's key is leg + EPS, so legs keep their meaning for the leg feature
SIMS = 10000


def season_data(con, lid, season, owner_of, epsilon):
    """(bids, alive, needs) with legs and teams re-keyed to be unique across seasons."""
    bt = bids.bid_table(con, "sleeper", lid, season)
    alive = bids.alive_by_leg(con, "sleeper", lid, season)
    nd = bids.need_table(con, "sleeper", lid, season, bt)
    bt = bt.with_columns(
        (pl.col("leg").cast(pl.Float64) + epsilon).alias("leg"),
        pl.col("team_id").replace_strict(owner_of, default=None).alias("team_id"),
    )
    alive = {w + epsilon: {owner_of[t] for t in ts} for w, ts in alive.items()}
    nd = {(lg + epsilon, owner_of[t], p): v for (lg, t, p), v in nd.items() if t in owner_of}
    return bt, alive, nd


def lgb_fit(rows, seed=0):
    import lightgbm as lgb

    kw = dict(n_estimators=150, learning_rate=0.05, num_leaves=7, min_child_samples=20,
              subsample=0.8, subsample_freq=1, colsample_bytree=0.8, reg_lambda=5.0,
              random_state=seed, verbose=-1)  # fmt: skip
    X, y, share = rows[:, :-2], rows[:, -2], rows[:, -1]
    cat = [X.shape[1] - 1]
    clf = lgb.LGBMClassifier(**kw).fit(X, y, categorical_feature=cat)
    m = y > 0
    reg = lgb.LGBMRegressor(**kw).fit(
        X[m], np.log(np.clip(share[m], 1e-4, 1)), categorical_feature=cat
    )
    sd = float(np.std(np.log(np.clip(share[m], 1e-4, 1)) - reg.predict(X[m])))
    return clf, reg, sd


def main():
    con = duckdb.connect(str(config.DB_PATH), read_only=True)
    owners = {}
    for lid in (L25, L26):
        owners[lid] = dict(
            con.execute("SELECT team_id, owner_id FROM team WHERE league_id = ?", [lid]).fetchall()
        )
    # A roster with no carried-over manager keeps a season-specific key.
    o26 = {t: (o or f"26:{t}") for t, o in owners[L26].items()}
    o25 = {t: (o or f"25:{t}") for t, o in owners[L25].items()}
    bt26, al26, nd26 = season_data(con, L26, 2026, o26, 0.0)
    bt25, al25, nd25 = season_data(con, L25, 2025, o25, EPS)
    bt_all = pl.concat([bt25, bt26], how="diagonal_relaxed")
    alive, needs = al25 | al26, nd25 | nd26
    print("claims: 2025", bt25.height, "2026", bt26.height)

    def spent_before(bt, leg, when):
        return dict(
            bt.filter(pl.col("won") & (pl.col("processed_at") < when))
            .group_by("team_id")
            .agg(pl.col("bid").sum())
            .iter_rows()
        )

    teams = sorted({t for s in alive.values() for t in s})
    tix = {t: i for i, t in enumerate(teams)}

    def lgb_rows(bt, legs):
        run = bids.main_runs(bt.filter(pl.col("leg").is_in(legs)))
        out = []
        for lg in legs:
            r = run.filter(pl.col("leg") == lg)
            if r.is_empty():
                continue
            when = r["processed_at"][0]
            sp = spent_before(bt, lg, when)
            b = {
                (t, p): (x, sh)
                for t, p, x, sh in r.select("team_id", "player_id", "bid", "share").iter_rows()
            }
            for p, q in r.group_by("player_id").agg(pl.col("quality").first()).iter_rows():
                for t in sorted(alive[lg]):
                    x, sh = b.get((t, p), (0, 0.0))
                    f = max(1000 - sp.get(t, 0), 0)
                    out.append([q if q is not None else np.nan, np.log1p(needs.get((lg, t, p), 0.0)),
                                round(lg), np.log1p(f), tix[t], float(x > 0), sh or 0.0])  # fmt: skip
        return np.array(out, dtype=float)

    def forecast(target_bt, leg, kind, train_bt, train_legs, feats=("need",)):
        """{player: (winning bid, simulated top bids)} for one run."""
        run = bids.main_runs(target_bt.filter(pl.col("leg") == leg))
        when = run["processed_at"][0]
        sp = spent_before(target_bt, leg, when)
        opps = [(t, 1000 - sp.get(t, 0), 0.5) for t in sorted(alive[leg])]
        pool = run.group_by("player_id").agg(
            pl.col("quality").first(), pl.col("bid").max().alias("win")
        )
        out = {}
        if kind == "lgb":
            clf, reg, sd = lgb_fit(lgb_rows(train_bt, train_legs))
            rng = np.random.default_rng(0)
        else:
            model = bids.fit(
                train_bt, alive, legs=train_legs, feats=feats, calibrated=False, needs=needs
            )
        for p, q, win in pool.iter_rows():
            if kind == "lgb":
                X = np.array([[q if q is not None else np.nan, np.log1p(needs.get((leg, t, p), 0.0)), round(leg),
                               np.log1p(max(f, 0)), tix[t]] for t, f, _ in opps], dtype=float)  # fmt: skip
                pb, mean = clf.predict_proba(X)[:, 1], reg.predict(X)
                f = np.array([max(o[1], 0) for o in opps], dtype=float)
                amt = np.floor(
                    np.minimum(np.exp(mean + sd * rng.standard_normal((SIMS, len(f)))), 1) * f
                )
                mx = np.where(rng.random((SIMS, len(f))) < pb, amt, 0).max(1)
            else:
                mx = bids.max_opponent_bids(
                    model,
                    q,
                    opps,
                    0.0,
                    round(leg),
                    SIMS,
                    needs=[needs.get((leg, t, p), 0.0) for t, _, _ in opps],
                )
            out[p] = (win, mx)
        return out

    def grade(res, min_win=20):
        rows = [(w, bids.crps(m, w), np.quantile(m, 0.1) <= w <= np.quantile(m, 0.9), np.median(m) - w)
                for lg in res.values() for w, m in lg.values() if w >= min_win]  # fmt: skip
        a = np.array(rows, dtype=float)
        return (
            dict(
                n=len(a),
                crps=round(float(a[:, 1].mean()), 1),
                cover=round(float(a[:, 2].mean()), 2),
                bias=round(float(a[:, 3].mean()), 1),
            )
            if len(a)
            else {}
        )

    legs26 = [3.0, 4.0, 5.0, 6.0, 7.0]
    all25 = sorted(x for x in bt25["leg"].unique().to_list() if x < 18)
    print("\n== 2026 runs 3-7, 33 auctions: what to train on")
    res26 = {}
    for name, kind, feats, with25 in (
        ("2026 only (current)", "lin", ("need",), False),
        ("+2025", "lin", ("need",), True),
        ("+2025, leg feature", "lin", ("need", "leg"), True),
        ("2026 only, LightGBM", "lgb", None, False),
        ("+2025, LightGBM", "lgb", None, True),
    ):
        res = {}
        for lg in legs26:
            tl = ([*all25] if with25 else []) + [x for x in legs26 + [1.0, 2.0] if x < lg]
            res[lg] = forecast(bt26, lg, kind, bt_all, sorted(tl), feats)
        res26[name] = res
        byleg = {int(lg): grade({lg: res[lg]}).get("crps") for lg in legs26}
        print(f"  {name:22}", grade(res), byleg)
    base = res26["2026 only (current)"]
    rng = np.random.default_rng(0)
    print("  paired CRPS change vs current, 95% by auction")
    for name, res in res26.items():
        if name.startswith("2026 only (cur"):
            continue
        d = np.array(
            [
                bids.crps(res[lg][p][1], res[lg][p][0]) - bids.crps(base[lg][p][1], base[lg][p][0])
                for lg in legs26
                for p in res[lg]
                if res[lg][p][0] >= 20
            ]
        )
        v = d[rng.integers(0, len(d), (5000, len(d)))].mean(1)
        print(
            f"    {name:22} {d.mean():+6.1f} [{np.quantile(v, 0.025):+6.1f}, {np.quantile(v, 0.975):+6.1f}] better in {(d < 0).sum()}/{len(d)}"
        )

    print("\n== 2025 run by run (a full season), trained on earlier 2025 runs only")
    for name, kind, feats in (
        ("linear", "lin", ("need",)),
        ("linear + leg", "lin", ("need", "leg")),
        ("LightGBM", "lgb", None),
    ):
        res = {}
        for lg in all25:
            tl = [x for x in all25 if x < lg]
            has = bids.main_runs(bt25.filter(pl.col("leg").is_in(tl))) if len(tl) >= 2 else None
            if (
                has is None
                or has.filter(pl.col("bid") > 0).height < 30
                or bt25.filter(pl.col("leg") == lg).is_empty()
            ):
                continue
            res[lg] = forecast(bt25, lg, kind, bt25, tl, feats)
        early = {k: v for k, v in res.items() if k < 8}
        mid = {k: v for k, v in res.items() if 8 <= k < 11}
        late = {k: v for k, v in res.items() if k >= 11}
        print(
            f"  {name:13} wk<8 {grade(early)} | wk8-10 {grade(mid)} | wk11+ (bid>=5) {grade(late, 5)}"
        )
        if late:
            med = [
                (round(k), round(float(np.median(m))), w)
                for k, v in sorted(late.items())
                for w, m in sorted(v.values(), key=lambda x: -x[0])[:1]
            ]
            print("     late runs: (week, forecast median, actual top bid)", med)

    print("\n== 2025 FAAB by run (alive teams)")
    run25 = bids.main_runs(bt25)
    final = dict(
        con.execute(
            "SELECT team_id, min(week) FROM txn WHERE league_id = ? AND type = 'chopped' GROUP BY 1",
            [L25],
        ).fetchall()
    )
    name25 = {
        o25[t]: n
        for t, n in con.execute(
            "SELECT team_id, coalesce(team_name, owner_name, team_id) FROM team WHERE league_id = ?",
            [L25],
        ).fetchall()
    }
    me25 = con.execute("SELECT team_id FROM team WHERE league_id = ? AND is_me", [L25]).fetchall()[
        0
    ][0]
    bal = {}
    for lg in all25:
        r = run25.filter(pl.col("leg") == lg)
        if r.is_empty():
            continue
        sp = spent_before(bt25, lg, r["processed_at"][0])
        f = sorted(((1000 - sp.get(t, 0), t) for t in alive[lg]), reverse=True)
        if len(f) < 2:
            continue
        bal[round(lg)] = dict((t, x) for x, t in f)
        w = (
            r.group_by("player_id")
            .agg(pl.col("bid").max(), pl.col("quality").first())
            .sort("bid", descending=True)
        )
        top_q = w.sort("quality", descending=True, nulls_last=True).head(3)
        print(
            f"  wk{round(lg):2} alive {len(f):2} total ${sum(x for x, _ in f):5} top ${f[0][0]:4} 2nd ${f[1][0]:4} median ${int(np.median([x for x, _ in f])):4} teams<$50 {sum(x < 50 for x, _ in f):2} | top bid ${w['bid'][0]:3} | best-rated claims (rating, price): "
            + ", ".join(f"{q:.0f}:${b}" for q, b in top_q.select("quality", "bid").iter_rows() if q)
        )

    print(
        "\n== did money predict going deep? FAAB rank at a run vs weeks survived after it (2025, one season)"
    )
    last = max(final.values())
    survived = {o25[t]: final.get(t, last + 1) for t in o25}
    for wk in (5, 8, 10, 12):
        if wk not in bal:
            continue
        ts = list(bal[wk])
        x = pl.Series([bal[wk][t] for t in ts]).rank().to_numpy()
        y = pl.Series([survived[t] for t in ts]).rank().to_numpy()
        print(f"  wk{wk}: {len(ts)} teams, rank correlation {np.corrcoef(x, y)[0, 1]:+.2f}")
    fin = sorted(survived.items(), key=lambda kv: -kv[1])[:6]
    print(
        "  finish (team, chopped wk; 18 = won), money at wk8 / wk12:",
        [(name25.get(t, t)[:14], w, bal.get(8, {}).get(t), bal.get(12, {}).get(t)) for t, w in fin],
    )
    print(
        "  me:",
        name25.get(o25[me25]),
        "chopped wk",
        survived[o25[me25]],
        {k: v.get(o25[me25]) for k, v in bal.items()},
    )

    claim_stage(con, o25, o26)


def claim_stage(con, o25, o26):
    """Will anyone claim him? A player-level model on the whole pool, 2026 runs 3-7."""
    pos = bids.positions(con)

    def rows(lid, season, owner, slots_lid):
        bt = bids.bid_table(con, "sleeper", lid, season)
        run = bids.main_runs(bt)
        slots = bids.league_slots(con, "sleeper", slots_lid)
        alive = bids.alive_by_leg(con, "sleeper", lid, season)
        pts = bids.weekly_ppr(con, season)
        prior = dict(
            bids.weekly_ppr(con, season - 1)
            .group_by("player_id")
            .agg(pl.col("pts").mean())
            .iter_rows()
        )
        out = []
        for leg in sorted(run["leg"].unique().to_list()):
            r = run.filter(pl.col("leg") == leg)
            if r.height < 3:
                continue
            tw = con.execute(
                "SELECT team_id, player_points FROM team_week WHERE league_id = ? AND week = ?",
                [lid, leg],
            ).fetchall()
            ros = {
                t: list((json.loads(p) if isinstance(p, str) else p or {}).keys()) for t, p in tw
            }
            agg = (
                pts.filter(pl.col("week") <= leg)
                .group_by("player_id")
                .agg(pl.col("pts").sum(), pl.len().alias("g"))
            )
            seen = {p: (s, n) for p, s, n in agg.iter_rows()}

            def q(p, seen=seen):
                return bids.quality(prior.get(p), *seen.get(p, (0.0, 0)))

            recent = set(pts.filter(pl.col("week").is_between(leg - 3, leg))["player_id"].to_list())
            owned = {p for t, ids in ros.items() if t in alive[leg] for p in ids}
            rate = {
                t: [(p, pos.get(p, "DEF"), q(p) or 0.0) for p in ids]
                for t, ids in ros.items()
                if t in alive[leg] and ids
            }
            claimed = set(r.filter(pl.col("bid") > 0)["player_id"].to_list())
            pool = {
                p for p in (recent | claimed) if p not in owned and pos.get(p) in ("RB", "WR", "TE")
            }
            sp = dict(
                bt.filter(pl.col("won") & (pl.col("processed_at") < r["processed_at"][0]))
                .group_by("team_id")
                .agg(pl.col("bid").sum())
                .iter_rows()
            )
            money = sum(max(1000 - sp.get(t, 0), 0) for t in alive[leg])
            ranks = sorted(((q(p) or 0.0), p) for p in pool)
            n = max(len(ranks) - 1, 1)
            order = {p: i / n for i, (_, p) in enumerate(ranks)}
            for p in pool:
                nd = sorted(
                    (bids.need(ro, pos.get(p), q(p), slots) for ro in rate.values()), reverse=True
                )
                out.append({"season": season, "leg": leg, "player_id": p, "q": q(p) or 0.0, "top_need": math.log1p(nd[0]),
                            "needy": math.log1p(sum(x > 0.5 for x in nd)), "pool_rank": order[p], "money": math.log1p(money / len(rate)),
                            "claimed": float(p in claimed)})  # fmt: skip
        return pl.DataFrame(out)

    d25, d26 = rows(L25, 2025, o25, L25), rows(L26, 2026, o26, L26)
    feats = ["q", "top_need", "needy", "pool_rank", "money"]
    print(f"\n== claim stage: pool rows 2025 {d25.height}, 2026 {d26.height}")
    for name, with25, cols in (("2026 only", False, feats), ("+2025", True, feats)):
        ps, ys, hit, tops = [], [], 0, 0
        for leg in (3, 4, 5, 6, 7):
            tr = d26.filter(pl.col("leg") < leg)
            if with25:
                tr = pl.concat([d25, tr])
            te = d26.filter(pl.col("leg") == leg)
            mu, sd = tr.select(cols).to_numpy().mean(0), tr.select(cols).to_numpy().std(0) + 1e-9
            X = np.column_stack([np.ones(tr.height), (tr.select(cols).to_numpy() - mu) / sd])
            w = bids._logistic(X, tr["claimed"].to_numpy())
            Xt = np.column_stack([np.ones(te.height), (te.select(cols).to_numpy() - mu) / sd])
            p = 1 / (1 + np.exp(-Xt @ w))
            y = te["claimed"].to_numpy()
            ps.append(p), ys.append(y)
            hit += int(y[np.argsort(-p)[:15]].sum())
            tops += int(y.sum())
        p, y = np.concatenate(ps), np.concatenate(ys)
        cal = []
        for lo, hi in ((0, 0.05), (0.05, 0.15), (0.15, 0.4), (0.4, 0.7), (0.7, 1.01)):
            m = (p >= lo) & (p < hi)
            if m.sum():
                cal.append(
                    f"{lo:.0%}-{min(hi, 1):.0%}: n={m.sum()} pred {p[m].mean():.0%} real {y[m].mean():.0%}"
                )
        ll = -np.mean(y * np.log(p + 1e-9) + (1 - y) * np.log(1 - p + 1e-9))
        print(
            f"  {name:10} expected per run {p.sum() / 5:.1f} vs actual {y.sum() / 5:.1f}; top 15 caught {hit}/{tops}; log loss {ll:.3f}\n    "
            + " | ".join(cal)
        )


if __name__ == "__main__":
    main()
