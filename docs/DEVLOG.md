# Developer log

A running record of what was built, why, and what the evidence said. Newest entries last.
The README describes the project as it is now; this file keeps the history, including dead ends.
Numbers come from the 2026 Sleeper league (18 teams, $1,000 FAAB) unless noted.

## 2026-10-31

### Phase 1: data layer
- Snapshot jobs (`daily`, `pre_waiver`, `post_waiver`, `pre_kickoff`) into one DuckDB file,
  scheduled with Windows Task Scheduler (WSL cron misses runs when the VM sleeps).
- Design rules: raw responses land before parsing; snapshots are append-only; projections store
  stat lines, not points; Sleeper ids are canonical. Original setup notes are in the appendix.
- Fixed on first run: an empty scaffold `tests/__init__.py` broke `from conftest import`;
  `sleeper_user_id` must be the numeric id, not the username.

### Yahoo: blocked on API approval
- OAuth works, but every Fantasy endpoint returns 403 "This application is not authorized",
  even public ones and even for a brand-new app. Since July 2026 Yahoo requires each app to be
  approved for the Fantasy API (application form at sports.yahoo.com/developer). Applied as
  personal, single-user, read-only use. Until approval, Yahoo leagues are skipped.
- Approval terms to honor when it lands: "Fantasy data provided by Yahoo Fantasy" attribution
  and the unmodified logo wherever Yahoo data appears; no reverse engineering; one account.
- Decision: a future commercial product would need its own application, not this one.

### Bid recommender proof of concept
- Sleeper returns losing waiver claims too: 110 winning and 257 losing bids for weeks 1-7.
  That turned the opponent model from "what did it sell for" into "what was each team willing
  to pay".
- Pipeline: league-scored projections -> best-lineup gain per pickup -> Monte Carlo of the
  remaining chops (lowest score is eliminated; verified every week 1-7) -> opponent max-bid
  distribution -> bid maximizing P(get) x (win-prob gain - price of a dollar x bid).
- Sleeper's preset `pts_ppr` uses -1 per INT; this league uses -2, so we score stat lines
  ourselves.

### Projections: consensus and our own model
- Added ESPN (public endpoint; stat-id map verified by reproducing ESPN's own totals within
  0.31 pts) and FantasyPros consensus (via nflverse; current week only, so it must be
  snapshotted before kickoff).
- Backtest weeks 1-7: Sleeper MAE 5.49, ESPN 5.59, their average 5.49. Their errors correlate
  0.97, so averaging the two gains nothing.
- Our LightGBM model on nflverse usage, expected points (ffopportunity), Vegas lines and
  opponent: alone 5.55; equal-weight Sleeper + ESPN + model 5.38 (best). Weights fitted on past
  weeks did no better (5.39), in line with the forecast-combination literature. Injury reports
  barely helped (5.536 vs 5.545).

### Survival model
- Player-level simulation (empirical outcome bank, DNP mass, QB/pass-catcher correlation 0.29)
  was well calibrated (80% intervals held 80%) but predicted chops no better than the team-level
  model (log score -2.59 vs -2.52; uniform -2.67), so production keeps the fast team-level form.
