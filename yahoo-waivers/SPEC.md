# Yahoo guillotine waiver CLI: spec

Status: **approved 2026-11-17** with the decisions in section 9; implemented.

A local command-line tool that recommends waiver claims for my Yahoo Fantasy Football guillotine
leagues, within the Yahoo Fantasy Sports API personal-use agreement. Yahoo data is fetched,
used in memory, and discarded when the command exits.

## 1. Requirements

### Compliance (hard)
| # | Requirement | How it is met (section) |
|---|---|---|
| C1 | Read-only API calls; OAuth 2.0; credentials from environment / gitignored `.env`; secrets never committed or logged | 5.1, 5.6, 7 |
| C2 | Only my own leagues and teams; league IDs from config | 5.2 |
| C3 | No persistence of Yahoo data (no database, CSV, pickle, parquet, response cache, or logged bodies); memory only, discarded on exit | 5.3 |
| C4 | No model trained, fitted or tuned on Yahoo data; scoring uses public non-Yahoo data plus fixed rules | 4.3 |
| C5 | Fetch only: league settings, my roster, my FAAB, recent league transactions, top N available players per position | 4.1 |
| C6 | Throttling; exponential backoff with jitter and a retry cap on 429 / 999 / other rate-limit responses | 5.4 |
| C7 | No Yahoo data sent to any external service, LLM APIs included | 5.5 |
| C8 | No AGPL; every dependency and its license listed in README (relaxed from permissive-only, see Q5) | 6 |
| C9 | Terminal output with the footer "Fantasy data provided by Yahoo Fantasy"; optional `--save` writes one file to `recs/`; each run clears `recs/` first and deletes anything older than 24 hours; `purge` empties `recs/` | 5.3, 4.4 |

### Functional
For each configured league:
- Load scoring settings, roster slots, FAAB budget and waiver type.
- Find players recently released from chopped teams, from league transactions.
- Score available players on positional need (given my roster), bye weeks, injury status (public
  sources), recent usage from nflverse (snap share, target share, carries, red-zone usage), and
  next week's matchup.
- Print the top 5 adds, each with a suggested drop, a priority order, a FAAB bid range and a
  one-line rationale.

### CLI
```
python -m waivers run [--league ID] [--save]
python -m waivers purge
python -m waivers auth          # one-time OAuth consent; see 5.1 and Q2
```

### Rules for Claude Code (the assistant building this)
- Never runs the tool against the live Yahoo API, and never reads its output or anything in
  `recs/`. The owner runs it.
- Tests use only synthetic fixtures (fake players and stats); every Yahoo call is mocked.
- Enforced, not just promised: the owner adds `recs/` and `.env` to Claude Code's sandbox
  read-deny list and a `Read` deny rule in `.claude/settings.json` (exact lines in README).

## 2. Where it lives
A separate project, `yahoo-waivers/`, with its own `pyproject.toml` and lockfile. It does not
import from or write to `fantasy-model` (that project keeps a DuckDB database, which C3 forbids
for Yahoo data). Its dependency set stays small enough to audit for C8.

The existing Yahoo code in `fantasy-model` (`yahoo.py`, the `yahoo-*` commands, Yahoo steps in the
scheduled jobs) stores responses in the database and would break C3 if enabled. It has never
been connected. Plan: remove it from `fantasy-model` in a separate change once this is approved
(Q1).

## 3. Architecture

```
yahoo-waivers/
  pyproject.toml          dependencies pinned in uv.lock; license check in tests
  config.example.toml     league keys, my team keys, N per position, throttle settings
  .env.example            variable names only
  src/waivers/
    __main__.py           argparse: run, purge, auth
    config.py             load config + environment; validates league/team keys
    yahoo_client.py       OAuth refresh, GET-only client, allowlisted endpoints, throttle/backoff
    yahoo_parse.py        JSON -> plain dataclasses (League, Roster, Player, Transaction)
    public_data.py        nflverse downloads + local cache (public data only)
    scoring.py            fixed-rule scoring, drops, FAAB ranges, rationales
    output.py             terminal table, footer, optional --save
    recs.py               recs/ cleanup and purge
  tests/
    fixtures/             synthetic Yahoo JSON and synthetic nflverse tables
    test_*.py
```

## 4. Data flow

### 4.1 Yahoo (in memory only)
All calls are `GET` to `https://fantasysports.yahooapis.com/fantasy/v2/` with `format=json`. The
client only builds these resources, for league keys in config:

| Need | Resource |
|---|---|
| Settings: scoring, roster slots, FAAB budget, waiver type | `league/{league_key}/settings` |
| My roster | `team/{my_team_key}/roster` |
| My FAAB balance | `team/{my_team_key}` (FAAB balance field) |
| Recent transactions | `league/{league_key}/transactions;types=add,drop;count=N` |
| Top N available per position | `league/{league_key}/players;status=A;position={pos};sort=AR;count=N` |

N defaults to 25 per position (QB, RB, WR, TE, K, DEF as the league uses), set in config. No
`stats` sub-resource is requested for players, so complete league stats are never pulled (C5).

### 4.2 Public non-Yahoo data (may be cached)
Downloaded directly from the nflverse GitHub releases (public), cached under
`~/.cache/yahoo-waivers/` with a refresh age:
- Weekly player stats (targets, carries, red-zone usage from play-by-play-derived fields)
- Snap counts
- Injury reports
- Schedules (byes, next opponent)
- Player ID map (`ff_playerids`), which links Yahoo player IDs to nflverse IDs

