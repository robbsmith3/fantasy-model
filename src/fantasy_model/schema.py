"""DuckDB schema.

Layers:
  raw_response      every API payload, untouched (re-parse any time)
  entity tables     upserted by natural key (history the API keeps serving)
  *_snapshot tables append-only by snapshot_id (state that disappears)
"""

SCHEMA = """
CREATE TABLE IF NOT EXISTS snapshot_run (
    snapshot_id   VARCHAR PRIMARY KEY,      -- 2026-11-03T01:00Z_pre_waiver (lexical == time order)
    job           VARCHAR NOT NULL,
    season        INTEGER NOT NULL,
    nfl_week      INTEGER NOT NULL,
    started_at    TIMESTAMPTZ NOT NULL,
    finished_at   TIMESTAMPTZ,
    ok            BOOLEAN,
    error         VARCHAR
);

CREATE TABLE IF NOT EXISTS raw_response (
    snapshot_id   VARCHAR NOT NULL,
    fetched_at    TIMESTAMPTZ NOT NULL,
    platform      VARCHAR NOT NULL,         -- sleeper | yahoo
    endpoint      VARCHAR NOT NULL,         -- logical name, e.g. league.rosters
    url           VARCHAR NOT NULL,
    http_status   INTEGER,
    body          JSON
);

CREATE TABLE IF NOT EXISTS league (
    platform        VARCHAR NOT NULL,
    league_id       VARCHAR NOT NULL,       -- Sleeper league_id / Yahoo league_key
    season          INTEGER NOT NULL,
    name            VARCHAR,
    total_rosters   INTEGER,
    faab_budget     INTEGER,
    scoring         JSON,
    roster_slots    JSON,
    settings        JSON,
    previous_league_id VARCHAR,
    updated_at      TIMESTAMPTZ,
    PRIMARY KEY (platform, league_id)
);

CREATE TABLE IF NOT EXISTS team (
    platform      VARCHAR NOT NULL,
    league_id     VARCHAR NOT NULL,
    team_id       VARCHAR NOT NULL,         -- Sleeper roster_id / Yahoo team_key
    owner_id      VARCHAR,
    owner_name    VARCHAR,
    team_name     VARCHAR,
    is_me         BOOLEAN DEFAULT FALSE,
    PRIMARY KEY (platform, league_id, team_id)
);

CREATE TABLE IF NOT EXISTS team_week (
    platform      VARCHAR NOT NULL,
    league_id     VARCHAR NOT NULL,
    season        INTEGER NOT NULL,
    week          INTEGER NOT NULL,
    team_id       VARCHAR NOT NULL,
    points        DOUBLE,
    starters      JSON,
    player_points JSON,
    PRIMARY KEY (platform, league_id, season, week, team_id)
);

CREATE TABLE IF NOT EXISTS txn (
    platform        VARCHAR NOT NULL,
    league_id       VARCHAR NOT NULL,
    transaction_id  VARCHAR NOT NULL,
    season          INTEGER NOT NULL,
    week            INTEGER,                -- Sleeper leg; NULL for Yahoo (use processed_at)
    type            VARCHAR,
    status          VARCHAR,
    team_id         VARCHAR,
    bid             INTEGER,                -- FAAB amount; THE training label
    created_at      TIMESTAMPTZ,
    processed_at    TIMESTAMPTZ,
    PRIMARY KEY (platform, league_id, transaction_id)
);

CREATE TABLE IF NOT EXISTS txn_player (
    platform        VARCHAR NOT NULL,
    league_id       VARCHAR NOT NULL,
    transaction_id  VARCHAR NOT NULL,
    player_id       VARCHAR NOT NULL,       -- platform-native id
    action          VARCHAR NOT NULL,       -- add | drop
    team_id         VARCHAR,
    source_type     VARCHAR,                -- waivers | freeagents | team
    PRIMARY KEY (platform, league_id, transaction_id, player_id, action)
);

CREATE TABLE IF NOT EXISTS roster_snapshot (
    snapshot_id   VARCHAR NOT NULL,
    platform      VARCHAR NOT NULL,
    league_id     VARCHAR NOT NULL,
    team_id       VARCHAR NOT NULL,
    player_id     VARCHAR NOT NULL,
    is_starter    BOOLEAN,
    PRIMARY KEY (snapshot_id, platform, league_id, team_id, player_id)
);

CREATE TABLE IF NOT EXISTS team_state_snapshot (
    snapshot_id     VARCHAR NOT NULL,
    platform        VARCHAR NOT NULL,
    league_id       VARCHAR NOT NULL,
    team_id         VARCHAR NOT NULL,
    faab_remaining  INTEGER,
    points_for      DOUBLE,
    n_players       INTEGER,
    eliminated      BOOLEAN,                -- heuristic: empty roster mid-season
    PRIMARY KEY (snapshot_id, platform, league_id, team_id)
);

-- Projected STATS, not points: each league re-scores with its own settings.
CREATE TABLE IF NOT EXISTS projection_snapshot (
    snapshot_id      VARCHAR NOT NULL,
    source           VARCHAR NOT NULL,
    season           INTEGER NOT NULL,
    week             INTEGER NOT NULL,
    source_player_id VARCHAR NOT NULL,
    position         VARCHAR,
    nfl_team         VARCHAR,
    stats            JSON,
    PRIMARY KEY (snapshot_id, source, season, week, source_player_id)
);

CREATE TABLE IF NOT EXISTS player_status_snapshot (
    snapshot_id       VARCHAR NOT NULL,
    platform          VARCHAR NOT NULL,
    player_id         VARCHAR NOT NULL,
    full_name         VARCHAR,
    position          VARCHAR,
    nfl_team          VARCHAR,
    status            VARCHAR,
    injury_status     VARCHAR,
    injury_body_part  VARCHAR,
    practice_participation VARCHAR,
    depth_chart_order INTEGER,
    PRIMARY KEY (snapshot_id, platform, player_id)
);

CREATE TABLE IF NOT EXISTS player_id_map (
    gsis_id        VARCHAR,
    sleeper_id     VARCHAR,
    yahoo_id       VARCHAR,
    espn_id        VARCHAR,
    fantasypros_id VARCHAR,
    name           VARCHAR,
    position       VARCHAR,
    team           VARCHAR
);

-- Every roster row expressed in Sleeper id space (the canonical id for status/projections).
CREATE OR REPLACE VIEW roster_snapshot_canon AS
SELECT rs.*,
       CASE WHEN rs.platform = 'sleeper' THEN rs.player_id ELSE m.sleeper_id END AS sleeper_id
FROM roster_snapshot rs
LEFT JOIN player_id_map m ON rs.platform = 'yahoo' AND m.yahoo_id = rs.player_id;

-- Waiver pool per (roster snapshot, league) = rostered-nowhere players, using the
-- latest player-status snapshot taken at or before that roster snapshot.
CREATE OR REPLACE VIEW waiver_pool AS
WITH roster_snaps AS (
    SELECT DISTINCT snapshot_id, platform, league_id FROM roster_snapshot
)
SELECT r.snapshot_id, r.platform, r.league_id,
       p.player_id AS sleeper_id, p.full_name, p.position, p.nfl_team, p.injury_status
FROM roster_snaps r
JOIN player_status_snapshot p
  ON p.snapshot_id = (SELECT max(s.snapshot_id) FROM player_status_snapshot s
                      WHERE s.snapshot_id <= r.snapshot_id)
WHERE p.position IN ('QB', 'RB', 'WR', 'TE', 'K', 'DEF')
  AND p.nfl_team IS NOT NULL
  AND NOT EXISTS (
      SELECT 1 FROM roster_snapshot_canon c
      WHERE c.snapshot_id = r.snapshot_id AND c.platform = r.platform
        AND c.league_id = r.league_id AND c.sleeper_id = p.player_id);
"""
