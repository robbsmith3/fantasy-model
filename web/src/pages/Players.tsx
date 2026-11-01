import { useSuspenseQuery } from "@tanstack/react-query";
import { useWindowVirtualizer } from "@tanstack/react-virtual";
import { useDeferredValue, useLayoutEffect, useMemo, useRef, useState } from "react";
import { num, q } from "../api";
import { InjuryBadge, PageHeader, Pos, Segmented } from "../components/ui";

type PosFilter = "ALL" | "QB" | "RB" | "WR" | "TE" | "DEF";
type Avail = "all" | "free" | "owned";
const ROW = 44;

export default function Players() {
  const { data } = useSuspenseQuery(q.players());
  const [text, setText] = useState("");
  const [pos, setPos] = useState<PosFilter>("ALL");
  const [avail, setAvail] = useState<Avail>("free");
  const query = useDeferredValue(text.trim().toLowerCase()); // typing never waits on filtering

  const rows = useMemo(
    () =>
      data.players
        .filter((p) => (pos === "ALL" || p.pos === pos)
          && (avail === "all" || (avail === "free" ? !p.owner_id : !!p.owner_id))
          && (!query || p.name?.toLowerCase().includes(query)))
        .sort((a, b) => b.ros - a.ros),
    [data, pos, avail, query],
  );

  const listRef = useRef<HTMLDivElement>(null);
  const [offset, setOffset] = useState(0);
  useLayoutEffect(() => setOffset(listRef.current?.offsetTop ?? 0), []);
  const v = useWindowVirtualizer({ count: rows.length, estimateSize: () => ROW, overscan: 12, scrollMargin: offset });
  const cols = `minmax(0,1fr) repeat(${data.weeks.length}, 3.25rem) 3.75rem`;

  return (
    <>
      <PageHeader title="Players" sub={`${rows.length.toLocaleString()} players · projected points, injury-adjusted`} />
      <div className="mb-4 flex flex-wrap items-center gap-2">
        <input
          type="search"
          value={text}
          onChange={(e) => setText(e.target.value)}
          placeholder="Search players"
          aria-label="Search players"
          className="w-full rounded-lg border border-line bg-surface px-3 py-1.5 text-sm placeholder:text-muted sm:w-56"
        />
        <Segmented label="Position" value={pos} onChange={setPos}
          options={(["ALL", "QB", "RB", "WR", "TE", "DEF"] as PosFilter[]).map((p) => ({ value: p, label: p === "ALL" ? "All" : p }))} />
        <Segmented label="Availability" value={avail} onChange={setAvail}
          options={[{ value: "free", label: "Available" }, { value: "owned", label: "Rostered" }, { value: "all", label: "All" }]} />
      </div>

      <div className="overflow-x-auto rounded-xl border border-line bg-surface">
        <div className="min-w-[36rem]">
          <div role="row" className="grid border-b border-line px-3 py-2 text-xs font-medium text-muted" style={{ gridTemplateColumns: cols }}>
            <span>Player</span>
            {data.weeks.map((w) => <span key={w} className="text-right">Wk {w}</span>)}
            <span className="text-right">Total</span>
          </div>
          <div ref={listRef} role="rowgroup" className="relative" style={{ height: v.getTotalSize() }}>
            {v.getVirtualItems().map((it) => {
              const p = rows[it.index];
              return (
                <div
                  key={p.player_id}
                  role="row"
                  className="absolute inset-x-0 grid items-center border-b border-line px-3 text-sm hover:bg-surface-2"
                  style={{ gridTemplateColumns: cols, height: ROW, transform: `translateY(${it.start - v.options.scrollMargin}px)` }}
                >
                  <span className="min-w-0 truncate">
                    <Pos pos={p.pos} />
                    {p.name}
                    <InjuryBadge status={p.injury} />
                    <span className="ml-1.5 text-xs text-muted">{p.owner ? p.owner : "free"}</span>
                  </span>
                  {p.pts.map((x, i) => <span key={i} className="text-right tabular text-ink-2">{num(x)}</span>)}
                  <span className="text-right font-medium tabular">{num(p.ros)}</span>
                </div>
              );
            })}
          </div>
        </div>
      </div>
    </>
  );
}
