# fantasy-model — Phase 1: data layer

Captures everything the Week B/C models need, across 1 Sleeper + 2 Yahoo guillotine leagues,
into one DuckDB file. **Priority is snapshotting what disappears** (budgets, pool, injuries,
projections at bid time) — that becomes the 2027 training set.

```
src/fantasy_model/
  schema.py    DuckDB tables + waiver_pool view (raw / entity / snapshot layers)
  db.py        connect, land_raw (payload stored before parsing), upsert
  sleeper.py   Sleeper adapter (no auth)
  yahoo.py     Yahoo adapter + OAuth2 (refresh token in ~/.config/fantasy-model, chmod 600)
  nflverse.py  player ID map (Sleeper<->Yahoo<->gsis) + weekly history
  cli.py       `ffm` entry point: snapshot jobs + setup utilities
config/        leagues.example.toml, yahoo.env (1Password op:// refs, no secrets)
scripts/       register_tasks.ps1 (Windows Task Scheduler)
```

## Step 1 — Drop into the project (WSL Ubuntu)
```bash
cd ~/code/fantasy-model          # unzip over the uv template; replaces pyproject.toml
uv sync                          # installs package + `ffm` script into .venv
printf "data/\nlogs/\nconfig/leagues.toml\n" >> .gitignore
cp config/leagues.example.toml config/leagues.toml
uv run pytest && uv run pre-commit run --all-files     # 7 tests, all mocked
```

## Step 2 — Sleeper (5 min)
```bash
curl -s https://api.sleeper.app/v1/user/<username> | python3 -m json.tool | grep user_id
```
Put `user_id` and the league id (from the league URL) in `config/leagues.toml`, then:
```bash
uv run ffm idmap                 # ~10k rows; required for Yahoo<->Sleeper joins
uv run ffm run daily             # smoke test #1
uv run ffm run pre_waiver        # Sleeper part should print league/scores/proj counts
```

## Step 3 — Yahoo OAuth (15 min, one time)
1. developer.yahoo.com/apps/create (logged into the account in the leagues):
   API permissions **Fantasy Sports → Read**; Redirect URI `oob` (if rejected, use
   `https://localhost:8080` and pass `--redirect https://localhost:8080` below).
2. In 1Password create item **"Yahoo Fantasy API"** with fields `client_id`, `client_secret`
   (vault path must match `config/yahoo.env`).
3. Authorize and discover league keys:
   ```bash
   op run --env-file=config/yahoo.env -- uv run ffm yahoo-auth
   uv run ffm yahoo-leagues         # copy the two 2026 league_keys into leagues.toml
   uv run ffm yahoo-probe <league_key>
   ```
4. **Gate:** probe must print `faab_bid present: True` (needs at least one waiver claim in
   the league). If False, the Yahoo bid labels don't exist and the opponent-bid model
   trains on Sleeper only — tell me and we'll adjust.

**Secret tradeoff:** `op run` needs 1Password unlocked, so unattended runs can't use it.
The client secret + refresh token are therefore copied to
`~/.config/fantasy-model/yahoo_token.json` (chmod 600, outside the repo, on your encrypted
disk). Revoke by deleting the Yahoo app. A 1Password service account would remove this,
but it's overkill here.

## Step 4 — Schedule (Windows Task Scheduler, not WSL cron)
WSL's VM shuts down when idle, so cron inside it silently misses runs. Task Scheduler
launches WSL on demand, can **wake the PC**, and **runs missed jobs at next boot**.
```powershell
# Windows PowerShell, from the repo via \\wsl$ path or a copy:
powershell -ExecutionPolicy Bypass -File \\wsl$\Ubuntu-24.04\home\<you>\code\fantasy-model\scripts\register_tasks.ps1
Start-ScheduledTask -TaskPath '\FantasyModel\' -TaskName daily     # test
```
| Task | When (local) | Captures | Why it can't be backfilled |
|---|---|---|---|
| `daily` | 9am Mon–Wed, Fri–Sat | injury status, practice, depth chart | Sleeper only serves current status |
| `pre_waiver` | Tue 8pm | rosters, FAAB left per team, last 2 wks scores, projections wk..wk+4 | pool + budgets at bid time = model inputs |
| `post_waiver` | Wed 9am | transactions + winning bids, post-claim rosters | labels + next week's starting state |
| `pre_kickoff` | Thu 6:30pm, Sun 11:30am | final projections + status | late projection moves = variance signal |
| `history` | Tue 10am | current-season nflverse stats/snaps/schedules | re-fetchable; kept fresh for Week B |

`daily` skips Thu/Sun because `pre_kickoff` pulls players then → stays inside Sleeper's
one-call-per-day on `/players/nfl`. **Check each league's waiver run time** and move
`pre_waiver`/`post_waiver` so they straddle it for *all three* leagues.

## Step 5 — History for Week B (optional now, needed next phase)
```bash
uv run ffm history 2018 2019 2020 2021 2022 2023 2024 2025
```
Loads weekly player stats, snap counts, schedules (spread/total → implied team totals).
Only the seasons passed are replaced; with no seasons, `uv run ffm history` refreshes the
current season (the weekly `history` task does this) and leaves earlier ones intact.

