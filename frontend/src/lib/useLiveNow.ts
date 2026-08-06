import { useEffect, useState } from "react";

/**
 * Returns the current wall-clock time, re-rendering the caller once a second.
 * Use in small leaf components only (a clock, an open-session cell) so the
 * per-second tick never re-renders an entire page.
 */
export function useLiveNow() {
  const [now, setNow] = useState(() => new Date());
  useEffect(() => {
    const timer = window.setInterval(() => setNow(new Date()), 1000);
    return () => window.clearInterval(timer);
  }, []);
  return now;
}
