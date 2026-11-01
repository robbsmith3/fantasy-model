"""CLI.  `uv run ffm <command>`

Snapshot jobs (idempotent, safe to re-run):
  run daily        Sleeper player status/injuries/depth (Mon-Wed, Fri-Sat)
  run pre_waiver   rosters + FAAB per team, last 2 weeks' scores, projections wk..wk+N
  run post_waiver  transactions + winning bids, post-claim rosters/budgets
  run pre_kickoff  final projections + player status (Thu, Sun; replaces `daily` those days)
  run backfill     Sleeper scores + transactions (incl. bids), Sleeper + ESPN projections
                   for every week so far; one-off, safe to re-run

Setup / utilities:
  yahoo-auth [--redirect URI]   one-time OAuth (run under `op run`)
  yahoo-leagues                 list your Yahoo league keys
  yahoo-probe <league_key>      dump one page of transactions; confirms faab_bid exists
  idmap                         refresh cross-platform player ID map (nflverse)
  history [<season> ...]        load nflverse weekly stats/snaps/schedules (default: current)
  health                        recent runs, failures, unmapped players
  eval-projections WEEK [WEEK ...]
                                accuracy of each projection source + consensus vs actual
  backtest-model WEEK [WEEK ...]  walk-forward accuracy of our model vs sources
  validate-sim WEEK [WEEK ...]  score past chop predictions: player-level vs team-level
  recommend [--assume-chop TEAM_ID] [--csv PATH]
  serve [--host H] [--port P]   local web UI + API (http://127.0.0.1:8000)
  lineup [--week N] [--team ID] survival lineup: max P(not lowest) vs max points
  live                          live chop odds from in-game scores (Sundays)
                                bid sheet for the next Sleeper waiver run
"""

from __future__ import annotations

import argparse
import json
import sys
import traceback

import polars as pl

from fantasy_model import (
    config,
    db,
    espn,
    fantasypros,
    nflverse,
    projection_model,
    sleeper,
    yahoo,
)

JOBS = ("daily", "pre_waiver", "post_waiver", "pre_kickoff", "backfill")


