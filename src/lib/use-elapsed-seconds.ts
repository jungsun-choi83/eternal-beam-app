import { useEffect, useState } from "react";

/**
 * WASM 누끼가 메인 스레드를 막아도 초가 올라가도록 Worker에서 타이머.
 */
export function useElapsedSeconds(active: boolean): number {
  const [seconds, setSeconds] = useState(0);

  useEffect(() => {
    if (!active) {
      setSeconds(0);
      return;
    }

    setSeconds(0);
    const workerCode = `let s=0;setInterval(()=>{self.postMessage(++s);},1000);`;
    const blob = new Blob([workerCode], { type: "application/javascript" });
    const url = URL.createObjectURL(blob);
    const worker = new Worker(url);
    worker.onmessage = (ev: MessageEvent<number>) => {
      setSeconds(typeof ev.data === "number" ? ev.data : 0);
    };
    return () => {
      worker.terminate();
      URL.revokeObjectURL(url);
    };
  }, [active]);

  return seconds;
}