These are public files, not Yahoo data, so caching them is allowed (C3, C4).

### 4.3 Scoring (fixed rules, no fitted model)
Weights are constants in `scoring.py`, chosen by hand and documented. They are never fitted or
tuned on Yahoo data, and no Yahoo data is used to evaluate them.

For each available player in the fetched pool:
- **Usage trend:** recent snap share, target share, carries and red-zone touches (last 3 games
  vs the season), from nflverse.
- **Positional need:** how much the player would raise my starting lineup over my current
  starter or flex at his position, using the league's roster slots and a projection from the
  usage signals.
- **Bye week:** penalty if his bye is next week; bonus if he covers a bye on my roster.
- **Injury:** discount from Yahoo's injury tag (in memory, fresher), falling back to the latest
  public injury report.
- **Matchup:** next opponent's points allowed to the position, from nflverse.
- **Released by a chopped team:** flagged, because these are usually the best players available
  that week.

Outputs, per league:
- **Top 5 adds**, ranked by score; the order is the claim priority.
- **Suggested drop:** the player on my roster whose loss costs the least, never a starter unless
  every bench player is worth more.
- **FAAB bid range:** a fixed rule on my own FAAB balance and the player's score tier, scaled by
  how far into the season it is (later chops need more of the remaining budget). Other teams' FAAB
  balances are not fetched (C5).
- **Rationale:** one line built from the strongest one or two signals.

### 4.4 Output
Printed to the terminal, followed by `Fantasy data provided by Yahoo Fantasy`. With `--save`, the
same text is written to one file, `recs/recs-<league>.txt`. At the start of every run (before
any Yahoo call) and on `purge`, the tool deletes everything in `recs/`; files older than 24 hours
are covered by that too. `recs/` is gitignored.

## 5. Compliance controls

### 5.1 Authentication (C1)
- `YAHOO_CLIENT_ID`, `YAHOO_CLIENT_SECRET` and `YAHOO_REFRESH_TOKEN` come from the environment or
  a gitignored `.env`.
- `python -m waivers auth` runs the one-time consent flow and writes the refresh token into `.env`
  (owner-only, `chmod 600`); if Yahoo issues a new one on refresh, it replaces the old line. It's a
  credential, not Yahoo data (Q2), and is never printed or logged. The tool warns if `.env` is
  readable by others (§3.d).
- Access tokens live in memory only and are refreshed each run.

### 5.2 Scope (C2)
- League keys and my team keys are required in config.
- The client refuses any resource for another league or team, checked against the config before
  every request (an allowlist, not a filter on results).

### 5.3 No persistence (C3)
- No database, file cache, pickle or HTTP cache library for Yahoo responses; responses are parsed
  into dataclasses and dropped when the process ends.
- The only file the tool writes is the optional `recs/` file.
- A test runs a full mocked `run` in a temporary directory and fails if any file appears outside
  `recs/` and the public-data cache.

### 5.4 Rate limiting (C6)
- At most 1 request per second by default, configurable.
- On HTTP 429, Yahoo's 999 status, or 5xx: exponential backoff (1s base, x2) with full jitter,
  up to 5 retries, honoring `Retry-After` when present, then a clear error naming the endpoint
  but not the body.

### 5.5 No external sharing (C7)
- The only network destinations are Yahoo's OAuth and API hosts and the nflverse GitHub releases.
- Nothing is sent to analytics, error reporting or LLM services.
- A test checks every outbound URL in a mocked run against that host list.

### 5.6 Logging (C1, C3)
- Logs only counts, endpoint names, status codes and timings.
- Never logs response bodies, tokens or player data.
- Exceptions from the client carry no response body.
- A test triggers every error path with a fixture body containing a marker string and fails if
  the marker reaches stdout, stderr or logs.

## 6. Dependencies (C8)
`requests` and `nflreadpy`, with their dependencies, plus `polars` and `python-dotenv`. Two
transitive packages are MPL-2.0 (`certifi`, `tqdm`), allowed per Q5. README lists every package,
version and license; a test fails if anything in the installed tree is AGPL.

## 7. Repository hygiene
`.gitignore` covers `.env`, `*.token`, `recs/`, the public-data cache, and `__pycache__`. Secret
scanning (gitleaks) runs before each commit, as in `fantasy-model`.

## 8. Testing
- Synthetic fixtures only: invented names, teams and stats in Yahoo's JSON shapes and nflverse's
  table shapes.
- Coverage: parsing, the endpoint allowlist, throttling and backoff (with a fake clock), scoring
  rules, drop choice, FAAB ranges, chopped-team detection, output footer, `recs/` cleanup and
  `purge`, and the persistence, network-destination, logging and license tests above.
- No test touches the network.

## 9. Decisions (answered 2026-11-17)
- **Q1.** Remove the old Yahoo code from `fantasy-model` and delete any Yahoo data in its database:
  done. The only rows were three failed 2026-10-31 login attempts (HTTP 403, no body).
- **Q2.** Refresh token in `.env`: allowed (a credential), gitignored and `chmod 600`.
- **Q3.** The Sleeper bid model may inform FAAB ranges (not Yahoo data, not trained on it). Not
  used yet: the ranges are fixed rules; a later change could add it.
- **Q4.** Yahoo's injury tag may be used in memory: used, ahead of nflverse reports.
- **Q5.** MPL-2.0 is fine for a personal, undistributed tool: uses `requests` and `nflreadpy`; the
  license test checks for AGPL only.
- **Q6.** On demand only. Scheduling is a later option if deadlines get missed.
