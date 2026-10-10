import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { de } from "./de";
import { en } from "./en";
import type { Catalog } from "./types";

const STORAGE_KEY = "halo-language";

export const catalogs: Record<string, Catalog> = { en, de };

interface I18nValue {
  language: string;
  catalog: Catalog;
  languages: string[];
  setLanguage: (language: string) => void;
  acceptDefault: (language: string | undefined) => void;
}

const I18nContext = createContext<I18nValue | null>(null);

export function I18nProvider({ children }: { children: ReactNode }) {
  const stored = readStoredLanguage();
  const [pinned, setPinned] = useState(stored != null);
  const [language, setLanguageState] = useState(stored ?? "en");

  const acceptDefault = useCallback((next: string | undefined) => {
    if (pinned || !next || !catalogs[next]) return;
    setLanguageState(next);
  }, [pinned]);

  useEffect(() => {
    if (pinned) return;
    let cancelled = false;
    fetch("/api/health")
      .then((response) => response.json())
      .then((body: { language?: string }) => {
        if (!cancelled) acceptDefault(body.language);
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, [pinned, acceptDefault]);

  useEffect(() => {
    const catalog = catalogs[language] ?? catalogs.en;
    document.documentElement.lang = language;
    document.title = catalog.product;
  }, [language]);

  const setLanguage = useCallback((next: string) => {
    if (!catalogs[next]) return;
    localStorage.setItem(STORAGE_KEY, next);
    setPinned(true);
    setLanguageState(next);
  }, []);

  const value = useMemo<I18nValue>(() => ({
    language,
    catalog: catalogs[language] ?? catalogs.en,
    languages: Object.keys(catalogs),
    setLanguage,
    acceptDefault,
  }), [language, setLanguage, acceptDefault]);

  return <I18nContext.Provider value={value}>{children}</I18nContext.Provider>;
}

export function useI18n(): I18nValue {
  const value = useContext(I18nContext);
  if (!value) throw new Error("useI18n must be used inside I18nProvider");
  return value;
}

export function fill(template: string, vars: Record<string, string | number>): string {
  return template.replace(/\{(\w+)\}/g, (_, key: string) => String(vars[key] ?? ""));
}

export function translateError(message: string, errors: Record<string, string>): string {
  const split = message.indexOf("|");
  const code = split === -1 ? message : message.slice(0, split);
  const detail = split === -1 ? "" : message.slice(split + 1);
  const template = errors[code];
  if (!template) return message;
  return template.replaceAll("{detail}", detail);
}

function readStoredLanguage(): string | null {
  try {
    const value = localStorage.getItem(STORAGE_KEY);
    return value && catalogs[value] ? value : null;
  } catch {
    return null;
  }
}
