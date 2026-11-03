"""Do injury and matchup context features improve projections? (DEVLOG 2026-11-03)

Walk-forward backtest of projection_model.CANDIDATE_GROUPS against the current model, at two
horizons: Tuesday (no target-week injury report) and in-week (report known). Read-only.
Run: uv run python scripts/experiments/context_features.py run OUT_DIR   (predictions, resumable)
     uv run python scripts/experiments/context_features.py report OUT_DIR
"""

import json
import sys
from pathlib import Path

import duckdb
import numpy as np
import polars as pl

from fantasy_model import config, value
from fantasy_model import projection_model as pm

HORIZONS = {"tue": False, "wk": True}
VARIANTS = {"base": [], **{g: [g] for g in pm.CANDIDATE_GROUPS}, "all": list(pm.CANDIDATE_GROUPS)}
SEEDS_2026, SEEDS_LONG = (0, 1, 2), (0,)
LONG_SEASONS, LONG_BLOCK = (2023, 2024, 2025), 3  # retrain every 3 weeks to keep the run practical
# Flags fixed before seeing results: one starter's worth of usage, or one of five linemen.
OPEN_MIN, OL_MIN = 4.0, 0.2
SOURCES_BEFORE = "2026-11-01"  # Sleeper/ESPN week 8: last snapshot before Sunday's games


def blocks(season):
    if season == 2026:
        return [[w] for w in range(1, 9)]
    return [list(range(w, min(w + LONG_BLOCK, 19))) for w in range(1, 19, LONG_BLOCK)]


def run(out: Path):
    out.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(config.DB_PATH), read_only=True)
    cache = out / "rows.parquet"
    if cache.exists():
        rows = pl.read_parquet(cache)
    else:
        rows = pm.build_rows(con, list(range(2018, 2027)))
        rows = rows.filter(pl.col("season") * 20 + pl.col("week") < 2026 * 20 + 9)
        rows = pm.context_features(con, rows)
        rows.write_parquet(cache)
    print("rows", rows.height, flush=True)
    key = pl.col("season") * 20 + pl.col("week")
    for season in (2026, *LONG_SEASONS):
        seeds = SEEDS_2026 if season == 2026 else SEEDS_LONG
        for hz, in_week in HORIZONS.items():
            for name, groups in VARIANTS.items():
                for seed in seeds:
                    path = out / f"p_{season}_{hz}_{name}_{seed}.parquet"
                    if path.exists():
                        continue
                    feats, parts = pm.feature_set(groups, in_week), []
                    for b in blocks(season):
                        train = rows.filter(key < season * 20 + b[0])
                        test = rows.filter((pl.col("season") == season) & pl.col("week").is_in(b))
                        m = pm.fit(train, feats, seed)
                        parts.append(test.select("player_id", "season", "week").with_columns(
                            pl.Series("points", pm.predict(m, test))
                        ))  # fmt: skip
                    pl.concat(parts).write_parquet(path)
                    print("done", path.name, flush=True)


def preds(out, season, hz, name, seed):
    return pl.read_parquet(out / f"p_{season}_{hz}_{name}_{seed}.parquet").rename(
        {"points": "model"}
    )


def sources_2026(con):
    lid = con.execute(
        "SELECT league_id FROM league WHERE platform = 'sleeper' AND season = 2026"
    ).fetchall()[0][0]
    scoring = value.league_scoring(con, "sleeper", lid)
    weeks = list(range(1, 9))
    wide = None
    for s, before in (("sleeper", SOURCES_BEFORE), ("espn", SOURCES_BEFORE), ("fantasypros", None)):
        d = value.source_points(con, scoring, s, weeks, 2026, before).select(
            "player_id", pl.col("week").cast(pl.Int64), pl.col("points").alias(s)
        )
        wide = (
            d if wide is None else wide.join(d, on=["player_id", "week"], how="full", coalesce=True)
        )  # noqa: E501
    g2s = dict(con.execute(
        "SELECT gsis_id, sleeper_id FROM player_id_map"
        " WHERE gsis_id IS NOT NULL AND sleeper_id IS NOT NULL"
    ).fetchall())  # fmt: skip
    return wide.rename({"player_id": "sleeper_id"}), g2s


