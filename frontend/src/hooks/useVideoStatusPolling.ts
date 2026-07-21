"use client";

import { useEffect, useRef } from "react";

const POLLING_INTERVAL_MS = 5000;

interface VideoStatusPollingOptions {
  enabled: boolean;
  refresh: () => Promise<void>;
}

export function useVideoStatusPolling({ enabled, refresh }: VideoStatusPollingOptions) {
  const refreshRef = useRef(refresh);

  useEffect(() => {
    refreshRef.current = refresh;
  }, [refresh]);

  useEffect(() => {
    if (!enabled) return;

    let active = true;
    let running = false;
    let runAgain = false;
    let timer: ReturnType<typeof setTimeout> | undefined;

    const schedule = () => {
      timer = setTimeout(run, POLLING_INTERVAL_MS);
    };

    const run = async () => {
      if (!active) return;
      if (running) {
        runAgain = true;
        return;
      }
      if (timer) clearTimeout(timer);
      running = true;
      try {
        await refreshRef.current();
      } finally {
        running = false;
        if (!active) return;
        if (runAgain) {
          runAgain = false;
          void run();
          return;
        }
        schedule();
      }
    };

    const onVisibilityChange = () => {
      if (document.visibilityState !== "visible") return;
      if (timer) clearTimeout(timer);
      void run();
    };

    schedule();
    document.addEventListener("visibilitychange", onVisibilityChange);
    return () => {
      active = false;
      if (timer) clearTimeout(timer);
      document.removeEventListener("visibilitychange", onVisibilityChange);
    };
  }, [enabled]);
}
