"""Our own weekly PPR projection from open nflverse data (usage, expected points, Vegas lines).

Rows are (candidate player, target week): anyone who played in the previous LOOKBACK weeks of
games. Target = actual PPR, 0 if he didn't play, so the model also learns "will he play" (the
Princeton 2026 thesis found much of an ML edge over ESPN comes from that). Every feature uses
only games strictly before the target week; Vegas lines for the target game are pre-game info.
"""

from __future__ import annotations

import numpy as np
import polars as pl

from fantasy_model.value import frame

POSITIONS = ("QB", "RB", "WR", "TE")
LOOKBACK = 4  # weeks; candidate pool for a target week
REG_WEEKS = 18
TRAIN_SEASONS = 9  # 2018-2026 when forecasting 2026

USAGE = {
    "ppr": "fantasy_points_ppr",
    "xfp": "total_fantasy_points_exp",
    "tgt": "targets",
    "car": "carries",
    "tsh": "target_share",
    "aysh": "air_yards_share",
    "wopr": "wopr",
    "patt": "attempts",
}
FEATURES = [
    *[f"{k}_{w}" for k in USAGE for w in ("l1", "l3", "l8")],
    "ppr_ewm",
    "xfp_ewm",
    "games",
    "games_season",
    "weeks_since",
    "prior_ppg",
    "pos_code",
    "implied",
    "spread",
    "home",
    "opp_allowed",
    "week",
]
# Official injury report for the target week. Filed Wed-Fri, so only usable for in-week
# projections; at Tuesday waivers next week's report doesn't exist yet (use FEATURES alone).
INJURY_FEATURES = ["inj_report", "inj_practice"]
REPORT_CODE = {"Questionable": 1, "Doubtful": 2, "Out": 3}
PRACTICE_CODE = {
    "Full Participation in Practice": 0,
    "Limited Participation in Practice": 1,
    "Did Not Participate In Practice": 2,
}

# Candidate context features, under test (scripts/experiments/context_features.py). Not used in
# production. Each group: (Tuesday features, in-week features); only in-week ones see the target
# week's injury report. Added by context_features(), never by build_rows().
CANDIDATE_GROUPS = {
    "teammates": (
        ["open_tgt_t", "open_car_t", "open_pos_t", "qb_out_t"],
        ["open_tgt_w", "open_car_w", "open_pos_w", "qb_out_w"],
    ),
    "oline": (["ol_out_t"], ["ol_out_w"]),
    "def_health": (
        ["dl_out_t", "lb_out_t", "db_out_t"],
        ["dl_out_w", "lb_out_w", "db_out_w"],
    ),
    "def_quality": (["def_adj"], ["def_adj"]),
    "career": (
        ["career_ppg", "career_games", "career_blend", "age"],
        ["career_ppg", "career_games", "career_blend", "age"],
    ),
}
CONTEXT_WINDOW = 8  # team games (this season) that define a player's usual role
REPORT_OUT = {"Out": 1.0, "Doubtful": 1.0, "Questionable": 0.15}  # expected share of game missed
SNAP_GROUP = {"T": "ol", "G": "ol", "C": "ol", "OL": "ol", "DE": "dl", "DT": "dl", "NT": "dl",
              "DL": "dl", "LB": "lb", "OLB": "lb", "ILB": "lb", "MLB": "lb", "CB": "db", "S": "db",
              "FS": "db", "SS": "db", "DB": "db"}  # fmt: skip
DEF_SHRINK = 4  # games of "average" a defense's adjusted rating is shrunk toward
CAREER_WEIGHTS = (5, 4, 3)  # last three seasons, newest first (Marcel-style)
CAREER_BLEND = 6  # games at which this season counts as much as the career baseline


def _gw(season, week):
    return season * 20 + week  # global week index; gaps across seasons are harmless


def player_games(con, seasons: list[int]) -> pl.DataFrame:
    """Regular-season games actually played, with usage and expected points."""
    return frame(
        con,
        """SELECT s.player_id, s.position, s.season, s.week::INT AS week, s.team,
                  s.opponent_team, s.fantasy_points_ppr, s.targets, s.carries, s.target_share,
                  s.air_yards_share, s.wopr, s.attempts, o.total_fantasy_points_exp
           FROM nfl_player_stats s
           LEFT JOIN nfl_ff_opportunity o
             ON o.player_id = s.player_id AND o.season = s.season AND o.week = s.week
           WHERE s.season_type = 'REG' AND list_contains(?, s.season)
             AND list_contains(?, s.position)""",
        [seasons, list(POSITIONS)],
    ).with_columns((pl.col("season") * 20 + pl.col("week")).alias("gw"))