def scored(t, col):
    e = t[col] - t["actual"]
    return float(e.abs().mean()), float(e.mean())


def boot(t, a, b, n=2000):
    """95% interval for mean(|a err| - |b err|), resampling team-weeks. Negative: a is better."""
    d = (
        t.with_columns(
            ((pl.col(a) - pl.col("actual")).abs() - (pl.col(b) - pl.col("actual")).abs()).alias("d")
        )
        .group_by("team", "week")
        .agg(pl.col("d").sum().alias("s"), pl.len().alias("n"))
    )
    s, c = d["s"].to_numpy(), d["n"].to_numpy()
    rng = np.random.default_rng(0)
    i = rng.integers(0, len(s), (n, len(s)))
    v = s[i].sum(1) / c[i].sum(1)
    return float(np.quantile(v, 0.025)), float(np.quantile(v, 0.975))


def report(out: Path):
    con = duckdb.connect(str(config.DB_PATH), read_only=True)
    rows = pl.read_parquet(out / "rows.parquet")
    src, g2s = sources_2026(con)
    meta_cols = ["player_id", "season", "week", "team", "position", "actual"]
    meta = rows.select(
        *meta_cols,
        *[c for c in rows.columns if c.startswith(("open_", "ol_out_", "qb_out_"))],
    ).with_columns(
        pl.col("week").cast(pl.Int64),
        pl.col("player_id").replace_strict(g2s, default=None).alias("sleeper_id"),
    )
    res: dict = {"coverage": {}}
    for c in ("open_tgt_t", "open_tgt_w", "ol_out_t", "ol_out_w", "dl_out_t", "def_adj",
              "career_ppg", "career_blend", "age"):  # fmt: skip
        res["coverage"][c] = float(rows.filter(pl.col("season") == 2026)[c].is_not_null().mean())

    def flag(t, h):
        return t.with_columns(
            (
                (pl.col(f"open_tgt_{h}").fill_null(0) + pl.col(f"open_car_{h}").fill_null(0)
                 >= OPEN_MIN)
                | (pl.col(f"ol_out_{h}").fill_null(0) >= OL_MIN)
            ).alias("hit"),
            (pl.col(f"open_tgt_{h}").fill_null(0) + pl.col(f"open_car_{h}").fill_null(0)
             >= OPEN_MIN).alias("hit_mate"),
            (pl.col(f"ol_out_{h}").fill_null(0) >= OL_MIN).alias("hit_ol"),
        )  # fmt: skip

    for hz in HORIZONS:
        h = "t" if hz == "tue" else "w"
        base = None
        for seed in SEEDS_2026:
            b = preds(out, 2026, hz, "base", seed).select(
                "player_id", pl.col("week").cast(pl.Int64), pl.col("model").alias(f"b{seed}")
            )
            base = b if base is None else base.join(b, on=["player_id", "week"])
        t26 = flag(meta.filter(pl.col("season") == 2026), h).join(base, on=["player_id", "week"])
        t26 = t26.join(src, on=["sleeper_id", "week"], how="inner")
        t26 = t26.filter(pl.max_horizontal("sleeper", "espn", "fantasypros") >= 5.0).drop_nulls(
            ["sleeper", "espn"]
        )
        # Noise floor: the same model, different seed.
        for seed in SEEDS_2026:
            t26 = t26.with_columns(
                ((pl.col("sleeper") + pl.col("espn") + pl.col(f"b{seed}")) / 3).alias(f"c3b{seed}")
            )
        aa = [abs(scored(t26, f"c3b{i}")[0] - scored(t26, f"c3b{j}")[0])
              for i, j in ((0, 1), (0, 2), (1, 2))]  # fmt: skip
        aam = [abs(scored(t26, f"b{i}")[0] - scored(t26, f"b{j}")[0])
               for i, j in ((0, 1), (0, 2), (1, 2))]  # fmt: skip
        out_hz: dict = {"n_2026": t26.height, "noise_c3": max(aa), "noise_model": max(aam),
                        "n_hit": int(t26["hit"].sum()), "n_mate": int(t26["hit_mate"].sum()),
                        "n_ol": int(t26["hit_ol"].sum()), "sources": {}}  # fmt: skip
        for s in ("sleeper", "espn"):
            out_hz["sources"][s] = scored(t26, s)
        w8 = t26.filter(pl.col("week") == 8).drop_nulls("fantasypros")
        out_hz["sources"]["fantasypros_w8"] = scored(w8, "fantasypros")
        out_hz["n_w8"] = w8.height
        out_hz["n_w8_hit"] = int(w8["hit"].sum())
        out_hz["variants"] = {}
        for name in VARIANTS:
            ms, c3, c4 = [], [], []
            sub = {k: [] for k in ("hit", "hit_mate", "hit_ol")}
            sub_c4 = []
            weeks = []
            last = None
            for seed in SEEDS_2026:
                p = preds(out, 2026, hz, name, seed).select(
                    "player_id", pl.col("week").cast(pl.Int64), "model"
                )
                t = t26.join(p, on=["player_id", "week"]).with_columns(
                    ((pl.col("sleeper") + pl.col("espn") + pl.col("model")) / 3).alias("c3"),
                    ((pl.col("sleeper") + pl.col("espn") + pl.col("fantasypros")
                      + pl.col("model")) / 4).alias("c4"),
                )  # fmt: skip
                ms.append(scored(t, "model"))
                c3.append(scored(t, "c3"))
                c4.append(scored(t.filter(pl.col("week") == 8).drop_nulls("fantasypros"), "c4"))
                sub_c4.append(
                    scored(
                        t.filter((pl.col("week") == 8) & pl.col("hit")).drop_nulls("fantasypros"),
                        "c4",
                    )
                )
                for k in sub:
                    s = t.filter(pl.col(k))
                    sub[k].append((*scored(s, "model"), *scored(s, "c3")))
                weeks.append(
                    {w: scored(t.filter(pl.col("week") == w), "c3")[0] for w in range(1, 9)}
                )  # noqa: E501
                last = t
            out_hz["variants"][name] = {
                "model": np.mean(ms, 0).tolist(),
                "c3": np.mean(c3, 0).tolist(),
                "c4_w8": np.mean(c4, 0).tolist(),
                "sub": {k: np.mean(v, 0).tolist() for k, v in sub.items()},
                "sub_c4_w8": np.mean(sub_c4, 0).tolist(),
                "weekly_c3": {w: float(np.mean([x[w] for x in weeks])) for w in range(1, 9)},
            }
            if name != "base":
                # Seed 2 row carries the last model; compare with the same seed's base.
                tb = last.with_columns(
                    ((pl.col("sleeper") + pl.col("espn") + pl.col("b2")) / 3).alias("c3b")
                )
                out_hz["variants"][name]["ci_c3"] = boot(tb, "c3", "c3b")
                out_hz["variants"][name]["ci_model"] = boot(tb, "model", "b2")
                hs = tb.filter(pl.col("hit"))
                out_hz["variants"][name]["ci_c3_hit"] = boot(hs, "c3", "c3b") if hs.height else None  # noqa: E501
        # Long sample: our model only, 2023-2025, scored where the baseline projects >= 5.
        lb = (
            pl.concat([preds(out, s, hz, "base", 0) for s in LONG_SEASONS])
            .with_columns(pl.col("week").cast(pl.Int64))
            .rename({"model": "b"})
        )
        tl = (
            flag(meta.filter(pl.col("season").is_in(LONG_SEASONS)), h)
            .join(lb, on=["player_id", "season", "week"])
            .filter(pl.col("b") >= 5.0)
            .with_columns(pl.col("team") + pl.col("season").cast(pl.Utf8))
        )
        long_out = {
            "n": tl.height,
            "n_hit": int(tl["hit"].sum()),
            "n_mate": int(tl["hit_mate"].sum()),
            "n_ol": int(tl["hit_ol"].sum()),
            "variants": {},
        }  # noqa: E501
        for name in VARIANTS:
            p = pl.concat([preds(out, s, hz, name, 0) for s in LONG_SEASONS]).with_columns(
                pl.col("week").cast(pl.Int64)
            )
            t = tl.join(p, on=["player_id", "season", "week"])
            v = {"all": scored(t, "model"), "hit": scored(t.filter(pl.col("hit")), "model"),
                 "mate": scored(t.filter(pl.col("hit_mate")), "model"),
                 "ol": scored(t.filter(pl.col("hit_ol")), "model")}  # fmt: skip
            if name != "base":
                v["ci"] = boot(t, "model", "b")
                v["ci_hit"] = boot(t.filter(pl.col("hit")), "model", "b")
                v["by_season"] = {
                    str(s): scored(t.filter(pl.col("season") == s), "model")[0]
                    - scored(t.filter(pl.col("season") == s), "b")[0]
                    for s in LONG_SEASONS
                }
            long_out["variants"][name] = v
        out_hz["long"] = long_out
        # Team-first evidence: does the player model miss at the team level when usage is open?
        t = tl.drop("hit", "hit_mate", "hit_ol")
        all_long = flag(meta.filter(pl.col("season").is_in(LONG_SEASONS)), h).join(
            lb, on=["player_id", "season", "week"]
        )
        res_p = all_long.with_columns(
            (pl.col("actual") - pl.col("b")).alias("res"),
            (pl.col(f"open_tgt_{h}").fill_null(0) + pl.col(f"open_car_{h}").fill_null(0)).alias(
                "open"
            ),  # noqa: E501
            pl.col(f"qb_out_{h}").fill_null(0).alias("qb"),
            pl.col(f"ol_out_{h}").fill_null(0).alias("ol"),
        )
        bins = []
        tw = res_p.group_by("season", "week", "team").agg(
            pl.col("open").median().alias("open"), pl.col("qb").max().alias("qb"),
            pl.col("ol").max().alias("ol"), pl.col("res").sum().alias("res"),
            pl.col("b").sum().alias("proj"), pl.col("actual").sum().alias("act"),
        )  # fmt: skip
        for lab, cond in (
            ("open <4", pl.col("open") < 4), ("open 4-10", pl.col("open").is_between(4, 10)),
            ("open >10", pl.col("open") > 10), ("QB out", pl.col("qb") >= 0.5),
            ("OL >=20% out", pl.col("ol") >= 0.2),
        ):  # fmt: skip
            d = tw.filter(cond)
            pl_rows = res_p.join(
                d.select("season", "week", "team"), on=["season", "week", "team"]
            ).filter(pl.col("b") >= 5)  # noqa: E501
            bins.append({
                "bin": lab, "team_weeks": d.height, "team_res": float(d["res"].mean()),
                "team_sd": float(d["res"].std()), "proj": float(d["proj"].mean()),
                "player_res": float(pl_rows["res"].mean()), "n_players": pl_rows.height,
            })  # fmt: skip
        out_hz["team"] = bins
        rp = res_p.filter(pl.col("b") >= 5)
        out_hz["resid_corr_open"] = float(np.corrcoef(rp["open"], rp["res"])[0, 1])
        out_hz["resid_r2_open_qb_ol"] = _r2(rp, ["open", "qb", "ol"])
        res[hz] = out_hz
    print(json.dumps(res, indent=1))


def _r2(d, cols):
    x = np.column_stack([np.ones(d.height), *[d[c].to_numpy() for c in cols]])
    y = d["res"].to_numpy()
    beta, *_ = np.linalg.lstsq(x, y, rcond=None)
    return float(1 - ((y - x @ beta) ** 2).sum() / ((y - y.mean()) ** 2).sum())


if __name__ == "__main__":
    {"run": run, "report": report}[sys.argv[1]](Path(sys.argv[2]))
