import { queryOptions } from "@tanstack/react-query";

// Response shapes of src/fantasy_model/api.py.
export type Team = {
  team_id: string;
  name: string;
  owner: string | null;
  is_me: boolean;
  avatar: string | null;
  alive: boolean;
  faab: number | null;
  chopped_week: number | null;
  proj: Record<string, number>;
  p_chop_next: number | null;
  p_win: number;
  exp_weeks: number | null;
  survival: number[];
};
export type League = {
  season: number;
  week: number;
  chop_pending: boolean;
  weeks: number[];
  chops_left: number;
  me: string;
  league_id: string;
  teams: Team[];
};
export type RosterPlayer = {
  player_id: string;
  name: string;
  pos: string;
  nfl_team: string | null;
  injury: string | null;
  pts: Record<string, number>;
  starts: number[];
  ros: number;
};
export type TeamDetail = {
  team_id: string;
  name: string;
  avatar: string | null;
  is_me: boolean;
  weeks: number[];
  players: RosterPlayer[];
};
export type Player = {
  player_id: string;
  name: string;
  pos: string;
  nfl_team: string | null;
  injury: string | null;
  owner: string | null;
  owner_id: string | null;
  pts: (number | null)[];
  ros: number;
};
export type Players = { weeks: number[]; players: Player[] };
export type SheetRow = {
  money_cost: number; // win probability this bid costs in later buying power
  walk_away: number; // the most he is worth, counting that cost
  player: string;
  pos: string;
  team: string | null;
  injury: string | null;
  pts_gain: number;
  d_p_win: number;
  deny: number;
  bid: number;
  p_win_claim: number;
  ev: number;
  market_median: number;
  drop: string;
  player_id: string;
  rival: string | null;
  rival_p: number | null;
  curve: [number, number][];
};
export type PlanRow = {
  order: number;
  player: string;
  pos: string;
  bid: number;
  p_get: number;
  d_p_win: number;
  drop: string | null;
  player_id: string;
  walk_away: number | null;
  backup_for: string | null; // earlier claim(s) sharing this drop; this one needs them to miss
};
export type MarketRow = {
  player: string;
  pos: string;
  team: string | null;
  injury: string | null;
  p_claimed: number; // chance any rival bids
  bid: number; // winning rival bid when one does (median; bid_lo-bid_hi is the 80% range)
  bid_lo: number;
  bid_hi: number;
  rival: string;
  rival_p: number;
  player_id: string;
};
/** Most the plan can cost: a backup bids less than its group, and only one of a group can land. */
export const planSpend = (plan: PlanRow[]) =>
  plan.reduce((s, p) => s + (p.backup_for ? 0 : p.bid), 0);
export type Recommendation = {
  summary: {
    team: string;
    faab: number;
    week: number;
    chop_pending: boolean;
    assume_chop: string | null;
    p_win: number;
    p_chop_next: number;
    cost_100: number; // win probability lost by spending $100
    cost_half: number; // ... by spending half our FAAB
    sigma: number;
  };
  sheet: SheetRow[];
  plan: PlanRow[];
  market: MarketRow[];
};
export type LineupSide = {
  starters: { slot: string; player_id: string; name: string; pos: string; mu: number }[];
  p_safe: number;
  mean: number;
};
export type Lineup = {
  week: number;
  team: string;
  team_name: string;
  max_points: LineupSide;
  survival: LineupSide;
  swaps: { out: string; in: string }[];
  line_median: number;
};
export type LiveTeam = {
  team_id: string;
  name: string;
  avatar: string | null;
  is_me: boolean;
  current: number;
  final_mean: number;
  final_p10: number;
  final_p90: number;
  share_left: number;
  p_chop: number;
};
export type Live = {
  week: number;
  games: { pre: number; live: number; final: number };
  teams: LiveTeam[];
  chop_line: { p10: number; p50: number; p90: number };
  fetched_at: number;
};
export type Timeline = {
  now: string;
  waiver_cutoff: string | null; // claims lock (e.g. Wed 2:00 AM CT)
  waiver_processed: string | null; // results land a few minutes later (~2:08)
  waiver_processing: boolean; // between the two
  next_kickoff: string | null;
  next_kickoff_week: number | null;
  sunday_main: string | null;
};
export type PlayerDetail = {
  player_id: string;
  name: string;
  pos: string;
  nfl_team: string | null;
  injury: string | null;
  owner: string | null;
  owner_id: string | null;
  is_mine: boolean;
  weeks: number[];
  consensus: Record<string, number>;
  by_source: Record<string, Record<string, number>>;
  outcome: {
    week: number;
    mean: number;
    p_dnp: number;
    p10: number;
    p25: number;
    p50: number;
    p75: number;
    p90: number;
  } | null;
  bids: { week: number; team: string; bid: number; won: boolean }[];
};
export type Review = {
  latest_leg: number | null;
  latest: {
    leg: number;
    player_id: string;
    player: string;
    price: number;
    pred_median: number;
    pred_p10: number;
    pred_p90: number;
    winner: string;
    pred_winner: string;
    winner_top3: boolean;
  }[];
  by_leg: { leg: number; n: number; covered: number; bias: number; winner_hits: number; winner_top3: number }[];
  summary: { auctions: number; coverage: number; median_bias: number; winner_hit: number; winner_top3: number };
  chop: { weeks: { week: number; teams: number; p_player: number; rank_player: number }[]; summary: Record<string, number> };
  my_claims: { week: number; player: string; bid: number; won: boolean; winning_bid: number | null }[];
  calibration: {
    spread_k: number;
    level_s: number;
    pay_scale: number | null;
    crps_before: number;
    crps_after: number;
    computed_at: string;
  };
};
export type Metrics = Record<string, number | string | null>;
export type ModelReport = {
  weeks: number[];
  projections: Metrics[];
  survival: { summary: Metrics; weeks: Metrics[] };
  prices: Metrics;
};
export type Alert = { level: "error" | "warning"; job: string; message: string };
export type Meta = {
  version: string;
  alerts: Alert[];
  runs: { id: string; job: string; ok: boolean | null; started: string; finished: string | null }[];
};
/** Slow endpoints answer {status} until a background computation finishes. */
export type Async<T> =
  | { status: "ready"; data: T }
  | { status: "computing"; since: number }
  | { status: "error"; error: string };

