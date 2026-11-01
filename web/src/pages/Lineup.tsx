import { keepPreviousData, useQuery, useSuspenseQuery } from "@tanstack/react-query";
import clsx from "clsx";
import { useState } from "react";
import { type LineupSide, num, pct, q } from "../api";
import { PlayerHead } from "../components/brand";
import { Card, ErrorBox, PageHeader, Pos, Segmented, Skeleton, Stat } from "../components/ui";

function Side({ title, side, other, accent }: { title: string; side: LineupSide; other: LineupSide; accent?: boolean }) {
  const otherIds = new Set(other.starters.map((s) => s.player_id));
  return (
    <Card title={title} className={clsx(accent && "ring-1 ring-accent/40")}>
      <div className="mb-4 grid grid-cols-2 gap-4">
        <Stat label="Chance you’re not lowest" value={pct(side.p_safe)} />
        <Stat label="Projected points" value={num(side.mean)} />
      </div>
      <ul className="divide-y divide-line">
        {side.starters.map((s) => (
          <li key={s.player_id} className="flex items-center justify-between py-2 text-sm">
            <span className={clsx("flex items-center gap-2.5", !otherIds.has(s.player_id) && "font-semibold")}>
              <PlayerHead id={s.player_id} name={s.name} size={28} />
              <Pos pos={s.pos} />
              {s.name}
              {!otherIds.has(s.player_id) && <span className="ml-2 text-xs font-normal text-accent">differs</span>}
            </span>
            <span className="tabular text-ink-2">{num(s.mu)}</span>
          </li>
        ))}
      </ul>
    </Card>
  );
}

export default function Lineup() {
  const { data: L } = useSuspenseQuery(q.league());
  const weeks = L.chop_pending ? L.weeks : L.weeks.filter((w) => w > L.week);
  const [week, setWeek] = useState(String(weeks[0]));
  const lu = useQuery({ ...q.lineup(Number(week)), placeholderData: keepPreviousData });
  const d = lu.data;

  return (
    <>
      <PageHeader
        title="Lineup"
        sub="The lowest score is chopped, so start whoever maximizes your chance of not being lowest, not just expected points."
        right={
          <Segmented
            label="Week"
            value={week}
            onChange={setWeek}
            options={weeks.slice(0, 4).map((w) => ({ value: String(w), label: `Wk ${w}` }))}
          />
        }
      />
      {lu.isError && <ErrorBox error={lu.error} />}
      {!d && !lu.isError && <Skeleton className="h-80 w-full" />}
      {d && (
        <div className={clsx("space-y-4 transition-opacity", lu.isPlaceholderData && "opacity-50")}>
          <Card>
            {d.swaps.length === 0 ? (
              <p className="text-sm">
                <span className="font-semibold">No change.</span>{" "}
                <span className="text-ink-2">Your max-points lineup is also your safest for week {d.week}.</span>
              </p>
            ) : (
              <div className="space-y-1 text-sm">
                {d.swaps.map((s) => (
                  <p key={s.in}>
                    Start <span className="font-semibold">{s.in}</span> instead of <span className="font-semibold">{s.out}</span>
                  </p>
                ))}
                <p className="text-xs text-muted">
                  Chance you’re not lowest: {pct(d.max_points.p_safe)} → {pct(d.survival.p_safe)}
                </p>
              </div>
            )}
            <p className="mt-2 text-xs text-muted">
              Typical score of the lowest rival this week: {num(d.line_median, 0)} pts
            </p>
          </Card>
          <div className="grid gap-4 md:grid-cols-2">
            <Side title="Survival lineup" side={d.survival} other={d.max_points} accent />
            <Side title="Max-points lineup" side={d.max_points} other={d.survival} />
          </div>
        </div>
      )}
    </>
  );
}