def run_job(job: str, cfg: dict | None = None, con=None) -> bool:
    cfg = cfg or config.load_config()
    own_con = con is None
    con = con or db.connect()
    started = db.now_utc()
    snapshot_id = f"{started:%Y-%m-%dT%H:%MZ}_{job}"
    state = sleeper.nfl_state(con, snapshot_id)
    season, week = int(state["season"]), int(state["week"])
    con.execute(
        "INSERT OR REPLACE INTO snapshot_run VALUES (?,?,?,?,?,NULL,NULL,NULL)",
        [snapshot_id, job, season, week, started],
    )

    me = cfg["me"].get("sleeper_user_id") or None
    weeks_ahead = int(cfg["projections"].get("weeks_ahead", 4))
    recent = [w for w in (week - 1, week) if w >= 1]  # rolling window self-heals misses
    log, errors = [], []

    def step(label: str, fn, *args):
        """Isolate failures: one league/endpoint breaking doesn't sink the run."""
        try:
            res = fn(*args)
            log.append(f"{label}={res}" if res is not None else label)
        except Exception:
            errors.append(f"[{label}] {traceback.format_exc(limit=3)}")

    if job == "daily":
        step("players", sleeper.snapshot_players, con, snapshot_id)

    elif job == "pre_waiver":
        for lg in config.leagues(cfg, "sleeper"):
            lid = lg["league_id"]
            step(
                f"sl:{lg.get('nickname', lid)}:league",
                sleeper.sync_league,
                con,
                snapshot_id,
                lid,
                me,
            )
            for w in recent:
                step(
                    f"sl:{lid}:scores_w{w}",
                    sleeper.sync_week_scores,
                    con,
                    snapshot_id,
                    lid,
                    season,
                    w,
                )
        for lg in config.leagues(cfg, "yahoo"):
            lk = lg["league_id"]
            step(f"yh:{lg.get('nickname', lk)}:league", yahoo.sync_league, con, snapshot_id, lk)
            for w in recent:
                step(
                    f"yh:{lk}:scores_w{w}", yahoo.sync_week_scores, con, snapshot_id, lk, season, w
                )
        ahead = list(range(week, min(week + weeks_ahead, 18) + 1))
        for w in ahead:
            step(f"proj_w{w}", sleeper.snapshot_projections, con, snapshot_id, season, w)
        step("espn_proj", espn.snapshot_projections, con, snapshot_id, season, ahead)
        step("model_proj", projection_model.snapshot_projections, con, snapshot_id, season, ahead)
        step("fp_proj", fantasypros.snapshot_projections, con, snapshot_id, season)

    elif job == "post_waiver":
        for lg in config.leagues(cfg, "sleeper"):
            lid = lg["league_id"]
            for leg in recent:
                step(
                    f"sl:{lid}:txns_leg{leg}",
                    sleeper.sync_transactions,
                    con,
                    snapshot_id,
                    lid,
                    season,
                    leg,
                )
            step(f"sl:{lid}:league", sleeper.sync_league, con, snapshot_id, lid, me)
        for lg in config.leagues(cfg, "yahoo"):
            lk = lg["league_id"]
            step(f"yh:{lk}:txns", yahoo.sync_transactions, con, snapshot_id, lk, season)
            step(f"yh:{lk}:league", yahoo.sync_league, con, snapshot_id, lk)

    elif job == "backfill":
        # Sleeper serves past weeks' scores and transactions, so these can be recovered.
        for lg in config.leagues(cfg, "sleeper"):
            lid = lg["league_id"]
            for w in range(1, week + 1):
                step(
                    f"sl:{lid}:scores_w{w}",
                    sleeper.sync_week_scores,
                    con,
                    snapshot_id,
                    lid,
                    season,
                    w,
                )
                step(
                    f"sl:{lid}:txns_leg{w}",
                    sleeper.sync_transactions,
                    con,
                    snapshot_id,
                    lid,
                    season,
                    w,
                )
        # Past weeks' projections, for backtests. ESPN serves every week in one call.
        past = list(range(1, week + 1))
        step("espn_proj_past", espn.snapshot_projections, con, snapshot_id, season, past)
        for w in past:
            step(f"proj_w{w}", sleeper.snapshot_projections, con, snapshot_id, season, w)

    elif job == "pre_kickoff":
        step(f"proj_w{week}", sleeper.snapshot_projections, con, snapshot_id, season, week)
        step("espn_proj", espn.snapshot_projections, con, snapshot_id, season, [week])
        step("fp_proj", fantasypros.snapshot_projections, con, snapshot_id, season)
        step("players", sleeper.snapshot_players, con, snapshot_id)

    else:
        raise SystemExit(f"unknown job {job!r}; choose from {JOBS}")

    ok = not errors
    con.execute(
        "UPDATE snapshot_run SET finished_at=?, ok=?, error=? WHERE snapshot_id=?",
        [db.now_utc(), ok, "\n".join(errors)[-4000:] or None, snapshot_id],
    )
    print(snapshot_id, "OK" if ok else "PARTIAL", " ".join(log))
    for e in errors:
        print(e, file=sys.stderr)
    if own_con:
        con.close()
    return ok


def health(con) -> None:
    q = lambda sql: con.execute(sql).fetchall()  # noqa: E731
    print("Last 10 runs:")
    for r in q(
        "SELECT snapshot_id, ok, finished_at IS NOT NULL FROM snapshot_run "
        "ORDER BY snapshot_id DESC LIMIT 10"
    ):
        print("  ", r)
    print(
        "Failed/unfinished:",
        q("SELECT count(*) FROM snapshot_run WHERE NOT coalesce(ok, FALSE)")[0][0],
    )
    print("ID map rows:", q("SELECT count(*) FROM player_id_map")[0][0])
    print(
        "Unmapped Yahoo rostered players (latest snapshot):",
        q("""
        SELECT count(*) FROM roster_snapshot_canon
        WHERE platform='yahoo' AND sleeper_id IS NULL
          AND snapshot_id = (SELECT max(snapshot_id) FROM roster_snapshot WHERE platform='yahoo')
    """)[0][0],
    )
    print("Bids captured:", q("SELECT platform, count(*), count(bid) FROM txn GROUP BY 1"))
    print(
        "Teams alive (latest):",
        q("""
        SELECT platform, league_id, count(*) FILTER (WHERE NOT eliminated), count(*)
        FROM team_state_snapshot t
        WHERE snapshot_id = (SELECT max(snapshot_id) FROM team_state_snapshot s
                             WHERE s.league_id = t.league_id)
        GROUP BY 1, 2
    """),
    )


