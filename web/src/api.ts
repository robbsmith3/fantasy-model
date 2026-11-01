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
};
export type Recommendation = {
  summary: {
    team: string;
    faab: number;
    week: number;
    chop_pending: boolean;
    assume_chop: string | null;
    p_win: number;
    p_chop_next: number;
    lambda_per_100: number;
    faab_discount: number;
    sigma: number;
  };
  sheet: SheetRow[];
  plan: PlanRow[];
};
export type LineupSide = {
  starters: { player_id: string; name: string; pos: string; mu: number }[];
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
  next_waiver: string | null;
  next_kickoff: string | null;
  next_kickoff_week: number | null;
  sunday_main: string | null;
};
export type Metrics = Record<string, number | string | null>;
export type ModelReport = {
  weeks: number[];
  projections: Metrics[];
  survival: { summary: Metrics; weeks: Metrics[] };
  prices: Metrics;
};
export type Meta = {
  version: string;
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
