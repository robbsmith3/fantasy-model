import { useQuery } from "@tanstack/react-query";
import { Link, Outlet } from "@tanstack/react-router";
import clsx from "clsx";
import { type ReactNode, useEffect, useState } from "react";
import { q } from "../api";
import { Logo } from "./brand";

const I = (d: string) => (
  <svg viewBox="0 0 24 24" className="size-[18px]" fill="none" stroke="currentColor" strokeWidth={1.8}
    strokeLinecap="round" strokeLinejoin="round" aria-hidden>
    <path d={d} />
  </svg>
);
const ICONS = {
  overview: I("M3 12l9-8 9 8M5 10v10h14V10"),
  waivers: I("M12 3v18M7 8h7.5a3 3 0 010 6H8.5a3 3 0 000 6H17"),
  live: I("M3 12h4l3-8 4 16 3-8h4"),
  lineup: I("M4 6h16M4 12h16M4 18h10"),
  league: I("M8 21h8M12 17v4M7 4h10v5a5 5 0 01-10 0V4zM17 6h3a3 3 0 01-3 4M7 6H4a3 3 0 003 4"),
  players: I("M16 11a4 4 0 10-8 0 4 4 0 008 0zM4 21a8 8 0 0116 0"),
  model: I("M4 20V10M10 20V4M16 20v-7M22 20H2"),
};

const NAV: { to: string; label: string; icon: ReactNode; mobile: boolean }[] = [
  { to: "/", label: "Overview", icon: ICONS.overview, mobile: true },
  { to: "/waivers", label: "Waivers", icon: ICONS.waivers, mobile: true },
  { to: "/live", label: "Live", icon: ICONS.live, mobile: true },
  { to: "/lineup", label: "Lineup", icon: ICONS.lineup, mobile: true },
  { to: "/league", label: "League", icon: ICONS.league, mobile: true },
  { to: "/players", label: "Players", icon: ICONS.players, mobile: false },
  { to: "/model", label: "Model", icon: ICONS.model, mobile: false },
];

type Theme = "system" | "light" | "dark";
function useTheme(): [Theme, () => void] {
  const [theme, setTheme] = useState<Theme>(() => {
    const t = document.documentElement.dataset.theme;
    return t === "light" || t === "dark" ? t : "system";
  });
  useEffect(() => {
    const el = document.documentElement;
    if (theme === "system") delete el.dataset.theme;
    else el.dataset.theme = theme;
    try {
      if (theme === "system") localStorage.removeItem("theme");
      else localStorage.setItem("theme", theme);
    } catch {
      /* storage unavailable: theme still applies for this visit */
    }
  }, [theme]);
  const next = () => setTheme((t) => (t === "system" ? "dark" : t === "dark" ? "light" : "system"));
  return [theme, next];
}

function ago(iso: string) {
  const s = (Date.now() - new Date(iso.replace(" ", "T")).getTime()) / 1000;
  if (!Number.isFinite(s)) return "";
  if (s < 90) return "just now";
  if (s < 5400) return `${Math.round(s / 60)}m ago`;
  if (s < 129600) return `${Math.round(s / 3600)}h ago`;
  return `${Math.round(s / 86400)}d ago`;
}

function Freshness() {
  const { data } = useQuery(q.meta());
  const last = data?.runs.find((r) => r.job !== "history");
  if (!last) return null;
  const failed = data?.runs.slice(0, 5).some((r) => r.ok === false);
  return (
    <span className="inline-flex items-center gap-1.5 text-xs text-muted" title={`Last job: ${last.id}`}>
      <span className={clsx("size-1.5 rounded-full", failed ? "bg-critical" : "bg-good")} />
      {last.job.replace("_", " ")} · {ago(last.started)}
      {failed && <span className="text-critical">· a recent job failed</span>}
    </span>
  );
}

export function Layout() {
  const [theme, cycle] = useTheme();
  const linkCls = "flex items-center gap-3 rounded-lg px-3 py-2 text-sm text-ink-2 hover:bg-surface-2 hover:text-ink";
  return (
    <div className="min-h-dvh md:grid md:grid-cols-[220px_1fr]">
      <aside className="sticky top-0 hidden h-dvh flex-col border-r border-line bg-surface px-3 py-5 md:flex">
        <div className="mb-7 flex items-center gap-2.5 px-2">
          <Logo />
          <div>
            <div className="text-[15px] font-semibold tracking-tight">Chop Desk</div>
            <div className="text-xs text-muted">Guillotine FAAB model</div>
          </div>
        </div>
        <nav className="flex flex-col gap-0.5" aria-label="Main">
          {NAV.map((n) => (
            <Link
              key={n.to}
              to={n.to}
              className={linkCls}
              activeOptions={{ exact: n.to === "/" }}
              activeProps={{ className: "bg-surface-2 !text-ink font-medium" }}
            >
              {n.icon}
              {n.label}
            </Link>
          ))}
        </nav>
        <div className="mt-auto space-y-3 px-3">
          <Freshness />
          <button onClick={cycle} className="text-xs text-ink-2 hover:text-ink">
            Theme: {theme}
          </button>
        </div>
      </aside>

      <div className="min-w-0">
        <header className="sticky top-0 z-30 flex items-center justify-between border-b border-line bg-page/85 px-4 py-3 backdrop-blur md:hidden">
          <span className="flex items-center gap-2 font-semibold tracking-tight"><Logo className="size-6" />Chop Desk</span>
          <div className="flex items-center gap-3">
            <Link to="/players" className="text-ink-2" aria-label="Players">{ICONS.players}</Link>
            <Link to="/model" className="text-ink-2" aria-label="Model">{ICONS.model}</Link>
            <button onClick={cycle} className="text-xs text-ink-2" aria-label={`Theme: ${theme}`}>
              {theme === "dark" ? "Dark" : theme === "light" ? "Light" : "Auto"}
            </button>
          </div>
        </header>
        <main className="mx-auto max-w-6xl px-4 pb-28 pt-6 md:px-8 md:pb-12 md:pt-8">
          <Outlet />
        </main>
      </div>

      <nav
        aria-label="Main"
        className="fixed inset-x-0 bottom-0 z-30 grid grid-cols-5 border-t border-line bg-surface/95 pb-[env(safe-area-inset-bottom)] backdrop-blur md:hidden"
      >
        {NAV.filter((n) => n.mobile).map((n) => (
          <Link
            key={n.to}
            to={n.to}
            activeOptions={{ exact: n.to === "/" }}
            className="flex flex-col items-center gap-0.5 py-2 text-[11px] text-muted"
            activeProps={{ className: "!text-accent" }}
          >
            {n.icon}
            {n.label}
          </Link>
        ))}
      </nav>
    </div>
  );
}
