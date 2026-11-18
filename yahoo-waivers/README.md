# yahoo-waivers

Waiver recommendations for my Yahoo Fantasy Football guillotine leagues, built to stay inside the
Yahoo Fantasy Sports API personal-use agreement: read-only calls, my own leagues only, Yahoo data
held in memory and gone when the command exits. See [SPEC.md](SPEC.md) for the full design.

```
$ uv run python -m waivers run
guillotine (My League) | week 10 | FAAB $80
--------------------------------------------
1. Some Player (WR, DAL)  bid $21-$36  drop Bench Guy
   +5.3 pts/wk to your lineup; released by a chopped team; snap share up to 85% (season 68%)
...
Fantasy data provided by Yahoo Fantasy
```

## Setup (once)

1. **Register an app** at the Yahoo Developer Network: https://developer.yahoo.com/apps/create/
   - Application type: **Installed Application**
   - Redirect URI: `oob` (if Yahoo insists on an https URL, use `https://localhost` and pass the
     same value to `auth --redirect`)
   - API permissions: **Fantasy Sports → Read**
   - Copy the **Client ID** and **Client Secret**.
2. **Install** (needs [uv](https://docs.astral.sh/uv/)):
   ```bash
   cd yahoo-waivers && uv sync
   ```
3. **Credentials:** copy `.env.example` to `.env`, fill in the client id and secret, then lock it
   down:
   ```bash
   cp .env.example .env && chmod 600 .env
   ```
4. **Sign in to Yahoo** (writes the refresh token into `.env`, owner-only):
   ```bash
   uv run python -m waivers auth
   ```
5. **Leagues:** copy `config.example.toml` to `config.toml` and set each league's `league_id` and
   your `team_id` (both are in the league and team page URLs).

## Run

```bash
uv run python -m waivers run                     # all leagues
uv run python -m waivers run --league 123456     # one league (id or name from config.toml)
uv run python -m waivers run --save              # also write one file to recs/
uv run python -m waivers purge                   # delete everything in recs/ now
```
Run it on demand before each waiver deadline. Every run empties `recs/` before doing anything
else, so a saved file never outlives the next run.

## Compliance notes

| Rule | How |
|---|---|
| Read-only | Only GET requests to the Yahoo API. The only POST is to Yahoo's OAuth token endpoint. |
| My leagues only | Requests are built from an allowlist of the league and team keys in `config.toml`; anything else raises before a request is made. |
| No storage of Yahoo data | No database, cache, CSV, pickle or parquet for Yahoo responses, and no logged response bodies. The only file written is the optional `recs/` output, which every run deletes first. |
| No model trained on Yahoo data | Scoring is fixed hand-set rules over public nflverse data plus my roster, pool and league settings in memory (`scoring.py`). |
| Minimum data | League settings, my team (FAAB), my roster, recent add/drop transactions, and the top N available players per position. No player stats are requested from Yahoo. |
| Rate limits | 1 request/second by default; exponential backoff with jitter on 429, 999 and 5xx, up to 5 retries, honoring `Retry-After`. |
| No sharing | Network calls go only to Yahoo (API and OAuth) and the public nflverse files on GitHub. A test checks every request's host. |
| Attribution | Output always ends with "Fantasy data provided by Yahoo Fantasy". |
| Credentials | `.env` is gitignored and should be `chmod 600`; the tool warns if it isn't. Tokens are never printed or logged. |

### Keeping Claude Code out of Yahoo data
Claude Code built this tool with synthetic data only and must not read real output. Add these
to `~/.claude/settings.json` (merge into the existing lists). They're settings Claude can't change.
In `permissions` rules an absolute path needs a leading `//`; a single `/` is relative to the
settings file's folder:
```json
{
  "permissions": {
    "deny": [
      "Read(//home/robbs/code/fantasy-model/yahoo-waivers/recs/**)",
      "Read(//home/robbs/code/fantasy-model/yahoo-waivers/.env)"
    ]
  },
  "sandbox": {
    "credentials": {
      "files": [
        { "path": "/home/robbs/code/fantasy-model/yahoo-waivers/recs", "mode": "deny" },
        { "path": "/home/robbs/code/fantasy-model/yahoo-waivers/.env", "mode": "deny" }
      ]
    }
  }
}
```
Don't paste real output into a Claude conversation; describe the problem or paste an error
message instead (errors carry only an endpoint name and status code).

## Tests

```bash
uv run pytest
```
All tests use invented players and mocked Yahoo responses; none touches the network. They cover
parsing, the allowlist, throttling and backoff, scoring, chopped-team detection, FAAB ranges,
`recs/` handling, that a run writes no files without `--save`, that requests only go to allowed
hosts, that response bodies never reach output or logs, and that no dependency is AGPL.

## Dependencies

| Package | Version | License | Why |
|---|---|---|---|
| requests | 2.34.2 | Apache-2.0 | HTTP |
| nflreadpy | 0.1.5 | MIT | Public nflverse data |
| polars | 2.1.0 | MIT | Tables |
| python-dotenv | 1.2.4 | BSD-3-Clause | Load `.env` |
| polars-runtime-32 | 2.1.0 | MIT | polars |
| urllib3 | 2.8.0 | MIT | requests |
| charset-normalizer | 3.5.2 | MIT | requests |
| idna | 3.20 | BSD-3-Clause | requests |
| certifi | 2026.10.23 | MPL-2.0 | requests (CA certificates) |
| platformdirs | 4.14.7 | MIT | nflreadpy |
| pydantic | 2.14.3 | MIT | nflreadpy |
| pydantic_core | 2.50.4 | MIT | pydantic |
| pydantic-settings | 2.16.3 | MIT | nflreadpy |
| annotated-types | 0.8.0 | MIT | pydantic |
| typing-inspection | 0.4.5 | MIT | pydantic |
| typing_extensions | 4.16.0 | PSF-2.0 | pydantic |
| tqdm | 4.70.1 | MPL-2.0 AND MIT | nflreadpy |
| pytest (dev) | 9.2.0 | MIT | tests |
| pluggy (dev) | 1.7.0 | MIT | pytest |
| iniconfig (dev) | 2.3.1 | MIT | pytest |
| packaging (dev) | 26.3 | Apache-2.0 OR BSD-2-Clause | pytest |
| pygments (dev) | 2.21.0 | BSD-2-Clause | pytest |
| ruff (dev) | 0.17.5 | MIT | lint and format |

MPL-2.0 is fine for a personal tool that's never distributed (agreement §2.c.viii only concerns
licenses that could impose obligations on Yahoo). A test fails if any dependency is AGPL.
