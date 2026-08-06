import { useEffect, useRef } from "react";
import { useQueryClient } from "@tanstack/react-query";

const STORAGE_KEY = "studysync.settings";

export interface RenewalEvent {
  student_id: number;
  name: string | null;
}

/**
 * Opens one Server-Sent Events connection to /api/realtime/stream and
 * reacts to live backend events:
 *
 *   - "attendance"  -> invalidates every attendance query so the page
 *                      refetches the instant a punch is written (no more
 *                      waiting for the 5s poll).
 *   - "renewal"     -> calls opts.onRenewal so the desk can be prompted.
 *
 * The API key is sent as a normal header, so this uses fetch + a streaming
 * reader instead of EventSource (which can't set custom headers). Reconnects
 * automatically with exponential backoff if the stream drops.
 */
export function useRealtimeEvents(opts: {
  onAttendance?: () => void;
  onRenewal?: (event: RenewalEvent) => void;
}): void {
  const queryClient = useQueryClient();
  const cbRef = useRef(opts);
  cbRef.current = opts;
  const queryClientRef = useRef(queryClient);
  queryClientRef.current = queryClient;

  useEffect(() => {
    let disposed = false;
    let retryTimer: number | undefined;
    let retryDelay = 1000;
    let controller: AbortController | undefined;

    function readSettings(): { baseUrl: string; apiKey: string } {
      try {
        const raw = localStorage.getItem(STORAGE_KEY);
        if (raw) {
          const parsed = JSON.parse(raw) as Record<string, unknown>;
          const rawUrl = String(parsed.baseUrl ?? "").replace(/\/+$/, "");
          let sanitizedUrl = "";
          if (rawUrl) {
            try {
              const u = new URL(rawUrl);
              if (u.protocol === "http:" || u.protocol === "https:") {
                sanitizedUrl = u.origin;
              }
            } catch {
              // not an absolute URL — treat as empty (same-origin)
            }
          }
          return {
            baseUrl: sanitizedUrl,
            apiKey: String(parsed.apiKey ?? ""),
          };
        }
      } catch {
        // ignore corrupt storage
      }
      return { baseUrl: "", apiKey: "" };
    }

    async function connect() {
      const { baseUrl, apiKey } = readSettings();
      if (!apiKey) {
        // Not configured yet — retry occasionally; the Settings page
        // persists to localStorage, which the next attempt will pick up.
        if (!disposed) retryTimer = window.setTimeout(connect, 5000);
        return;
      }
      const streamUrl = baseUrl
        ? `${baseUrl}/api/realtime/stream`
        : "/api/realtime/stream";
      controller = new AbortController();
      try {
        const res = await fetch(streamUrl, {
          headers: { "X-API-Key": apiKey },
          signal: controller.signal,
        });
        if (!res.ok || !res.body) {
          throw new Error(`SSE connection failed (${res.status})`);
        }
        retryDelay = 1000; // connected — reset backoff
        const reader = res.body.getReader();
        const decoder = new TextDecoder();
        let buffer = "";
        let eventName = "";
        let dataStr = "";
        for (;;) {
          const { done, value } = await reader.read();
          if (done) break;
          buffer += decoder.decode(value, { stream: true });
          let sep: number;
          while ((sep = buffer.indexOf("\n\n")) !== -1) {
            const frame = buffer.slice(0, sep);
            buffer = buffer.slice(sep + 2);
            for (const line of frame.split("\n")) {
              const text = line.trim();
              if (text.startsWith("event:")) {
                eventName = text.slice(6).trim();
              } else if (text.startsWith("data:")) {
                dataStr = text.slice(5).trim();
              }
            }
            if (eventName) {
              let payload: unknown = dataStr;
              if (dataStr) {
                try {
                  payload = JSON.parse(dataStr) as unknown;
                } catch {
                  // keep the raw string
                }
              }
              const cb = cbRef.current;
              if (eventName === "attendance") {
                queryClientRef.current.invalidateQueries({
                  queryKey: ["attendance"],
                });
                cb.onAttendance?.();
              } else if (eventName === "renewal") {
                cb.onRenewal?.(payload as RenewalEvent);
              }
              eventName = "";
              dataStr = "";
            }
          }
        }
      } catch {
        // stream closed or errored — reconnect below
      }
      if (disposed || controller?.signal.aborted) return;
      retryTimer = window.setTimeout(connect, retryDelay);
      retryDelay = Math.min(retryDelay * 2, 15000);
    }

    void connect();
    return () => {
      disposed = true;
      controller?.abort();
      if (retryTimer) window.clearTimeout(retryTimer);
    };
  }, []);
}
