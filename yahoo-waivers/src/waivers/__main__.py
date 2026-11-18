"""python -m waivers run [--league ID] [--save] | purge | auth"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import time
import traceback
from pathlib import Path

from waivers import output, public_data, recs, scoring
from waivers.config import ConfigError, load_credentials, load_settings, save_refresh_token
from waivers.yahoo_client import POSITIONS, YahooClient, YahooError, authorize_url, exchange_code
from waivers.yahoo_parse import (
    parse_faab,
    parse_players,
    parse_roster,
    parse_settings,
    parse_transactions,
)

log = logging.getLogger("waivers")


def home() -> Path:
    return Path(os.environ.get("WAIVERS_HOME", Path.cwd()))


def _positions(slots: list[str]) -> list[str]:
    wanted = set()
    for s in slots:
        wanted |= scoring.FLEX.get(s, {s})
    return [p for p in POSITIONS if p in wanted]


def run_league(client: YahooClient, lg, data, per_position: int, budget: int) -> str:
    key = lg.league_key
    info = parse_settings(client.league_settings(key))
    faab = parse_faab(client.team(key))
    faab = faab if faab is not None else (info.faab_budget or budget)
    roster = parse_roster(client.roster(key))
    moves = parse_transactions(client.transactions(key))
    pool = []
    for pos in _positions(info.slots):
        pool += parse_players(client.available(key, pos, per_position))
    week = info.current_week
    sigs = public_data.signals(data, info.scoring, week)
    byes = public_data.byes(data.schedule)
    opps = {
        week + k: public_data.opponents(data.schedule, week + k) for k in range(scoring.HORIZON)
    }
    matchup = public_data.matchup_factors(data, info.scoring, week)
    chopped = scoring.chopped_releases(moves, int(time.time()))
    picks = scoring.recommend(info, roster, pool, faab, sigs, byes, opps, matchup, chopped)
    notes = [] if info.uses_faab else ["this league doesn't use FAAB; bids shown as $0"]
    log.info("%s: %d rostered, %d available scored", lg.name, len(roster), len(pool))
    return output.league_block(f"{lg.name} ({info.name})", week, faab, picks, notes)


def cmd_run(args, client_factory=None, data_loader=None) -> int:
    root = home()
    recs.clean_at_start(root / "recs")  # before any Yahoo call
    settings = load_settings(root / "config.toml")
    leagues = [lg for lg in settings.leagues
               if args.league in (None, lg.league_id, lg.name)]  # fmt: skip
    if not leagues:
        raise ConfigError(f"no configured league matches {args.league!r}")
    env = root / ".env"
    if client_factory:
        client = client_factory(settings)
    else:
        creds = load_credentials(env)
        client = YahooClient(creds, settings.leagues, settings.requests_per_second,
                             settings.max_retries,
                             on_new_refresh_token=lambda t: save_refresh_token(env, t))  # fmt: skip
    if data_loader is None:
        import nflreadpy

        season = nflreadpy.get_current_season()
        data = public_data.load(season)
    else:
        data = data_loader()
    blocks = []
    for lg in leagues:
        try:
            blocks.append(run_league(client, lg, data, settings.players_per_position,
                                     settings.faab_budget))  # fmt: skip
        except YahooError as e:  # message has the endpoint and status only, never a body
            blocks.append(f"{lg.name}\ncouldn't load: {e}")
        except (
            Exception
        ) as e:  # parsing etc.: the message could quote Yahoo values, so leave it out
            blocks.append(f"{lg.name}\ncouldn't process: {_where(e)}")
    text = output.render(blocks)
    print(text, end="")
    if args.save:
        path = recs.save(root / "recs", text)
        print(f"saved to {path.relative_to(root)} (deleted at the start of the next run)")
    return 0


def _where(e: Exception) -> str:
    """Error type and our code location, without the message (which may contain Yahoo data)."""
    frames = [f for f in traceback.extract_tb(e.__traceback__) if "/waivers/" in f.filename]
    f = frames[-1] if frames else None
    loc = f" at {Path(f.filename).name}:{f.lineno} ({f.name})" if f else ""
    return f"{type(e).__name__}{loc}"


def cmd_purge(args) -> int:
    print(f"deleted {recs.purge(home() / 'recs')} file(s) from recs/")
    return 0


def cmd_auth(args) -> int:
    env = home() / ".env"
    creds = load_credentials(env)
    print("1. Open this URL, sign in to Yahoo and approve the app:")
    print("   " + authorize_url(creds.client_id, args.redirect))
    code = input("2. Paste the code Yahoo shows you: ").strip()
    save_refresh_token(env, exchange_code(creds, code, args.redirect))
    print(f"Saved the refresh token to {env.name} (owner-only). You won't need to do this again.")
    return 0


def main(argv=None, client_factory=None, data_loader=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m waivers")
    ap.add_argument("-v", "--verbose", action="store_true", help="log request counts and timings")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="print waiver recommendations")
    r.add_argument("--league", help="league_id or name from config.toml (default: all)")
    r.add_argument("--save", action="store_true", help="also write one file to recs/")
    sub.add_parser("purge", help="delete everything in recs/ now")
    a = sub.add_parser("auth", help="one-time Yahoo sign-in; saves the refresh token to .env")
    a.add_argument("--redirect", default="oob", help="redirect URI registered for your app")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING,
                        format="%(levelname)s %(message)s")  # fmt: skip
    try:
        if args.cmd == "run":
            return cmd_run(args, client_factory, data_loader)
        if args.cmd == "purge":
            return cmd_purge(args)
        return cmd_auth(args)
    except (ConfigError, YahooError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
