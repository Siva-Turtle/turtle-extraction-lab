import * as React from "react";
import { Navigate, useLocation } from "react-router-dom";
import Agents from "../pages/Agents";
import Attributes from "../pages/Attributes";
import Logs from "../pages/Logs";
import Review from "../pages/Review";
import TestLab from "../pages/TestLab";

type ModuleId = "test" | "agents" | "attributes" | "logs" | "review";

function moduleOf(pathname: string): ModuleId | null {
  if (pathname === "/agents") return "agents";
  if (pathname === "/attributes") return "attributes";
  if (pathname === "/logs") return "logs";
  if (pathname === "/review") return "review";
  if (pathname === "/" || pathname === "/test") return "test";
  return null;
}

/**
 * Keep-alive wrapper for the five module pages: each visited page is
 * rendered exactly once and hidden (not unmounted) when inactive, so
 * module state (filters, form input, scroll) survives tab switches.
 *
 * Tradeoff: the entry animation plays only on first mount, not on every
 * navigation — state preservation wins over replaying the transition.
 */
export function ModulesView(): React.JSX.Element {
  const { pathname } = useLocation();
  const active = moduleOf(pathname);

  // Lazy-mount: only the active page renders on first load (avoids 5x
  // queries); every visited page stays mounted afterwards.
  const [visited, setVisited] = React.useState<Set<ModuleId>>(
    () => new Set<ModuleId>([active ?? "test"]),
  );
  React.useEffect(() => {
    if (active === null) return;
    setVisited((prev) => {
      if (prev.has(active)) return prev;
      const next = new Set(prev);
      next.add(active);
      return next;
    });
  }, [active]);

  // Per-module window scroll restore.
  const pos = React.useRef<Record<string, number>>({});
  const prev = React.useRef<ModuleId>(active ?? "test");
  React.useEffect(() => {
    if (typeof window === "undefined" || active === null) return;
    pos.current[prev.current] = window.scrollY;
    window.scrollTo(0, pos.current[active] ?? 0);
    prev.current = active;
  }, [active]);

  if (active === null) return <Navigate to="/" replace />;

  return (
    <div className="mx-auto w-full min-w-0 max-w-6xl animate-[turtle-tab-panel-in_180ms_ease-out]">
      {visited.has("test") && (
        <div className={active === "test" ? "" : "hidden"}>
          <TestLab />
        </div>
      )}
      {visited.has("agents") && (
        <div className={active === "agents" ? "" : "hidden"}>
          <Agents />
        </div>
      )}
      {visited.has("attributes") && (
        <div className={active === "attributes" ? "" : "hidden"}>
          <Attributes />
        </div>
      )}
      {visited.has("logs") && (
        <div className={active === "logs" ? "" : "hidden"}>
          <Logs />
        </div>
      )}
      {visited.has("review") && (
        <div className={active === "review" ? "" : "hidden"}>
          <Review />
        </div>
      )}
    </div>
  );
}