- Added team-strength drift (starters' projections drift ~3 pts after 1 week, ~5 after 6):
  our P(win) 47% -> 38%.
- Rival pickups: first version had Four More Yards (most FAAB) at 76% to win because it won
  every future dump. Fixed by tracking FAAB per simulated season and valuing sequential pickups;
  now ~12%. Backtest of "who wins the auction": right 12% (top 3: 52%; richest team 3%).

### Bid model
- Per-opponent participation + share-of-FAAB model with shrunken team effects.
  CRPS on 33 contested auctions: base 103.3 -> team effects 96.7. Competition (105.1),
  desperation (102.8) and season progress (169.9, extrapolates) did not help.
- Joint claim plan (distinct drops, total within FAAB, each claim valued given earlier ones may
  win), blocking value (what the would-be rival gains), and a FAAB price from our own survival
  curve (a team near the chop spends now).

### Lineup and live odds
- Survival lineup (maximize P(not lowest), not points): works as designed in tests, but on real
  rosters gains were <= 0.2 points of P(safe); bench options are close substitutes.
- Live chop odds: Sleeper live scores + ESPN game clocks + simulated remainder of each game.

### Web UI (Chop Desk)
- FastAPI over read-only DuckDB, cached per data snapshot; React + TypeScript + Vite + Tailwind,
  TanStack Router/Query. Pages: Overview, Waivers, Live, Lineup, League, Team, Players, Model.
- Found while checking: a ResizeObserver race left the survival chart empty; route view
  transitions left whole pages blank in headless renders (removed); `$-24` formatting;
  DuckDB allows one writer, so jobs now wait for the lock while the server reads.
- Waiver deadlines: claims lock at the run's hour (Wed 2:00 AM CT); results post ~2:08. The data
  only has the processed time, so the cutoff is that time rounded down to the hour.
- Phone access via Tailscale was scoped and dropped: PC-only for now.

### Weekly report card
- Grades each waiver run against walk-forward predictions. Weeks 3-7: price inside the 80%
  range 67%, median miss -$48 (prices ran higher), named the winner 12%.
- Tried widening ranges to reach 80% coverage: needed ~2x spread and worsened CRPS 92.8 -> 149.
  The weekly calibration therefore minimizes CRPS (currently: no correction) and refits the
  rival pay scale (1.445, matching the hand-set 1.45).

## Open questions
- Naming auction winners is the weakest piece; weekly roster need is the obvious missing input.
- Injury discounts (`fit.INJURY_AVAIL`) are hand-set.
- Yahoo parser is unverified against live responses (raw payloads are kept for re-parsing).

---

## Appendix: original Phase 1 README (archived as written)

### fantasy-model — Phase 1: data layer

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

#### Step 1 — Drop into the project (WSL Ubuntu)
```bash
cd ~/code/fantasy-model          # unzip over the uv template; replaces pyproject.toml
uv sync                          # installs package + `ffm` script into .venv
printf "data/\nlogs/\nconfig/leagues.toml\n" >> .gitignore
cp config/leagues.example.toml config/leagues.toml
uv run pytest && uv run pre-commit run --all-files     # 7 tests, all mocked
```

#### Step 2 — Sleeper (5 min)
```bash
curl -s https://api.sleeper.app/v1/user/<username> | python3 -m json.tool | grep user_id
```
Put `user_id` and the league id (from the league URL) in `config/leagues.toml`, then:
```bash
uv run ffm idmap                 # ~10k rows; required for Yahoo<->Sleeper joins
uv run ffm run daily             # smoke test #1
uv run ffm run pre_waiver        # Sleeper part should print league/scores/proj counts
```

#### Step 3 — Yahoo OAuth (15 min, one time)
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

#### Step 4 — Schedule (Windows Task Scheduler, not WSL cron)
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

#### Step 5 — History for Week B (optional now, needed next phase)
```bash
uv run ffm history 2018 2019 2020 2021 2022 2023 2024 2025
```
Loads weekly player stats, snap counts, schedules (spread/total → implied team totals).
Only the seasons passed are replaced; with no seasons, `uv run ffm history` refreshes the
current season (the weekly `history` task does this) and leaves earlier ones intact.

#### Operating it
```bash
uv run ffm health                # last runs, failures, unmapped Yahoo players, bids captured
tail -f logs/snapshots.log
uv run python -c "import duckdb; print(duckdb.connect('data/guillotine.duckdb').sql('from waiver_pool limit 20'))"
```
A job exits non-zero and records `ok=false` + traceback in `snapshot_run.error` if any step
fails, but **other leagues/steps still run** (per-step isolation).

#### Phase 2 — Bid recommendations (`ffm recommend`)
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

#### The UI (Chop Desk)
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

The server precomputes league odds, players and the default bid sheet at startup and caches
everything per data snapshot. While it computes, scheduled jobs wait for the database lock
(up to 2 minutes) instead of failing.

#### Design rules
1. **Raw first** — every response lands in `raw_response` before parsing; parser bugs are
   fixed by re-parsing, never by lost snapshots. Matters most for Yahoo (parser written
   against documented shape, not yet verified live) and Sleeper projections (unofficial).
2. **Ephemeral vs re-fetchable** — `*_snapshot` tables are append-only; history is upserted.
3. **Stats, not points** — projections store stat lines; each league re-scores later.
4. **Idempotent + rolling 2-week window** — re-runs dedupe; missed runs self-heal.
5. **Sleeper id is canonical** — `roster_snapshot_canon` maps Yahoo ids via `player_id_map`.

#### Known gaps / verify on first real run
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
