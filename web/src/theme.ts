import { useEffect, useState } from "react";

export type ThemeName = "light" | "dark";

const STORAGE_KEY = "halo-theme";

export function useTheme(): { theme: ThemeName; setTheme: (theme: ThemeName) => void } {
  const [pinned, setPinned] = useState(() => readStored() != null);
  const [theme, setThemeState] = useState<ThemeName>(currentTheme);

  useEffect(() => {
    document.documentElement.dataset.theme = theme;
  }, [theme]);

  useEffect(() => {
    if (pinned) return;
    const media = window.matchMedia("(prefers-color-scheme: dark)");
    const apply = () => setThemeState(media.matches ? "dark" : "light");
    media.addEventListener("change", apply);
    return () => media.removeEventListener("change", apply);
  }, [pinned]);

  return {
    theme,
    setTheme: (next) => {
      localStorage.setItem(STORAGE_KEY, next);
      setPinned(true);
      setThemeState(next);
    },
  };
}

function currentTheme(): ThemeName {
  return readStored() ?? (window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light");
}

function readStored(): ThemeName | null {
  try {
    const value = localStorage.getItem(STORAGE_KEY);
    return value === "light" || value === "dark" ? value : null;
  } catch {
    return null;
  }
}
