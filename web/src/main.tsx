import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  RouterProvider,
  createRootRouteWithContext,
  createRoute,
  createRouter,
  lazyRouteComponent,
} from "@tanstack/react-router";
import { type JSX, StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { q } from "./api";
import { Layout } from "./components/Layout";
import { ErrorBox, Skeleton } from "./components/ui";
import "./styles.css";

const queryClient = new QueryClient({
  defaultOptions: { queries: { refetchOnWindowFocus: false, retry: 1 } },
});

const root = createRootRouteWithContext<{ queryClient: QueryClient }>()({ component: Layout });
// Loaders prefetch on link hover (defaultPreload: "intent"), so navigation feels instant.
const overview = createRoute({
  getParentRoute: () => root,
  path: "/",
  loader: ({ context }) => context.queryClient.ensureQueryData(q.league()),
  component: lazyRouteComponent(() => import("./pages/Overview")),
});
const waivers = createRoute({
  getParentRoute: () => root,
  path: "/waivers",
  validateSearch: (s: Record<string, unknown>): { chop?: string } =>
    typeof s.chop === "string" && s.chop ? { chop: s.chop } : {},
  loader: ({ context }) => context.queryClient.ensureQueryData(q.league()),
  component: lazyRouteComponent(() => import("./pages/Waivers")),
});

const page = <P extends string>(path: P, importer: () => Promise<{ default: () => JSX.Element }>, loader?: (qc: QueryClient) => Promise<unknown>) =>
  createRoute({
    getParentRoute: () => root,
    path,
    loader: loader ? ({ context }) => loader(context.queryClient) : undefined,
    component: lazyRouteComponent(importer),
  });
const live = page("/live", () => import("./pages/Live"));
const lineup = page("/lineup", () => import("./pages/Lineup"), (qc) => qc.ensureQueryData(q.league()));
const league = page("/league", () => import("./pages/League"), (qc) => qc.ensureQueryData(q.league()));
const players = page("/players", () => import("./pages/Players"), (qc) => qc.ensureQueryData(q.players()));
const model = page("/model", () => import("./pages/Model"));
const team = createRoute({
  getParentRoute: () => root,
  path: "/teams/$teamId",
  loader: ({ context, params }) =>
    Promise.all([context.queryClient.ensureQueryData(q.team(params.teamId)), context.queryClient.ensureQueryData(q.league())]),
  component: lazyRouteComponent(() => import("./pages/Team")),
});

const router = createRouter({
  routeTree: root.addChildren([overview, waivers, live, lineup, league, team, players, model]),
  context: { queryClient },
  defaultPreload: "intent",
  defaultPreloadStaleTime: 0,
  defaultPendingMs: 150,
  defaultPendingComponent: () => <Skeleton className="h-64 w-full" />,
  defaultErrorComponent: ({ error }) => <ErrorBox error={error} />,
});
declare module "@tanstack/react-router" {
  interface Register {
    router: typeof router;
  }
}

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>
  </StrictMode>,
);
