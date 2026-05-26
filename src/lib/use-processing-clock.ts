import { useEffect, useState } from "react";

export interface ProcessingClock {
  seconds: number;
  tick: number;
}

export function useProcessingClock(active: boolean): ProcessingClock {
  const [clock, setClock] = useState<ProcessingClock>({ seconds: 0, tick: 0 });

  useEffect(() => {
    if (!active) {
      setClock({ seconds: 0, tick: 0 });
      return;
    }

    setClock({ seconds: 0, tick: 0 });

    let fallbackId: ReturnType<typeof setInterval> | null = null;
    let worker: Worker | null = null;

    const startFallback = () => {
      let s = 0;
      fallbackId = setInterval(() => {
        s += 1;
        setClock({ seconds: s, tick: s });
      }, 1000);
    };

    try {
      worker = new Worker("/processing-timer-worker.js");
      worker.onmessage = (ev: MessageEvent<ProcessingClock>) => {
        const d = ev.data;
        if (d && typeof d.seconds === "number") {
          setClock({ seconds: d.seconds, tick: d.tick ?? d.seconds });
        }
      };
      worker.onerror = () => {
        worker?.terminate();
        worker = null;
        startFallback();
      };
    } catch {
      startFallback();
    }

    return () => {
      worker?.terminate();
      if (fallbackId) clearInterval(fallbackId);
    };
  }, [active]);

  return clock;
}