def team_games(con, seasons: list[int]) -> pl.DataFrame:
    """One row per (team, season, week) with opponent, home flag, implied total and spread."""
    sched = frame(
        con,
        """SELECT season, week::INT AS week, home_team, away_team, total_line, spread_line
           FROM nfl_schedules WHERE game_type = 'REG' AND list_contains(?, season)""",
        [seasons],
    )
    # nflverse spread_line > 0 means the home team is favored by that many points.
    home = sched.select(
        "season", "week", pl.col("home_team").alias("team"), pl.col("away_team").alias("opp"),
        pl.lit(1).alias("home"),
        ((pl.col("total_line") + pl.col("spread_line")) / 2).alias("implied"),
        pl.col("spread_line").alias("spread"),
    )  # fmt: skip
    away = sched.select(
        "season", "week", pl.col("away_team").alias("team"), pl.col("home_team").alias("opp"),
        pl.lit(0).alias("home"),
        ((pl.col("total_line") - pl.col("spread_line")) / 2).alias("implied"),
        (-pl.col("spread_line")).alias("spread"),
    )  # fmt: skip
    return pl.concat([home, away]).with_columns(
        (pl.col("season") * 20 + pl.col("week")).alias("gw")
    )


def build_rows(con, seasons: list[int], as_of: tuple[int, int] | None = None) -> pl.DataFrame:
    """Candidate (player, target week) rows with features and target (`actual`).

    as_of=(season, week): only games before that week are known (drops a partly played week),
    and target weeks at or after it are featurized as of then, for forward projections."""
    games = player_games(con, seasons).sort("player_id", "gw")
    as_of_gw = _gw(*as_of) if as_of else None
    if as_of_gw is not None:
        games = games.filter(pl.col("gw") < as_of_gw)
    tg = team_games(con, seasons)
    # Lines are only posted ~1 week out; later weeks get the team's season-average line so far.
    tg = tg.with_columns(
        pl.col(c).fill_null(pl.col(c).mean().over("team", "season")) for c in ("implied", "spread")
    )

    # Points each defense allowed to each position per game, as of before each week.
    allowed = (
        games.group_by("opponent_team", "position", "season", "week", "gw")
        .agg(pl.col("fantasy_points_ppr").sum().alias("pts"))
        .sort("gw")
    )

    weeks = sorted({(s, w) for s, w in tg.select("season", "week").unique().iter_rows()})
    prior_ppg = (
        games.group_by("player_id", "season")
        .agg(pl.col("fantasy_points_ppr").mean().alias("ppg"))
        .with_columns((pl.col("season") + 1).alias("season"))
    )
    out = []
    for season, week in weeks:
        if week > REG_WEEKS:
            continue
        gw = _gw(season, week)
        hist = games.filter(pl.col("gw") < gw)
        ref = min(gw, as_of_gw) if as_of_gw is not None else gw  # "now" for future targets
        ref_week = ref - season * 20
        # Early weeks look back into last season; skip the unused gw slots between seasons.
        lo = ref - LOOKBACK - (20 - REG_WEEKS if ref_week <= LOOKBACK else 0)
        recent = hist.filter(pl.col("gw") >= lo)
        cand = recent.group_by("player_id").agg(pl.col("position").last(), pl.col("team").last())
        if cand.is_empty():
            continue
        h = hist.join(cand.select("player_id"), on="player_id")
        feats = h.group_by("player_id").agg(
            *[
                pl.col(src).tail(n).mean().alias(f"{k}_{tag}")
                for k, src in USAGE.items()
                for tag, n in (("l1", 1), ("l3", 3), ("l8", 8))
            ],
            pl.col("fantasy_points_ppr").ewm_mean(half_life=4).last().alias("ppr_ewm"),
            pl.col("total_fantasy_points_exp").ewm_mean(half_life=4).last().alias("xfp_ewm"),
            pl.len().alias("games"),
            (pl.col("season") == season).sum().alias("games_season"),
            (ref - pl.col("gw").max()).alias("weeks_since"),
        )
        rows = (
            cand.join(feats, on="player_id")
            .join(
                tg.filter((pl.col("season") == season) & (pl.col("week") == week)).select(
                    "team", "opp", "home", "implied", "spread"
                ),
                on="team",
                how="inner",  # no game (bye) -> no row
            )
            .with_columns(pl.lit(season).alias("season"), pl.lit(week).alias("week"))
        )
        opp = (
            allowed.filter(pl.col("gw") < gw)
            .group_by("opponent_team", "position")
            .agg(pl.col("pts").tail(8).mean().alias("opp_allowed"))
        )
        rows = rows.join(
            opp, left_on=["opp", "position"], right_on=["opponent_team", "position"], how="left"
        )
        out.append(rows)
    rows = pl.concat(out, how="diagonal_relaxed")
    actual = games.select(
        "player_id", "season", "week", pl.col("fantasy_points_ppr").alias("actual")
    )
    inj = frame(
        con,
        """SELECT gsis_id AS player_id, season::INT AS season, week::INT AS week,
                  report_status, practice_status
           FROM nfl_injuries WHERE game_type = 'REG' AND list_contains(?, season)""",
        [seasons],
    ).unique(["player_id", "season", "week"])
    inj = inj.select(
        "player_id",
        "season",
        "week",
        pl.col("report_status").replace_strict(REPORT_CODE, default=0).alias("inj_report"),
        pl.col("practice_status").replace_strict(PRACTICE_CODE, default=0).alias("inj_practice"),
    )
    return (
        rows.join(prior_ppg.rename({"ppg": "prior_ppg"}), on=["player_id", "season"], how="left")
        .join(inj, on=["player_id", "season", "week"], how="left")
        .with_columns(pl.col("inj_report").fill_null(0), pl.col("inj_practice").fill_null(0))
        .join(actual, on=["player_id", "season", "week"], how="left")
        .with_columns(pl.col("actual").is_not_null().alias("played"))
        .with_columns(
            pl.col("actual").fill_null(0.0),
            pl.col("position")
            .replace_strict({p: i for i, p in enumerate(POSITIONS)})
            .alias("pos_code"),
        )
    )