## Operating it
```bash
uv run ffm health                # last runs, failures, unmapped Yahoo players, bids captured
tail -f logs/snapshots.log
uv run python -c "import duckdb; print(duckdb.connect('data/guillotine.duckdb').sql('from waiver_pool limit 20'))"
```
A job exits non-zero and records `ok=false` + traceback in `snapshot_run.error` if any step
fails, but **other leagues/steps still run** (per-step isolation).

## Phase 2 — Bid recommendations (`ffm recommend`)
Run Tuesday night after `pre_waiver` (the chopped roster is in the pool by then):
```bash
uv run ffm recommend                     # bid sheet + joint claim plan for the next Sleeper run
uv run ffm recommend --assume-chop 12    # preview: as if team 12 were chopped this week
uv run ffm recommend --csv bids.csv      # also save the sheet
```
How a bid is chosen, module by module:

| Step | Module | What it does | Evidence (2026 Sleeper league) |
|---|---|---|---|
| Projections | `value`, `espn`, `fantasypros`, `projection_model` | Equal-weight mean of Sleeper, ESPN, FantasyPros (current week) and our LightGBM model on nflverse usage / expected points / Vegas lines | wks 2-7 MAE 5.38 vs Sleeper 5.42; fitted weights no better (`ffm backtest-model 2 3 4 5 6 7`) |
| Lineup fit | `fit` | Best legal lineup each week; points a pickup adds net of the cheapest drop; injury discounts | — |
| Survival | `survival` | Monte Carlo of the remaining chops: weekly score sd 22.7 + team-strength drift (injuries/roles) | Team scores calibrated: 80% intervals cover 80% (`ffm validate-sim 1 2 3 4 5 6 7`) |
| Opponent bids | `bids` | Per-opponent participation + share-of-FAAB model with shrunken team tendencies | CRPS 103 -> 97 vs league-wide model; ranges a bit narrow (67% vs 80%) |
| Decision | `recommend` | Bid maximizing P(get) x (win-prob gain - $ x price) + blocking value; joint plan with distinct drops within FAAB | — |

Price of a dollar: median win-prob-per-$ among contested players this week x a discount from
our own survival curve (a team near the chop spends now; a safe team keeps money's option value).

Keep these fresh: `ffm history` weekly (scheduled), and `ffm run backfill` after a missed week.

**Known model gaps**: rivals' future FAAB pickups aren't simulated (rosters fixed), so P(win) is
still optimistic for strong teams; injury discounts in `fit.INJURY_AVAIL` are hand-set; competition
and desperation features showed no gain on 33 auctions but are kept for re-testing.

## The UI (Chop Desk)
```bash
cd web && npm install && npm run build && cd ..   # once, and after frontend changes
uv run ffm serve                                     # http://127.0.0.1:8000
```
Pages: Overview (win odds, survival curve, claim-plan preview), Waivers (claim plan + full
sheet; preview any team's chop from the dropdown), Live (in-game chop odds, refreshes every
minute), Lineup (survival vs max-points lineup), League + team rosters, Players (searchable,
virtualized), Model (backtest report, recent jobs). Frontend dev with hot reload: run
`uv run ffm serve` and `cd web && npm run dev` (http://localhost:5173 proxies /api).

Handy: Ctrl/⌘+K (or /) searches players, teams and pages; any player name opens his detail
panel; Waivers → "Copy claims" puts the plan on your clipboard for entering in Sleeper; Live →
"Alert me if I'm in danger" sends a system notification while the app is open.

**Install as an app.** In Chrome/Edge on this PC, open http://127.0.0.1:8000 and use the install
icon in the address bar: it gets its own window, icon and offline shell (data is always live).
On a phone, browsers only install apps (and allow notifications) over HTTPS, so the phone needs
a secure route to this PC first, e.g. Tailscale (`tailscale serve`) or a Cloudflare tunnel.

The server precomputes league odds, players and the default bid sheet at startup and caches
everything per data snapshot. While it computes, scheduled jobs wait for the database lock
(up to 2 minutes) instead of failing.

## Design rules
1. **Raw first** — every response lands in `raw_response` before parsing; parser bugs are
   fixed by re-parsing, never by lost snapshots. Matters most for Yahoo (parser written
   against documented shape, not yet verified live) and Sleeper projections (unofficial).
2. **Ephemeral vs re-fetchable** — `*_snapshot` tables are append-only; history is upserted.
3. **Stats, not points** — projections store stat lines; each league re-scores later.
4. **Idempotent + rolling 2-week window** — re-runs dedupe; missed runs self-heal.
5. **Sleeper id is canonical** — `roster_snapshot_canon` maps Yahoo ids via `player_id_map`.

## Known gaps / verify on first real run
- **Yahoo parser unverified live.** Tested against fixtures in Yahoo's documented JSON shape.
  If something parses wrong, the raw payload is saved; send me the `raw_response.body`.
- **Elimination = empty roster** (both platforms). Confirm after the next chop.
- **Yahoo txns have no week**; derive from `processed_at` vs the NFL schedule in Week B.
- **Yahoo DEF ids** (100000+) won't map to Sleeper's team-abbrev DEF ids; irrelevant if
  your Yahoo leagues use the default no-K/DEF guillotine settings.
- **Projections = Sleeper only so far.** The "average of sources" decision lands in Week B:
  add a second source (Yahoo projected points or a manual FantasyPros CSV) as another
  `projection_snapshot.source`, then average.
- `ffm idmap` and `ffm history` couldn't be run live from my sandbox (GitHub blocked);
  first run on your machine is the test.
