import { useQuery } from "@tanstack/react-query";
import { useNavigate } from "@tanstack/react-router";
import clsx from "clsx";
import { type ReactNode, useDeferredValue, useEffect, useMemo, useRef, useState } from "react";
import { q } from "../api";
import { PlayerHead, TeamAvatar } from "./brand";
import { usePlayerSheet } from "./PlayerSheet";

type Item = { key: string; label: string; hint: string; icon: ReactNode; run: () => void };

const PAGES = [
  ["/", "Overview"],
  ["/waivers", "Waivers"],
  ["/live", "Live chop odds"],
  ["/lineup", "Lineup"],
  ["/league", "League"],
  ["/players", "Players"],
  ["/model", "Model"],
] as const;

/** Ctrl/⌘+K or "/" anywhere. Pages, teams and players; Enter opens, arrows move. */
export function CommandPalette({ open, setOpen }: { open: boolean; setOpen: (v: boolean) => void }) {
  const navigate = useNavigate();
  const sheet = usePlayerSheet();
  const [text, setText] = useState("");
  const [active, setActive] = useState(0);
  const query = useDeferredValue(text.trim().toLowerCase());
  const input = useRef<HTMLInputElement>(null);
  const league = useQuery({ ...q.league(), enabled: open }).data;
  const players = useQuery({ ...q.players(), enabled: open }).data;

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const typing = e.target instanceof HTMLElement && /^(INPUT|TEXTAREA|SELECT)$/.test(e.target.tagName);
      if ((e.key === "k" && (e.metaKey || e.ctrlKey)) || (e.key === "/" && !typing)) {
        e.preventDefault();
        setOpen(true);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [setOpen]);

  useEffect(() => {
    if (open) {
      setText("");
      setActive(0);
      setTimeout(() => input.current?.focus(), 0);
    }
  }, [open]);

  const items = useMemo<Item[]>(() => {
    const go = (fn: () => void) => () => {
      setOpen(false);
      fn();
    };
    const pages: Item[] = PAGES.map(([to, label]) => ({
      key: `page:${to}`,
      label,
      hint: "Page",
      icon: <span className="inline-flex size-7 items-center justify-center rounded-full bg-surface-2 text-xs text-muted">↵</span>,
      run: go(() => navigate({ to })),
    }));
    const teams: Item[] = (league?.teams ?? []).map((t) => ({
      key: `team:${t.team_id}`,
      label: t.name,
      hint: t.alive ? "Team" : `Chopped wk ${t.chopped_week ?? "?"}`,
      icon: <TeamAvatar src={t.avatar} name={t.name} size={28} />,
      run: go(() => navigate({ to: "/teams/$teamId", params: { teamId: t.team_id } })),
    }));
    const people: Item[] = (players?.players ?? [])
      .slice()
      .sort((a, b) => b.ros - a.ros)
      .map((p) => ({
        key: `player:${p.player_id}`,
        label: p.name,
        hint: `${p.pos} · ${p.nfl_team ?? "FA"} · ${p.owner ?? "free agent"}`,
        icon: <PlayerHead id={p.player_id} name={p.name} size={28} />,
        run: go(() => sheet.open(p.player_id)),
      }));
    if (!query) return [...pages, ...teams.filter((t) => !t.hint.startsWith("Chopped"))].slice(0, 12);
    const match = (i: Item) => i.label.toLowerCase().includes(query);
    return [...pages.filter(match), ...teams.filter(match), ...people.filter(match)].slice(0, 30);
  }, [query, league, players, navigate, sheet, setOpen]);

  useEffect(() => setActive(0), [query]);
  if (!open) return null;
  const onKey = (e: React.KeyboardEvent) => {
    if (e.key === "Escape") setOpen(false);
    else if (e.key === "ArrowDown") {
      e.preventDefault();
      setActive((a) => Math.min(a + 1, items.length - 1));
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setActive((a) => Math.max(a - 1, 0));
    } else if (e.key === "Enter") items[active]?.run();
  };

  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center px-4 pt-[12vh]" role="dialog" aria-modal="true" aria-label="Search">
      <button type="button" aria-label="Close search" className="absolute inset-0 bg-black/40 backdrop-blur-[2px]" onClick={() => setOpen(false)} />
      <div className="enter relative w-full max-w-lg overflow-hidden rounded-2xl border border-line bg-surface shadow-2xl" onKeyDown={onKey}>
        <input
          ref={input}
          value={text}
          onChange={(e) => setText(e.target.value)}
          placeholder="Search players, teams, pages…"
          aria-label="Search"
          role="combobox"
          aria-expanded="true"
          aria-controls="palette-list"
          aria-activedescendant={items[active] ? `pal-${items[active].key}` : undefined}
          className="w-full border-b border-line bg-transparent px-5 py-4 text-base outline-none placeholder:text-muted"
        />
        <ul id="palette-list" role="listbox" className="max-h-[50vh] overflow-y-auto p-2">
          {items.length === 0 && <li className="px-3 py-6 text-center text-sm text-muted">No matches</li>}
          {items.map((it, i) => (
            <li
              key={it.key}
              id={`pal-${it.key}`}
              role="option"
              aria-selected={i === active}
              onPointerEnter={() => setActive(i)}
              onClick={it.run}
              className={clsx("flex cursor-pointer items-center gap-3 rounded-xl px-3 py-2", i === active && "bg-surface-2")}
            >
              {it.icon}
              <span className="min-w-0 flex-1 truncate text-sm font-medium">{it.label}</span>
              <span className="shrink-0 text-xs text-muted">{it.hint}</span>
            </li>
          ))}
        </ul>
        <div className="flex gap-4 border-t border-line px-4 py-2 text-[11px] text-muted">
          <span>↑↓ move</span><span>↵ open</span><span>esc close</span>
        </div>
      </div>
    </div>
  );
}
