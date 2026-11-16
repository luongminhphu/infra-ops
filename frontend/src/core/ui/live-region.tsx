import { useEffect, useState } from "react";

/**
 * One polite live region for the whole app. Field errors and async results are
 * announced through `announce()` instead of making every message a live region.
 */
let push: ((msg: string) => void) | null = null;

export function announce(message: string): void {
  // Delay slightly so the announcement does not talk over the next focused field.
  window.setTimeout(() => push?.(message), 150);
}

export function LiveRegion() {
  const [message, setMessage] = useState("");
  useEffect(() => {
    push = (msg) => {
      setMessage("");
      window.requestAnimationFrame(() => setMessage(msg));
    };
    return () => {
      push = null;
    };
  }, []);
  return (
    <div className="sr-only" aria-live="polite" aria-atomic="true">
      {message}
    </div>
  );
}
