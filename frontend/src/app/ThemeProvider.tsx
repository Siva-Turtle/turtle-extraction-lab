import * as React from "react";

export type ThemeSetting = "light" | "dark";
export type ResolvedTheme = "light" | "dark";

const STORAGE_KEY = "turtle-lab-theme";

function systemTheme(): ResolvedTheme {
  if (typeof window !== "undefined" && typeof window.matchMedia === "function") {
    return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
  }
  return "light";
}

function readSetting(): ThemeSetting {
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (raw === "light" || raw === "dark") return raw;
  } catch {
    // Storage unavailable: fall through to the OS default.
  }
  return systemTheme();
}

function applyTheme(resolved: ResolvedTheme): void {
  document.documentElement.classList.toggle("dark", resolved === "dark");
  document.documentElement.style.colorScheme = resolved;
}

interface ThemeContextValue {
  setting: ThemeSetting;
  resolved: ResolvedTheme;
  setTheme: (next: ThemeSetting) => void;
}

const ThemeContext = React.createContext<ThemeContextValue | null>(null);

export function ThemeProvider({ children }: { children: React.ReactNode }): React.JSX.Element {
  const [setting, setSettingState] = React.useState<ThemeSetting>(readSetting);
  const resolved: ResolvedTheme = setting;

  React.useEffect(() => {
    applyTheme(resolved);
  }, [resolved]);

  const setTheme = React.useCallback((next: ThemeSetting): void => {
    setSettingState(next);
    try {
      window.localStorage.setItem(STORAGE_KEY, next);
    } catch {
      // Storage unavailable: the in-memory choice still applies this session.
    }
  }, []);

  const value = React.useMemo<ThemeContextValue>(
    () => ({ setting, resolved, setTheme }),
    [setting, resolved, setTheme],
  );
  return <ThemeContext.Provider value={value}>{children}</ThemeContext.Provider>;
}

export function useTheme(): ThemeContextValue {
  const ctx = React.useContext(ThemeContext);
  if (!ctx) throw new Error("useTheme must be used inside ThemeProvider");
  return ctx;
}
