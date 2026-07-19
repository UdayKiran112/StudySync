import { createContext, useContext, useEffect, useState, type ReactNode } from "react";

interface Settings {
  baseUrl: string;
  apiKey: string;
}

interface SettingsContextValue extends Settings {
  setBaseUrl: (v: string) => void;
  setApiKey: (v: string) => void;
  isConfigured: boolean;
}

const DEFAULT_BASE_URL = "http://localhost:8000";
const STORAGE_KEY = "studysync.settings";

const SettingsContext = createContext<SettingsContextValue | null>(null);

function loadInitial(): Settings {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (raw) {
      const parsed = JSON.parse(raw);
      return {
        baseUrl: parsed.baseUrl || DEFAULT_BASE_URL,
        apiKey: parsed.apiKey || "",
      };
    }
  } catch {
    // ignore corrupt storage
  }
  return { baseUrl: DEFAULT_BASE_URL, apiKey: "" };
}

export function SettingsProvider({ children }: { children: ReactNode }) {
  const [settings, setSettings] = useState<Settings>(loadInitial);

  useEffect(() => {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(settings));
  }, [settings]);

  const value: SettingsContextValue = {
    ...settings,
    setBaseUrl: (v) => setSettings((s) => ({ ...s, baseUrl: v })),
    setApiKey: (v) => setSettings((s) => ({ ...s, apiKey: v })),
    isConfigured: Boolean(settings.baseUrl && settings.apiKey),
  };

  return <SettingsContext.Provider value={value}>{children}</SettingsContext.Provider>;
}

export function useSettings() {
  const ctx = useContext(SettingsContext);
  if (!ctx) throw new Error("useSettings must be used within SettingsProvider");
  return ctx;
}
