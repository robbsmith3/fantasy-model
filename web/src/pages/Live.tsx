import { useQuery } from "@tanstack/react-query";
import { useNavigate } from "@tanstack/react-router";
import clsx from "clsx";
import { num, pct, q } from "../api";
import { useEffect, useRef, useState } from "react";
import { RiskBadge, riskOf, TeamAvatar } from "../components/brand";
import { ChopLine } from "../components/charts";
import { Card, ErrorBox, PageHeader, Skeleton, Stat, Td, Th } from "../components/ui";

const PREF = "notify-danger";
const readPref = () => {
  try {
    return localStorage.getItem(PREF) === "1";
  } catch {
    return false;
  }
};

/** While the app is open: a system notification when our chop risk crosses into Danger. */
function useDangerAlert(pChop: number | undefined) {
  const supported = typeof window !== "undefined" && "Notification" in window;
  const [on, setOn] = useState(() => supported && readPref() && Notification.permission === "granted");
  const wasDanger = useRef(false);
  useEffect(() => {
    if (pChop == null) return;
    const danger = riskOf(pChop) === "danger";
    if (on && danger && !wasDanger.current) {
      new Notification("Chop Desk: you’re in Danger", {
        body: `${pct(pChop)} chance you post the lowest score this week.`,
        icon: "/icon-192.png",
        tag: "chop-danger",
      });
    }
    wasDanger.current = danger;
  }, [pChop, on]);
  const toggle = async () => {
    const next = !on && (await Notification.requestPermission()) === "granted";
    setOn(next);
    try {
      localStorage.setItem(PREF, next ? "1" : "0");
    } catch {
      /* storage unavailable: still on for this visit */
    }
  };
  return { supported, on, toggle };
}

export default function Live() {
  const live = useQuery(q.live());
  const navigate = useNavigate();
  const d = live.data;
  const me = d?.teams.find((t) => t.is_me);
  const alert = useDangerAlert(me?.p_chop);
  const updated = d ? new Date(d.fetched_at * 1000).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" }) : null;

  return (
    <>
      <PageHeader
        title="Live chop odds"
        sub="Points so far plus a simulated finish for every unplayed minute. Refreshes every minute."
        right={
          <div className="flex flex-wrap items-center gap-2">
          {alert.supported && (
            <button
              type="button"
              onClick={alert.toggle}
              aria-pressed={alert.on}
              className={clsx(
                "rounded-lg border px-3 py-1.5 text-sm",
                alert.on ? "border-accent/50 bg-accent-wash text-ink" : "border-line bg-surface text-ink-2 hover:text-ink",
              )}
            >
              {alert.on ? "Danger alerts on" : "Alert me if I’m in danger"}
            </button>
          )}
          <button
            onClick={() => live.refetch()}
            disabled={live.isFetching}
            className="rounded-lg border border-line bg-surface px-3 py-1.5 text-sm text-ink-2 hover:text-ink disabled:opacity-50"
          >
            {live.isFetching ? "Updating…" : `Updated ${updated ?? "–"}`}
          </button>
          </div>
        }
      />
      {live.isError && <ErrorBox error={live.error} />}
      {!d && !live.isError && <Skeleton className="h-72 w-full" />}
      {d && me && (
        <div className={clsx("space-y-4 transition-opacity", live.isFetching && "opacity-70")}>
          <Card>
            <div className="grid grid-cols-2 gap-6 sm:grid-cols-4">
              <Stat hero label="Your chance of the lowest score" value={pct(me.p_chop)} hint={<RiskBadge p={me.p_chop} />} />
              <Stat label="Your points so far" value={num(me.current)} />
              <Stat label="Projected final" value={num(me.final_mean)} hint={`80% range ${num(me.final_p10, 0)}–${num(me.final_p90, 0)}`} />
              <Stat label={`Week ${d.week} games`} value={`${d.games.final}/${d.games.final + d.games.live + d.games.pre}`} hint={`${d.games.live} live · ${d.games.pre} to play`} />
            </div>
          </Card>

          <Card
            title="The chop line"
            sub="Where each team is likely to finish; the lowest score is chopped. Tap a team for its roster."
          >
            <ChopLine
              line={d.chop_line}
              rows={[...d.teams]
                .sort((a, b) => a.final_mean - b.final_mean)
                .map((t) => ({
                  key: t.team_id,
                  label: t.name,
                  icon: <TeamAvatar src={t.avatar} name={t.name} size={24} />,
                  highlight: t.is_me,
                  low: t.final_p10,
                  mid: t.final_mean,
                  high: t.final_p90,
                  now: t.current,
                  right: <>{pct(t.p_chop)}<RiskBadge p={t.p_chop} compact /></>,
                  detail: (
                    <>
                      {num(t.current)} now · ~{num(t.final_mean, 0)} final ({num(t.final_p10, 0)}–{num(t.final_p90, 0)}) ·{" "}
                      {pct(t.share_left, 0)} of games left
                    </>
                  ),
                }))}
              onSelect={(id) => navigate({ to: "/teams/$teamId", params: { teamId: id } })}
            />
          </Card>

          <Card title="Scores" pad={false}>
            <div className="overflow-x-auto">
              <table className="w-full">
                <thead>
                  <tr className="border-b border-line">
                    <Th sticky>Team</Th><Th right>Now</Th><Th right>Projected final</Th><Th right>80% range</Th><Th right>Games left</Th><Th right>Lowest score</Th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-line">
                  {d.teams.map((t) => (
                    <tr key={t.team_id} className={clsx(t.is_me ? "bg-accent-wash" : "bg-surface")}>
                      <Td sticky className={clsx(t.is_me && "bg-[color-mix(in_srgb,var(--accent)_10%,var(--surface))] font-semibold")}><span className="flex items-center gap-2.5"><TeamAvatar src={t.avatar} name={t.name} size={24} />{t.name}</span></Td>
                      <Td right>{num(t.current)}</Td>
                      <Td right>{num(t.final_mean)}</Td>
                      <Td right className="text-ink-2">{num(t.final_p10, 0)}–{num(t.final_p90, 0)}</Td>
                      <Td right className="text-ink-2">{pct(t.share_left, 0)}</Td>
                      <Td right className="font-semibold">{pct(t.p_chop)}</Td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Card>
        </div>
      )}
    </>
  );
}
