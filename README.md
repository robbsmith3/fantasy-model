# Chop Desk

[![CI](https://github.com/robbsmith3/fantasy-model/actions/workflows/ci.yml/badge.svg)](https://github.com/robbsmith3/fantasy-model/actions/workflows/ci.yml)

**A FAAB bidding and survival model for guillotine fantasy football leagues.**

In a guillotine league the lowest-scoring team each week is eliminated ("chopped") and its whole
roster drops onto waivers, where everyone left bids from a single season-long FAAB budget. The
question each week isn't "who scores the most points" but "how much should I bid to stay off the
chopping block, and is this player worth more now or is my budget worth more later?"

Chop Desk collects league data on a schedule, projects every player, simulates the rest of the
season, models how much each rival will bid, and turns it into a claim plan, a lineup and live
elimination odds, served as a local web app.

```
   Sleeper · ESPN · FantasyPros · nflverse · Yahoo*
                        │  scheduled snapshots
                        ▼
              DuckDB (raw responses + snapshots)
                        │
   projections ─▶ lineup value ─▶ season simulation ─▶ opponent bid model
                                                              │
     claim plan · survival lineup · live chop odds · weekly report card  (web app + CLI)
```
<sub>*Yahoo support is built but waits on Yahoo's API approval.</sub>

## Features

- **Waiver claim plan:** for each available player, the bid that best trades a higher chance of
  winning him against the FAAB it costs you later. Claims are planned together (distinct drops,
  total within budget) and each one explains itself ("adds 8.7 pts over 4 weeks, keeps him from
  the rival most likely to land him, market ~$115").
- **Season simulation:** 20,000 simulated seasons of weekly scores, team-strength drift and
  rivals spending their FAAB on each chopped roster give every team's chance of being chopped
  each week and of winning the league.
- **Opponent bid model:** trained on every past claim in your league, *including losing bids*,
  with each team's own bidding tendencies and **roster need**: how much the player would improve
  that team's starting lineup, flex slots included.
- **Projections:** an equal-weight consensus of Sleeper, ESPN, FantasyPros and our own LightGBM
  model built on nflverse usage, expected points and betting lines, scored with your league's
  exact rules.
- **Survival lineup:** starts the players that maximize your chance of *not being the lowest
  score*, which is not always the max-points lineup.
- **Live chop odds:** on game days, live scores plus a simulated finish for every unplayed
  minute, drawn as a "chop line".
- **Weekly report card:** grades every waiver run against what the model predicted beforehand
  and refits its corrections automatically.
- **Web app:** overview, waivers, live, lineup, league, players and model pages; search
  (Ctrl/⌘+K), player detail panels, one-click "copy claims", dark/light themes, installable.

## Quick start

**Requirements:** Windows with WSL2 (Ubuntu), [uv](https://docs.astral.sh/uv/), Node 20.12+
(e.g. via [nvm](https://github.com/nvm-sh/nvm)) and a Sleeper league with FAAB waivers.

```bash
git clone https://github.com/robbsmith3/fantasy-model.git && cd fantasy-model
uv sync                                              # Python deps + the `ffm` CLI
cp config/leagues.example.toml config/leagues.toml   # then add your Sleeper ids (below)
```

In `config/leagues.toml` set your numeric Sleeper user id (from
`https://api.sleeper.app/v1/user/<username>`) and your league id (from the league URL). Then
load history and this season so far:

```bash
uv run ffm idmap                                     # cross-platform player ids
uv run ffm history 2018 2019 2020 2021 2022 2023 2024 2025 2026
uv run ffm run pre_waiver                            # rosters, FAAB, projections
uv run ffm run backfill                              # past scores, bids, projections
```

Build and start the web app:

```bash
cd web && npm install && npm run build && cd ..
uv run ffm serve                                     # http://127.0.0.1:8000
```

### Scheduling

Data jobs run from Windows Task Scheduler (cron inside WSL misses runs while the VM is asleep).
From Windows PowerShell:

```powershell
powershell -ExecutionPolicy Bypass -File \\wsl.localhost\Ubuntu-24.04\home\<you>\code\fantasy-model\scripts\register_tasks.ps1
```

| Job | When (local) | What it saves |
|---|---|---|
| `daily` | 9am Mon–Wed, Fri–Sat | injury status, practice reports, depth charts |
| `pre_waiver` | Tue 8pm | rosters, FAAB, scores, projections (Sleeper, ESPN, FantasyPros, our model) |
| `post_waiver` | Wed 9am | winning *and losing* bids, then the weekly report card |
| `pre_kickoff` | Thu 6:30pm, Sun 11:30am | final projections, player status, betting props |
| `history` | Tue 10am | current-season nflverse stats, snaps, schedules |

Set `pre_waiver` and `post_waiver` to bracket your league's waiver run.

## Weekly workflow

| When | What to do |
|---|---|
| Sunday | Watch **Live**: the chop line updates every minute during games. |
| Tuesday night | Open **Waivers** after the 8pm job; review the plan; **Copy claims** into Sleeper. |
| Before the cutoff | Claims lock at the waiver run's hour (shown on the site with a countdown). |
| Wednesday | Results post a few minutes after the cutoff; the 9am job grades the run (**Model** page). |
| Before kickoff | Check **Lineup** for any swap that makes you safer. |

## Commands

```bash
uv run ffm recommend [--assume-chop TEAM_ID] [--csv PATH]   # bid sheet + claim plan
uv run ffm lineup [--week N]                                 # survival vs max-points lineup
uv run ffm live                                              # in-game chop odds
uv run ffm review                                            # weekly report card
uv run ffm serve                                             # web app
uv run ffm health                                            # recent jobs and failures
uv run ffm backup                                            # zip data + config, verify
uv run ffm injury-curves                                     # where injury discounts come from
uv run ffm --help                                            # everything else
```

## How accurate is it?

Backtested on the 2026 Sleeper league (18 teams), always predicting each week using only data
from before it. Reproduce with the commands shown.

| Component | Result | Command |
|---|---|---|
| Projections | consensus misses by 5.38 pts per player-week (Sleeper alone: 5.42) | `ffm backtest-model 2 3 4 5 6 7` |
| Team-score ranges | 80% ranges contain the actual score 80% of the time | `ffm validate-sim 1 2 3 4 5 6 7` |
| Chop prediction | modestly better than chance (log score −2.52 vs −2.67) | `ffm validate-sim ...` |
| Winning-bid prices | actual price inside the 80% range 76% of the time; prices run ~$27 above the median prediction | `ffm review` |
| Naming the auction winner | right 15% of the time (in the top 3: 45%) | `ffm review` |

Week-to-week fantasy scoring is mostly noise, so the gains are in the margins. The weakest piece
is predicting *which* rival wins a given player.

## Project structure

```
src/fantasy_model/
  sleeper.py espn.py fantasypros.py yahoo.py nflverse.py   data adapters
  schema.py db.py                                          DuckDB schema, raw landing, upserts
  value.py projection_model.py                             consensus projections + own model
  fit.py lineup.py                                         lineups, pickup value, survival lineup
  survival.py player_sim.py dynamics.py                    season simulation, outcomes, rival pickups
  bids.py recommend.py                                     opponent bid model, claim plan
  live.py review.py evaluate.py                            live odds, report card, backtests
  api.py cli.py                                            web API (`ffm serve`), CLI (`ffm`)
web/                                                       React + TypeScript + Vite frontend
scripts/register_tasks.ps1                                 Windows Task Scheduler jobs
config/                                                    league config (example), 1Password refs
docs/DEVLOG.md                                             developer log: decisions and evidence
```

Design rules: raw API responses are stored before parsing (a parser bug never loses data);
snapshots are append-only; projections are stored as stat lines and scored per league; every
backtest only uses data from before the week it predicts.

## Data sources

- **[Sleeper API](https://docs.sleeper.com/):** leagues, rosters, transactions, live scores.
  The projections endpoint is unofficial.
- **ESPN:** public, unofficial fantasy endpoints (projections) and scoreboard (game clocks).
- **[nflverse](https://nflverse.nflverse.com/):** player stats, snap counts, schedules and betting
  lines, injury reports, expected fantasy points (ffopportunity), and FantasyPros consensus
  rankings and projections.
- **[The Odds API](https://the-odds-api.com/):** NFL player props (yards, receptions, TDs),
  saved before kickoff; free plan. Being collected for testing, not yet in the consensus. Needs a
  key in `~/.config/fantasy-model/odds-api.key` (or `$ODDS_API_KEY`).
- **Yahoo Fantasy:** awaiting API access. Screens showing Yahoo data will carry "Fantasy data
  provided by Yahoo Fantasy" as Yahoo requires.

This is a personal, non-commercial project. Unofficial endpoints can change without notice;
respect each provider's terms.

## Development

CI (GitHub Actions) runs on every push and pull request: lint, format, secret scan and tests for
Python; type check, build and `npm audit` for the web app.

```bash
uv run pytest                     # Python tests (all network calls mocked)
uv run pre-commit run --all-files # ruff lint + format + gitleaks secret scan
cd web && npm run dev             # frontend with hot reload (proxies /api to `ffm serve`)
```

Secrets stay out of the repo: Yahoo credentials are 1Password references in
`config/yahoo.env`, and the OAuth token lives in `~/.config/fantasy-model/` (chmod 600).
Decisions, experiments and dead ends are recorded in [docs/DEVLOG.md](docs/DEVLOG.md).

## Security

- `ffm serve` listens on `127.0.0.1` only and has no login. Keep it that way: don't bind it to
  `0.0.0.0` or forward its port, or anyone on the network could read your league data.
- No credentials live in this repo. League ids are in the git-ignored `config/leagues.toml`;
  Yahoo's client id and secret are 1Password references (`config/yahoo.env`); the OAuth token and
  the Odds API key are stored outside the repo with owner-only permissions.
- Collected data (`data/`) and logs (`logs/`) are git-ignored.
- The server only answers requests addressed to `127.0.0.1`/`localhost` (a guard against
  DNS rebinding, where a hostile web page points its own domain at your machine).
- Every commit is scanned for secrets (gitleaks, in pre-commit and CI).

**Monitoring.** A failed job or backup pops up a Windows notification right away, and the web app
shows a banner when any job failed or hasn't run on schedule (e.g. the PC was off).

**Backups.** Much of the database can't be re-downloaded (FAAB, waiver pools and projections at
bid time). After each Wednesday `post_waiver` job, `data/` and your config are zipped to the
folder set in `[backup] dir` (point it at OneDrive or another synced folder), the copy is
verified by opening it, and the newest 8 are kept.

## License

[MIT](LICENSE)