def feature_set(groups: list[str], in_week: bool) -> list[str]:
    """FEATURES (plus the player's own injury report in-week) plus the candidate groups."""
    extra = [f for g in groups for f in CANDIDATE_GROUPS[g][in_week]]
    return [*FEATURES, *(INJURY_FEATURES if in_week else []), *extra]


def _norm_name(col: str) -> pl.Expr:
    return (
        pl.col(col).str.to_lowercase().str.replace(r" (jr|sr|ii|iii|iv|v)\.?$", "")
        .str.replace_all(r"[^a-z ]", "")
    )  # fmt: skip


def _usual(by_week: dict, week: int) -> tuple[dict, set]:
    """({key: value per team game}, keys in the last game) over the window before `week`."""
    wks = sorted(w for w in by_week if w < week)[-CONTEXT_WINDOW:]
    usual: dict = {}
    for w in wks:
        for k, v in by_week[w].items():
            usual[k] = usual.get(k, 0.0) + v / len(wks)
    return usual, set(by_week[wks[-1]]) if wks else set()


def _absences(usual: dict, last: set, report: dict) -> tuple[dict, dict]:
    """Share of the next game each usual player is expected to miss: Tuesday (missed the last
    game) and in-week (the report if he's on it, else Tuesday: long-term absences leave it)."""
    tue = {k: 0.0 if k in last else 1.0 for k in usual}
    return tue, {k: report[k] if k in report else tue[k] for k in usual}


def _reports(con, seasons: list[int]) -> pl.DataFrame:
    return (
        frame(
            con,
            """SELECT season::INT AS season, week::INT AS week, team, gsis_id, full_name,
                      report_status
               FROM nfl_injuries WHERE game_type = 'REG' AND list_contains(?, season)""",
            [seasons],
        )
        .with_columns(
            _norm_name("full_name").alias("name"),
            pl.col("report_status").replace_strict(REPORT_OUT, default=0.0, return_dtype=pl.Float64)
            .alias("out"),
        )
    )  # fmt: skip


