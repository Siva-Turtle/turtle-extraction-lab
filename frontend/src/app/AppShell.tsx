import * as React from "react";
import { NavLink, Outlet, useLocation } from "react-router-dom";
import { Bot, ChevronLeft, ChevronRight, FlaskConical, Moon, ScrollText, Sun, Tags } from "lucide-react";
import { cn } from "../lib/cn";
import { api } from "../lib/api";
import { useTheme } from "./ThemeProvider";

const NAV_ITEMS = [
  { to: "/attributes", label: "Attributes", icon: <Tags className="h-5 w-5" aria-hidden="true" /> },
  { to: "/agents", label: "Agents", icon: <Bot className="h-5 w-5" aria-hidden="true" /> },
  { to: "/test", label: "Test Lab", icon: <FlaskConical className="h-5 w-5" aria-hidden="true" /> },
  { to: "/logs", label: "Logs", icon: <ScrollText className="h-5 w-5" aria-hidden="true" /> },
];

function Wordmark(): React.JSX.Element {
  const { resolved } = useTheme();
  return (
    <span className="flex min-w-0 items-center gap-2" aria-label="Turtle Extraction Lab home">
      <img
        src={resolved === "dark" ? "/turtle-logo-white.svg" : "/turtle-logo-black.svg"}
        alt="Turtle"
        className="h-7 w-auto"
      />
      <span className="truncate font-module text-sm font-bold text-[#1d1d1d] dark:text-[#F0EFEC]">
        extraction lab
      </span>
    </span>
  );
}

function SidebarLink({
  to,
  label,
  icon,
  collapsed,
}: {
  to: string;
  label: string;
  icon: React.ReactNode;
  collapsed: boolean;
}): React.JSX.Element {
  return (
    <NavLink
      to={to}
      title={collapsed ? label : undefined}
      className={({ isActive }) =>
        cn(
          "flex min-h-[44px] w-full items-center gap-3 rounded-lg px-3 font-heading text-sm font-semibold transition-[background-color] duration-[120ms] ease-out motion-reduce:transition-none",
          "focus-visible:outline-2 focus-visible:outline-brand focus-visible:outline-offset-1",
          collapsed && "justify-center gap-0 px-0",
          isActive
            ? "bg-[#2edebe] text-[#1d1d1d] dark:text-[#1d1d1d]"
            : "text-[#4a5058] hover:bg-[#e8fbf6] hover:text-[#1d1d1d] dark:text-[#C3C2B7] dark:hover:bg-white/10 dark:hover:text-[#F0EFEC]",
        )
      }
    >
      {icon}
      <span aria-hidden={collapsed} className={cn("truncate", collapsed && "hidden")}>
        {label}
      </span>
    </NavLink>
  );
}