def print_eval(con, weeks: list[int]) -> None:
    from fantasy_model import evaluate

    lg = config.leagues(config.load_config(), "sleeper")[0]
    season = con.execute("SELECT max(season) FROM snapshot_run").fetchall()[0][0]
    table = evaluate.projection_table(con, "sleeper", lg["league_id"], season, weeks)
    cols = [c for c in ("sleeper", "espn", "fantasypros", "consensus") if c in table.columns]
    common = [c for c in cols if table[c].null_count() < table.height]
    acc = evaluate.accuracy(table, [c for c in common if table[c].null_count() == 0] or common)
    print(
        f"weeks {weeks}: {table.height} relevant player-weeks (any source >= "
        f"{evaluate.RELEVANT:g} pts); rows where every listed source projects the player"
    )
    print(acc)


def print_recommendation(con, assume_chop: str | None, csv: str | None) -> None:
    from fantasy_model import recommend

    cfg = config.load_config()
    lg = config.leagues(cfg, "sleeper")[0]
    ctx = recommend.load_context(con, "sleeper", lg["league_id"], assume_chop)
    out = recommend.recommend(con, ctx, assume_chop)
    s, sheet = out["summary"], out["sheet"]
    print(
        f"{s['team']}  |  FAAB ${s['faab']}  |  week {s['week']}"
        + (f"  |  PREVIEW: {s['assume_chop']} chopped" if s["assume_chop"] else "")
    )
    print(
        f"P(win league) {s['p_win']:.1%}   "
        f"P(chopped {'this' if s['chop_pending'] else 'next'} week) {s['p_chop_next']:.1%}   "
        f"$100 of FAAB ~ {s['lambda_per_100']:.2%} win prob "
        f"(saved $ worth {s['faab_discount']:.0%} of spent $)"
    )
    if sheet.is_empty():
        print("No bid worth placing: no available player improves your lineup enough.")
        return
    print(
        f"\n{'#':>2} {'player':24} {'pos':4} {'+pts':>5} {'+win':>6} {'block':>6} {'BID':>5} "
        f"{'P(get)':>6} {'mkt med':>7}  drop"
    )
    for i, r in enumerate(sheet.head(15).iter_rows(named=True), 1):
        inj = f" ({r['injury']})" if r["injury"] else ""
        print(
            f"{i:2} {(r['player'] + inj)[:24]:24} {r['pos']:4} {r['pts_gain']:5.1f} "
            f"{r['d_p_win']:6.2%} {r['deny']:6.2%} {r['bid']:5d} {r['p_win_claim']:6.0%} "
            f"{r['market_median']:7.0f}  {r['drop']}"
        )
    plan = out["plan"]
    if not plan.is_empty():
        print(
            f"\nCLAIM PLAN (joint: distinct drops, total <= ${s['faab']}, each claim valued "
            "given the ones above may win):"
        )
        for r in plan.iter_rows(named=True):
            print(
                f"  {r['order']}. {r['player'][:24]:24} {r['pos']:4} bid ${r['bid']:<4} "
                f"P(get) {r['p_get']:4.0%}  +win {r['d_p_win']:6.2%}  drop {r['drop']}"
            )
        print(f"  total if every claim wins: ${plan['bid'].sum()}")
    if csv:
        sheet.drop("curve").write_csv(csv)  # nested list column doesn't fit CSV
        print(f"wrote {csv}")


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(
        prog="ffm", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("run").add_argument("job", choices=JOBS)
    sub.add_parser("yahoo-auth").add_argument("--redirect", default="oob")
    sub.add_parser("yahoo-leagues")
    sub.add_parser("yahoo-probe").add_argument("league_key")
    sub.add_parser("idmap")
    sub.add_parser("history").add_argument("seasons", nargs="*", type=int)
    sub.add_parser("health")
    sub.add_parser("eval-projections").add_argument("weeks", nargs="+", type=int)
    sub.add_parser("backtest-model").add_argument("weeks", nargs="+", type=int)
    sub.add_parser("validate-sim").add_argument("weeks", nargs="+", type=int)
    sub.add_parser("live")
    lu = sub.add_parser("lineup")
    lu.add_argument("--week", type=int)
    lu.add_argument("--team")
    srv = sub.add_parser("serve")
    srv.add_argument("--host", default="127.0.0.1")
    srv.add_argument("--port", type=int, default=8000)
    rec = sub.add_parser("recommend")
    rec.add_argument("--assume-chop", help="preview as if this team_id were eliminated")
    rec.add_argument("--csv", help="also write the sheet here")
    a = ap.parse_args(argv)

    if a.cmd == "run":
        sys.exit(0 if run_job(a.job) else 1)
    if a.cmd == "serve":
        from fantasy_model import api

        return api.serve(a.host, a.port)
    if a.cmd == "yahoo-auth":
        return yahoo.authorize(a.redirect)

    con = db.connect()
    sid = f"{db.now_utc():%Y-%m-%dT%H:%MZ}_{a.cmd}"
    if a.cmd == "yahoo-leagues":
        for lg in yahoo.my_leagues(con, sid):
            print(lg)
    elif a.cmd == "yahoo-probe":
        fc = yahoo._get(
            con, sid, "probe.transactions", f"league/{a.league_key}/transactions;types=add;count=5"
        )
        print(json.dumps(fc, indent=1)[:6000])
        print("\n>>> faab_bid present:", '"faab_bid"' in json.dumps(fc))
    elif a.cmd == "idmap":
        print("player_id_map rows:", nflverse.load_id_map(con))
    elif a.cmd == "history":
        import nflreadpy

        res = nflverse.load_history(con, a.seasons or [nflreadpy.get_current_season()])
        print(sid, res)
        if any(isinstance(v, str) for v in res.values()):
            con.close()
            sys.exit(1)  # nonzero so Task Scheduler shows the failure
    elif a.cmd == "eval-projections":
        print_eval(con, a.weeks)
    elif a.cmd == "backtest-model":
        from fantasy_model import evaluate

        lg = config.leagues(config.load_config(), "sleeper")[0]
        season = con.execute("SELECT max(season) FROM snapshot_run").fetchall()[0][0]
        acc = evaluate.model_backtest(con, "sleeper", lg["league_id"], season, a.weeks)
        print(acc.with_columns(pl.selectors.float().round(3)))
    elif a.cmd == "validate-sim":
        from fantasy_model import evaluate

        lg = config.leagues(config.load_config(), "sleeper")[0]
        season = con.execute("SELECT max(season) FROM snapshot_run").fetchall()[0][0]
        r = evaluate.sim_validation(con, "sleeper", lg["league_id"], season, a.weeks)
        print(r["weeks"].with_columns(pl.selectors.float().round(3)))
        print({k: round(v, 3) for k, v in r["summary"].items()})
    elif a.cmd == "live":
        from fantasy_model import live, recommend

        lg = config.leagues(config.load_config(), "sleeper")[0]
        r = live.live_odds(con, recommend.load_context(con, "sleeper", lg["league_id"]))
        g = r["games"]
        print(
            f"week {r['week']}  |  games: {g['final']} final, {g['live']} live, {g['pre']} to play"
        )
        for t in r["teams"]:
            me = " <- you" if t["is_me"] else ""
            print(
                f"  {t['name'][:26]:26} now {t['current']:6.1f}  final ~{t['final_mean']:6.1f} "
                f"[{t['final_p10']:5.1f}-{t['final_p90']:5.1f}]  P(chop) {t['p_chop']:6.1%}{me}"
            )
    elif a.cmd == "lineup":
        from fantasy_model import lineup, recommend

        lg = config.leagues(config.load_config(), "sleeper")[0]
        ctx = recommend.load_context(con, "sleeper", lg["league_id"])
        week = a.week or (ctx.week if ctx.chop_pending else ctx.week + 1)
        r = lineup.optimize(con, ctx, week, a.team)
        mp, sv = r["max_points"], r["survival"]
        print(
            f"{ctx.names[r['team']]}  |  week {week}  |  safety line (lowest rival) median "
            f"{r['line_median']}"
        )
        print(f"max-points lineup: P(not lowest) {mp['p_safe']:.1%}, projected {mp['mean']}")
        print(f"survival lineup:   P(not lowest) {sv['p_safe']:.1%}, projected {sv['mean']}")
        if not r["swaps"]:
            print("  no change: the max-points lineup is also the safest")
        for sw in r["swaps"]:
            print(f"  start {sw['in']}  instead of  {sw['out']}")
        for x in sv["starters"]:
            print(f"    {x['pos']:4} {x['name']:24} {x['mu']:5.1f}")
    elif a.cmd == "recommend":
        print_recommendation(con, a.assume_chop, a.csv)
    elif a.cmd == "health":
        health(con)
    con.close()


if __name__ == "__main__":
    main()