def _teammate_features(rows: pl.DataFrame, games: pl.DataFrame, reports: pl.DataFrame) -> list:
    """Per row: usage left open by absent teammates (his own usage excluded) and whether the
    team's usual QB is expected out, for both horizons."""
    usage: dict = {}
    pos_of: dict = {}
    for s, w, t, p, pos, tg, ca, at in games.select(
        "season", "week", "team", "player_id", "position", "targets", "carries", "attempts"
    ).fill_null(0).iter_rows():  # fmt: skip
        d = usage.setdefault((s, t), {})
        for k, v in (("tgt", tg), ("car", ca), ("att", at)):
            d.setdefault(k, {}).setdefault(w, {})[p] = float(v)
        pos_of[p] = pos
    rep: dict = {}
    for s, w, t, p, o in reports.select("season", "week", "team", "gsis_id", "out").iter_rows():
        rep.setdefault((s, w, t), {})[p] = o

    cache: dict = {}

    def team_ctx(s, w, t):
        key = (s, w, t)
        if key in cache:
            return cache[key]
        u = usage.get((s, t))
        ctx = None
        if u and any(x < w for x in u["tgt"]):
            tgt, last = _usual(u["tgt"], w)
            car, _ = _usual(u["car"], w)
            att, _ = _usual(u["att"], w)
            active = [p for p in tgt if tgt[p] + car[p] + att[p] > 0]
            tue, wk = _absences(dict.fromkeys(active), last, rep.get(key, {}))
            qbs = [p for p in active if att[p] > 0]
            qb = max(qbs, key=att.get) if qbs else None
            ctx = {
                "t": tue, "w": wk, "tgt": tgt, "car": car,
                "qb_t": float(qb is not None and tue[qb] >= 0.5),
                "qb_w": float(qb is not None and wk[qb] >= 0.5),
            }  # fmt: skip
        cache[key] = ctx
        return ctx

    out: list = []
    for s, w, t, p, pos in rows.select("season", "week", "team", "player_id", "position").iter_rows():  # fmt: skip # noqa: E501
        c = team_ctx(s, w, t)
        row = {}
        for h in ("t", "w"):
            if c is None:
                row |= {f"{n}_{h}": None for n in ("open_tgt", "open_car", "open_pos", "qb_out")}
                continue
            ab = {k: v for k, v in c[h].items() if v > 0 and k != p}
            row[f"open_tgt_{h}"] = sum(v * c["tgt"][k] for k, v in ab.items())
            row[f"open_car_{h}"] = sum(v * c["car"][k] for k, v in ab.items())
            row[f"open_pos_{h}"] = sum(
                v * (c["tgt"][k] + c["car"][k]) for k, v in ab.items() if pos_of.get(k) == pos
            )
            row[f"qb_out_{h}"] = c[f"qb_{h}"]
        out.append(row)
    return out


def _snap_health(con, seasons: list[int], reports: pl.DataFrame) -> pl.DataFrame:
    """(season, week, team): share of each group's usual snaps expected missing, both horizons.
    Snap counts have no player ids, so reports are matched on team and normalized name."""
    snaps = frame(
        con,
        """SELECT season, week::INT AS week, team, player, position, offense_pct, defense_pct
           FROM nfl_snap_counts WHERE game_type = 'REG' AND list_contains(?, season)""",
        [seasons],
    ).with_columns(_norm_name("player").alias("name"))
    by: dict = {}
    for s, w, t, n, pos, o, d in snaps.select(
        "season", "week", "team", "name", "position", "offense_pct", "defense_pct"
    ).iter_rows():  # fmt: skip
        g = SNAP_GROUP.get(pos)
        pct = (o if g == "ol" else d) if g else None
        if pct:
            by.setdefault((s, t, g), {}).setdefault(w, {})[n] = pct
    rep: dict = {}
    for s, w, t, n, o in reports.select("season", "week", "team", "name", "out").iter_rows():
        rep.setdefault((s, w, t), {})[n] = o
    teams = {(s, t) for s, t, _ in by}
    rows = []
    for s, t in sorted(teams):
        weeks = range(1, REG_WEEKS + 2)
        for w in weeks:
            row = {"season": s, "week": w, "team": t}
            for g in ("ol", "dl", "lb", "db"):
                usual, last = _usual(by.get((s, t, g), {}), w)
                total = sum(usual.values())
                tue, wk = _absences(usual, last, rep.get((s, w, t), {}))
                row[f"{g}_out_t"] = sum(usual[k] * tue[k] for k in usual) / total if total else None
                row[f"{g}_out_w"] = sum(usual[k] * wk[k] for k in usual) / total if total else None
            rows.append(row)
    return pl.DataFrame(rows, infer_schema_length=None)


