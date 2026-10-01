import { useEffect, useState } from "react";
import { Link, Route, Routes, useLocation } from "react-router-dom";
import { api } from "./lib/api";
import Agents from "./pages/Agents";
import Attributes from "./pages/Attributes";
import Logs from "./pages/Logs";
import TestLab from "./pages/TestLab";

const NAV = [
  { to: "/agents", label: "Agents" },
  { to: "/attributes", label: "Attributes" },
  { to: "/test", label: "Test Lab" },
  { to: "/logs", label: "Logs" },
];

export default function App() {
  const { pathname } = useLocation();
  const [health, setHealth] = useState("…");
  useEffect(() => {
    api.get("/health").then((r) => setHealth(r.data.status)).catch(() => setHealth("down"));
  }, []);
  return (
    <div className="min-h-screen">
      <header className="sticky top-0 z-10 border-b border-ink-100 bg-surface">
        <div className="mx-auto flex max-w-6xl items-center gap-6 px-4 py-3">
          <span className="font-display text-xl font-bold">turtle</span>
          <span className="font-module text-sm text-ink-500">extraction lab</span>
          <nav className="ml-4 flex gap-1">
            {NAV.map((n) => (
              <Link
                key={n.to}
                to={n.to}
                className={`rounded-lg px-3 py-2 font-heading text-sm ${
                  pathname === n.to ? "bg-brand-100 text-ink" : "text-ink-500 hover:bg-ink-50"
                }`}
              >
                {n.label}
              </Link>
            ))}
          </nav>
          <span className="ml-auto font-heading text-xs text-ink-400">api: {health}</span>
        </div>
      </header>
      <main className="mx-auto max-w-6xl px-4 py-6">
        <Routes>
          <Route path="/" element={<TestLab />} />
          <Route path="/agents" element={<Agents />} />
          <Route path="/attributes" element={<Attributes />} />
          <Route path="/test" element={<TestLab />} />
          <Route path="/logs" element={<Logs />} />
        </Routes>
      </main>
    </div>
  );
}
