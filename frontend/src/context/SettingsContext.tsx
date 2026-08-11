import {
  createContext,
  useContext,
  useEffect,
  useState,
  type ReactNode,
} from "react";

interface Settings {
  baseUrl: string;
  apiKey: string;
}

interface SettingsContextValue extends Settings {
  setBaseUrl: (v: string) => void;
  setApiKey: (v: string) => void;
  /** Blanks the API key (keeps the base URL). Persists as an empty value. */
  clearApiKey: () => void;
  /** Removes the whole settings entry from localStorage and resets to defaults. */
  clearSettings: () => void;
  isConfigured: boolean;
}

// Production is served behind a reverse proxy (Caddy) that also proxies
// /api/*, so an empty VITE_API_BASE_URL means "same origin as the page"
// and requires zero configuration on any client machine. Dev builds leave
// the variable unset and fall back to the direct FastAPI dev server.
const DEFAULT_BASE_URL =
  import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000";
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
  return {
    baseUrl: DEFAULT_BASE_URL,
    apiKey: "",
  };
}

export function SettingsProvider({ children }: { children: ReactNode }) {
  const [settings, setSettings] = useState<Settings>(loadInitial);

  // Never persist an empty/cleared key: as soon as there is no API key the
  // entry is removed from localStorage entirely, so a cleared setup (or a
  // sign-out) leaves no secret behind for anyone with the browser profile.
  useEffect(() => {
    if (!settings.apiKey) {
      try {
        localStorage.removeItem(STORAGE_KEY);
      } catch {
        // ignore storage errors
      }
      return;
    }
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(settings));
    } catch {
      // ignore quota / security errors
    }
  }, [settings]);

  const value: SettingsContextValue = {
    ...settings,
    setBaseUrl: (v) => {
      const trimmed = v.trim();
      try {
        const u = new URL(trimmed);
        if (u.protocol === "http:" || u.protocol === "https:") {
          setSettings((s) => ({ ...s, baseUrl: u.origin }));
          return;
        }
      } catch { /* invalid URL, fall through */ }
      setSettings((s) => ({ ...s, baseUrl: trimmed }));
    },
    setApiKey: (v) => setSettings((s) => ({ ...s, apiKey: v })),
    clearApiKey: () => setSettings((s) => ({ ...s, apiKey: "" })),
    clearSettings: () => {
      try {
        localStorage.removeItem(STORAGE_KEY);
      } catch {
        // ignore storage errors
      }
      setSettings({ baseUrl: DEFAULT_BASE_URL, apiKey: "" });
    },
    isConfigured: Boolean(settings.apiKey),
  };

  return (
    <SettingsContext.Provider value={value}>
      {children}
    </SettingsContext.Provider>
  );
}

export function useSettings() {
  const ctx = useContext(SettingsContext);
  if (!ctx) throw new Error("useSettings must be used within SettingsProvider");
  return ctx;
}