def _defense_quality(games: pl.DataFrame) -> pl.DataFrame:
    """(season, week, opp, position) -> def_adj: points a defense gives up to the position beyond
    what its opponents usually score, from this season's earlier games and shrunk toward 0 by
    DEF_SHRINK games of average. Offense and defense effects are fit together, so a soft schedule
    doesn't flatter a defense. Positive = generous."""
    pts = (
        games.group_by("season", "week", "team", "opponent_team", "position")
        .agg(pl.col("fantasy_points_ppr").sum().alias("pts"))
    )  # fmt: skip
    out = []
    for (season, pos), d in pts.group_by("season", "position"):
        for w in range(2, REG_WEEKS + 2):
            h = d.filter(pl.col("week") < w)
            if h.is_empty():
                continue
            y = h["pts"].to_numpy()
            off, dfn = h["team"].to_list(), h["opponent_team"].to_list()
            mu = float(y.mean())
            o_eff: dict = {}
            d_eff: dict = {}
            for _ in range(5):
                for eff, keys, other, okeys in (
                    (o_eff, off, d_eff, dfn),
                    (d_eff, dfn, o_eff, off),
                ):
                    sums: dict = {}
                    ns: dict = {}
                    for k, ok, v in zip(keys, okeys, y, strict=True):
                        sums[k] = sums.get(k, 0.0) + v - mu - other.get(ok, 0.0)
                        ns[k] = ns.get(k, 0) + 1
                    eff.clear()
                    eff.update({k: sums[k] / (ns[k] + DEF_SHRINK) for k in sums})
            out += [
                {"season": season, "week": w, "opp": k, "position": pos, "def_adj": v}
                for k, v in d_eff.items()
            ]
    return pl.DataFrame(out)


def _birth_dates(con) -> dict[str, str]:
    """{gsis id: birth date} from the newest stored Sleeper players file."""
    import json

    found = con.execute(
        """SELECT body FROM raw_response WHERE endpoint ILIKE '%players%'
           ORDER BY fetched_at DESC LIMIT 1"""
    ).fetchall()
    if not found:
        return {}
    body = found[0][0]
    players = json.loads(body) if isinstance(body, str) else body
    g2s = con.execute(
        "SELECT sleeper_id, gsis_id FROM player_id_map WHERE gsis_id IS NOT NULL"
    ).fetchall()
    births = {g: players[s]["birth_date"] for s, g in g2s if players.get(s, {}).get("birth_date")}
    for p in players.values():
        if p.get("gsis_id") and p.get("birth_date"):
            births.setdefault(p["gsis_id"].strip(), p["birth_date"])
    return births


def _career(rows: pl.DataFrame, games: pl.DataFrame, births: dict[str, str]) -> list:
    """Weighted scoring over the last three seasons, its blend with this season so far, and age."""
    from datetime import date

    by_season = (
        games.group_by("player_id", "season")
        .agg(pl.col("fantasy_points_ppr").sum().alias("pts"), pl.len().alias("g"))
    )  # fmt: skip
    hist = {(p, s): (pts, g) for p, s, pts, g in by_season.iter_rows()}
    so_far: dict = {}
    for p, s, w, pts in games.select("player_id", "season", "week", "fantasy_points_ppr").sort(
        "week"
    ).iter_rows():  # fmt: skip
        so_far.setdefault((p, s), []).append((w, pts))
    out = []
    for p, s, w in rows.select("player_id", "season", "week").iter_rows():
        pts = games_n = 0.0
        played = 0
        for k, wt in enumerate(CAREER_WEIGHTS, start=1):
            h = hist.get((p, s - k))
            if h:
                pts += wt * h[0]
                games_n += wt * h[1]
                played += h[1]
        career = pts / games_n if games_n else None
        now = [x for wk, x in so_far.get((p, s), []) if wk < w]
        if career is None:
            blend = sum(now) / len(now) if now else None
        else:
            blend = (career * CAREER_BLEND + sum(now)) / (CAREER_BLEND + len(now))
        born = births.get(p)
        age = (date(s, 9, 1) - date.fromisoformat(born)).days / 365.25 if born else None
        out.append({"career_ppg": career, "career_games": played, "career_blend": blend, "age": age})  # fmt: skip # noqa: E501
    return out