async function get<T>(path: string): Promise<T> {
  const r = await fetch(path, { headers: { Accept: "application/json" } });
  if (!r.ok) {
    let detail = r.statusText;
    try {
      detail = (await r.json()).detail ?? detail;
    } catch {
      /* non-JSON error body */
    }
    throw new Error(`${r.status}: ${detail}`);
  }
  return r.json() as Promise<T>;
}

const MIN = 60_000;
// Snapshot-derived data only changes after a scheduled job, so it stays fresh for minutes.
export const q = {
  meta: () => queryOptions({ queryKey: ["meta"], queryFn: () => get<Meta>("/api/meta"), staleTime: MIN }),
  timeline: () =>
    queryOptions({ queryKey: ["timeline"], queryFn: () => get<Timeline>("/api/timeline"), staleTime: 10 * MIN }),
  league: () =>
    queryOptions({ queryKey: ["league"], queryFn: () => get<League>("/api/league"), staleTime: 5 * MIN }),
  team: (id: string) =>
    queryOptions({
      queryKey: ["team", id],
      queryFn: () => get<TeamDetail>(`/api/teams/${encodeURIComponent(id)}`),
      staleTime: 5 * MIN,
    }),
  player: (id: string) =>
    queryOptions({
      queryKey: ["player", id],
      queryFn: () => get<PlayerDetail>(`/api/players/${encodeURIComponent(id)}`),
      staleTime: 5 * MIN,
    }),
  players: () =>
    queryOptions({ queryKey: ["players"], queryFn: () => get<Players>("/api/players"), staleTime: 5 * MIN }),
  lineup: (week?: number) =>
    queryOptions({
      queryKey: ["lineup", week ?? null],
      queryFn: () => get<Lineup>(`/api/lineup${week ? `?week=${week}` : ""}`),
      staleTime: 5 * MIN,
    }),
  recommend: (chop?: string) =>
    queryOptions({
      queryKey: ["recommend", chop ?? null],
      queryFn: () =>
        get<Async<Recommendation>>(`/api/recommend${chop ? `?assume_chop=${encodeURIComponent(chop)}` : ""}`),
      staleTime: 5 * MIN,
      // Poll while the server computes; stop once ready or failed.
      refetchInterval: (query) => (query.state.data?.status === "computing" ? 1500 : false),
    }),
  model: () =>
    queryOptions({
      queryKey: ["model"],
      queryFn: () => get<Async<ModelReport>>("/api/model"),
      staleTime: 10 * MIN,
      refetchInterval: (query) => (query.state.data?.status === "computing" ? 2000 : false),
    }),
  review: () =>
    queryOptions({
      queryKey: ["review"],
      queryFn: () => get<Async<Review>>("/api/review"),
      staleTime: 10 * MIN,
      refetchInterval: (query) => (query.state.data?.status === "computing" ? 2000 : false),
    }),
  live: () =>
    queryOptions({
      queryKey: ["live"],
      queryFn: () => get<Live>("/api/live"),
      staleTime: 30_000,
      refetchInterval: 60_000,
      refetchIntervalInBackground: false,
    }),
};

export const pct = (x: number | null | undefined, d = 1) =>
  x == null ? "–" : `${(x * 100).toFixed(d)}%`;
export const num = (x: number | null | undefined, d = 1) => (x == null ? "–" : x.toFixed(d));
export const money = (x: number | null | undefined) =>
  x == null ? "–" : `${x < 0 ? "−" : ""}$${Math.abs(Math.round(x))}`;
