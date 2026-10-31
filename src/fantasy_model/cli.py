"""CLI.  `uv run ffm <command>`

Snapshot jobs (idempotent, safe to re-run):
  run daily        Sleeper player status/injuries/depth (Mon-Wed, Fri-Sat)
  run pre_waiver   rosters + FAAB per team, last 2 weeks' scores, projections wk..wk+N
  run post_waiver  transactions + winning bids, post-claim rosters/budgets
  run pre_kickoff  final projections + player status (Thu, Sun; replaces `daily` those days)

Setup / utilities:
  yahoo-auth [--redirect URI]   one-time OAuth (run under `op run`)
  yahoo-leagues                 list your Yahoo league keys
  yahoo-probe <league_key>      dump one page of transactions; confirms faab_bid exists
  idmap                         refresh cross-platform player ID map (nflverse)
  history <season> [...]        load nflverse weekly stats/snaps/schedules
  health                        recent runs, failures, unmapped players
"""

from __future__ import annotations

import argparse
import json
import sys
import traceback

from fantasy_model import config, db, nflverse, sleeper, yahoo

JOBS = ("daily", "pre_waiver", "post_waiver", "pre_kickoff")


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
        for w in range(week, min(week + weeks_ahead, 18) + 1):
            step(f"proj_w{w}", sleeper.snapshot_projections, con, snapshot_id, season, w)

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

    elif job == "pre_kickoff":
        step(f"proj_w{week}", sleeper.snapshot_projections, con, snapshot_id, season, week)
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
    sub.add_parser("history").add_argument("seasons", nargs="+", type=int)
    sub.add_parser("health")
    a = ap.parse_args(argv)

    if a.cmd == "run":
        sys.exit(0 if run_job(a.job) else 1)
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
        print(nflverse.load_history(con, a.seasons))
    elif a.cmd == "health":
        health(con)
    con.close()


if __name__ == "__main__":
    main()