def context_features(con, rows: pl.DataFrame) -> pl.DataFrame:
    """build_rows() output plus the CANDIDATE_GROUPS columns. Everything comes from games before
    the target week; only the *_w columns read the target week's injury report."""
    seasons = sorted(rows["season"].unique().to_list())
    games = player_games(con, seasons)
    reports = _reports(con, seasons)
    teammates = pl.DataFrame(_teammate_features(rows, games, reports), infer_schema_length=None)
    career = pl.DataFrame(_career(rows, games, _birth_dates(con)), infer_schema_length=None)
    health = _snap_health(con, seasons, reports)
    ol = health.select("season", "week", "team", "ol_out_t", "ol_out_w")
    dfn = health.select(
        "season", "week", pl.col("team").alias("opp"),
        *[f"{g}_out_{h}" for g in ("dl", "lb", "db") for h in ("t", "w")],
    )  # fmt: skip
    return (
        pl.concat([rows, teammates, career], how="horizontal")
        .join(ol, on=["season", "week", "team"], how="left")
        .join(dfn, on=["season", "week", "opp"], how="left")
        .join(_defense_quality(games), on=["season", "week", "opp", "position"], how="left")
    )


def fit(train: pl.DataFrame, features: list[str] = FEATURES, seed: int = 0):
    import lightgbm as lgb

    model = lgb.LGBMRegressor(
        n_estimators=400, learning_rate=0.03, num_leaves=31, min_child_samples=50,
        subsample=0.8, subsample_freq=1, colsample_bytree=0.8, reg_lambda=1.0,
        random_state=seed, verbose=-1,
    )  # fmt: skip
    model.fit(train.select(features).to_numpy(), train["actual"].to_numpy())
    model.features_ = features
    return model


def predict(model, rows: pl.DataFrame) -> np.ndarray:
    return np.clip(model.predict(rows.select(model.features_).to_numpy()), 0, None)


def walk_forward(
    rows: pl.DataFrame, season: int, weeks: list[int], features: list[str] = FEATURES
) -> pl.DataFrame:
    """For each target week, train on everything strictly earlier, predict that week."""
    preds = []
    for w in weeks:
        key = pl.col("season") * 20 + pl.col("week")
        train = rows.filter(key < season * 20 + w)
        test = rows.filter((pl.col("season") == season) & (pl.col("week") == w))
        m = fit(train, features)
        preds.append(test.select("player_id", "season", "week").with_columns(
            pl.Series("points", predict(m, test))
        ))  # fmt: skip
    return pl.concat(preds)


def complete_through(con, season: int) -> int:
    """First week of `season` with an unfinished game (features/training stop before it)."""
    wk = con.execute(
        """SELECT min(week) FROM nfl_schedules
           WHERE season = ? AND game_type = 'REG' AND result IS NULL""",
        [season],
    ).fetchall()[0][0]
    return int(wk) if wk is not None else REG_WEEKS + 1


def snapshot_projections(con, snapshot_id: str, season: int, weeks: list[int]) -> int:
    """Train on every completed week, project `weeks`, store as source 'model' (Sleeper ids)."""
    from fantasy_model.db import upsert

    as_of = (season, complete_through(con, season))
    rows = build_rows(con, list(range(season - (TRAIN_SEASONS - 1), season + 1)), as_of)
    key = pl.col("season") * 20 + pl.col("week")
    model = fit(rows.filter(key < _gw(*as_of)))
    target = rows.filter((pl.col("season") == season) & pl.col("week").is_in(weeks))
    g2s = dict(
        con.execute(
            "SELECT gsis_id, sleeper_id FROM player_id_map"
            " WHERE gsis_id IS NOT NULL AND sleeper_id IS NOT NULL"
        ).fetchall()
    )
    out = []
    for r, pts in zip(target.iter_rows(named=True), predict(model, target), strict=True):
        sid = g2s.get(r["player_id"])
        if sid:
            out.append(
                {
                    "snapshot_id": snapshot_id,
                    "source": "model",
                    "season": season,
                    "week": r["week"],
                    "source_player_id": sid,
                    "position": r["position"],
                    "nfl_team": r["team"],
                    "stats": {"pts": round(float(pts), 2)},
                }
            )
    return upsert(con, "projection_snapshot", out)