export function AppShell(): React.JSX.Element {
  const [collapsed, setCollapsed] = React.useState(false);
  const { pathname } = useLocation();
  const { setting, setTheme } = useTheme();
  const [health, setHealth] = React.useState("…");

  React.useEffect(() => {
    api.get("/health").then((r) => setHealth(r.data.status)).catch(() => setHealth("down"));
  }, [pathname]);

  return (
    <div className="flex min-h-svh w-full min-w-0 max-w-[100vw] overflow-x-clip bg-white text-[#1d1d1d] dark:bg-[#161616] dark:text-[#F0EFEC]">
      {/* Sidebar: md and up */}
      <aside
        aria-label="Primary navigation"
        className={cn(
          "sticky top-0 hidden h-svh shrink-0 flex-col border-r border-[#e5e7eb] bg-white transition-[width] duration-150 ease-out md:flex dark:border-white/10 dark:bg-[#1a1a1a]",
          collapsed ? "w-16" : "w-60",
        )}
      >
        <div
          className={cn(
            "flex h-[58px] items-center border-b border-[#e5e7eb] dark:border-white/10",
            collapsed ? "justify-center px-2" : "justify-between px-4",
          )}
        >
          {collapsed ? (
            <button
              type="button"
              onClick={() => setCollapsed(false)}
              aria-label="Expand sidebar"
              className="flex h-11 w-11 items-center justify-center rounded-lg text-[#4a5058] transition-colors duration-[120ms] hover:bg-[#e8fbf6] focus-visible:outline-2 focus-visible:outline-brand dark:text-[#C3C2B7] dark:hover:bg-white/10"
            >
              <ChevronRight className="h-5 w-5" aria-hidden="true" />
            </button>
          ) : (
            <>
              <Wordmark />
              <button
                type="button"
                onClick={() => setCollapsed(true)}
                aria-label="Collapse sidebar"
                className="flex h-11 w-11 items-center justify-center rounded-lg text-[#4a5058] transition-colors duration-[120ms] hover:bg-[#e8fbf6] focus-visible:outline-2 focus-visible:outline-brand dark:text-[#C3C2B7] dark:hover:bg-white/10"
              >
                <ChevronLeft className="h-5 w-5" aria-hidden="true" />
              </button>
            </>
          )}
        </div>
        <nav className="flex-1 overflow-y-auto p-2" aria-label="Sections">
          <ul className="flex flex-col gap-0.5">
            {NAV_ITEMS.map((item) => (
              <li key={item.to}>
                <SidebarLink to={item.to} label={item.label} icon={item.icon} collapsed={collapsed} />
              </li>
            ))}
          </ul>
        </nav>
      </aside>

      {/* Main column */}
      <div className="flex min-h-svh w-full min-w-0 flex-1 flex-col">
        {/* Top bar */}
        <header className="sticky top-0 z-30 flex h-[58px] w-full min-w-0 items-center gap-2 border-b border-[#e5e7eb] bg-white px-3 sm:px-4 dark:border-white/10 dark:bg-[#1a1a1a]">
          <span
            className={cn(
              "hidden overflow-hidden transition-all duration-200 ease-out md:block",
              collapsed ? "max-w-64 opacity-100" : "max-w-0 opacity-0",
            )}
          >
            <Wordmark />
          </span>
          <span className="shrink-0 md:hidden">
            <Wordmark />
          </span>
          <div className="min-w-0 flex-1" />
          <span
            className={cn(
              "hidden items-center gap-1.5 rounded-full px-2.5 py-1 font-heading text-[11px] font-bold uppercase tracking-wide sm:inline-flex",
              health === "ok"
                ? "bg-[#e9f9ef] text-[#15803d] dark:bg-[#22c55e]/15 dark:text-[#4ade80]"
                : "bg-[#fdecec] text-[#b91c1c] dark:bg-[#ef4444]/15 dark:text-[#f87171]",
            )}
            title="Backend API status"
          >
            <span
              aria-hidden="true"
              className={cn("h-1.5 w-1.5 rounded-full", health === "ok" ? "bg-[#22c55e]" : "bg-[#ef4444]")}
            />
            api: {health}
          </span>
          <button
            type="button"
            onClick={() => setTheme(setting === "dark" ? "light" : "dark")}
            aria-label={setting === "dark" ? "Switch to light theme" : "Switch to dark theme"}
            className="flex h-11 w-11 items-center justify-center rounded-full text-[#4a5058] transition-colors duration-[120ms] hover:bg-[#e8fbf6] focus-visible:outline-2 focus-visible:outline-brand dark:text-[#C3C2B7] dark:hover:bg-white/10"
          >
            {setting === "dark" ? (
              <Sun className="h-5 w-5" aria-hidden="true" />
            ) : (
              <Moon className="h-5 w-5" aria-hidden="true" />
            )}
          </button>
        </header>

        {/* Page content */}
        <main className="w-full min-w-0 flex-1 bg-[#eef0f4] px-3 py-4 pb-24 sm:px-4 sm:py-6 md:pb-10 dark:bg-[#161616]">
          <div className="mx-auto w-full min-w-0 max-w-6xl animate-[turtle-tab-panel-in_180ms_ease-out]" key={pathname}>
            <Outlet />
          </div>
        </main>

        {/* Bottom tab bar: below md */}
        <nav
          aria-label="Mobile navigation"
          className="fixed inset-x-0 bottom-0 z-30 border-t border-[#e5e7eb] bg-white pb-[env(safe-area-inset-bottom)] md:hidden dark:border-white/10 dark:bg-[#1a1a1a]"
        >
          <ul className="grid w-full grid-cols-4">
            {NAV_ITEMS.map((t) => (
              <li key={t.to} className="min-w-0">
                <NavLink
                  to={t.to}
                  className={({ isActive }) =>
                    cn(
                      "flex min-h-[56px] min-w-0 flex-col items-center justify-center gap-0.5 px-1 font-heading text-[11px] font-semibold",
                      "focus-visible:outline-2 focus-visible:outline-brand focus-visible:outline-offset-[-2px]",
                      isActive ? "text-[#1d1d1d] dark:text-[#F0EFEC]" : "text-[#8a8f98] dark:text-[#898781]",
                    )
                  }
                >
                  {({ isActive }) => (
                    <>
                      <span className="flex h-6 items-center">{t.icon}</span>
                      <span className="truncate">{t.label}</span>
                      <span className={cn("h-1 w-1 rounded-full", isActive ? "bg-[#2fdebf]" : "bg-transparent")} aria-hidden="true" />
                    </>
                  )}
                </NavLink>
              </li>
            ))}
          </ul>
        </nav>
      </div>
    </div>
  );
}
