import axios, { AxiosError } from "axios";
import type { ApiErrorBody } from "./types";

const STORAGE_KEY = "studysync.settings";

function readSettings(): { baseUrl: string; apiKey: string } {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (raw) {
      const parsed = JSON.parse(raw);
      return { baseUrl: parsed.baseUrl || "http://localhost:8000", apiKey: parsed.apiKey || "" };
    }
  } catch {
    // ignore
  }
  return { baseUrl: "http://localhost:8000", apiKey: "" };
}

export const apiClient = axios.create();

apiClient.interceptors.request.use((config) => {
  const { baseUrl, apiKey } = readSettings();
  config.baseURL = baseUrl.replace(/\/+$/, "");
  config.headers = config.headers ?? {};
  if (apiKey) {
    config.headers["X-API-Key"] = apiKey;
  }
  return config;
});

/** Extracts a human-readable message from a FastAPI error response. */
export function extractErrorMessage(error: unknown): string {
  if (axios.isAxiosError(error)) {
    const err = error as AxiosError<ApiErrorBody>;
    if (err.response) {
      const detail = err.response.data?.detail;
      if (typeof detail === "string") return detail;
      if (Array.isArray(detail)) {
        return detail
          .map((d) => (d.loc ? `${d.loc[d.loc.length - 1]}: ${d.msg}` : d.msg))
          .join("; ");
      }
      if (err.response.status === 401) return "Invalid or missing API key.";
      if (err.response.status === 503) return "Server API key is not configured.";
      return `Request failed (${err.response.status})`;
    }
    if (err.request) return "Could not reach the server. Check the API base URL and that the backend is running.";
  }
  if (error instanceof Error) return error.message;
  return "Something went wrong.";
}
