"use client";

import { useEffect, useRef, useState } from "react";
import { hasSession } from "@/lib/supabase-auth";

/** SoulTrace 공식 금속 두 꽃잎 심볼 — 다시 그리지 않는다. */
export const SPLASH_MARK_SRC = "/brand/eternal-beam-mark.png";
const FADE_MS = 600;

type SplashContinue = "getStarted" | "home";

interface SplashScreenProps {
  language?: string;
  onContinue: (next: SplashContinue) => void;
}

function prefersReducedMotion(): boolean {
  if (typeof window === "undefined") return true;
  return window.matchMedia("(prefers-reduced-motion: reduce)").matches;
}

/**
 * 앱 초기화 · 세션 확인용 스플래시.
 * 페이드인(600ms)과 세션 조회를 동시에 돌리고, 둘 다 끝나면 바로 전환한다.
 * 임의의 추가 대기 시간은 없다.
 */
export function SplashScreen({ language = "ko", onContinue }: SplashScreenProps) {
  const [error, setError] = useState(false);
  const [attempt, setAttempt] = useState(0);
  const onContinueRef = useRef(onContinue);
  onContinueRef.current = onContinue;
  const en = language === "en";

  useEffect(() => {
    let cancelled = false;
    const fadeMs = prefersReducedMotion() ? 0 : FADE_MS;
    const fade = new Promise<void>((resolve) => {
      if (fadeMs === 0) {
        resolve();
        return;
      }
      window.setTimeout(resolve, fadeMs);
    });

    void (async () => {
      try {
        await hasSession();
        await fade;
        if (cancelled) return;
        // 계정 만들기는 홈 다음이다. 세션이 있든 없든 먼저 홈을 보여 준다.
        onContinueRef.current("home");
      } catch {
        await fade;
        if (cancelled) return;
        setError(true);
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [attempt]);

  return (
    <div
      data-screen="splash"
      className="eb-splash"
      role="status"
      aria-label="Eternal Beam"
      aria-busy={!error}
    >
      <div className="eb-splash__lockup">
        <img
          src={SPLASH_MARK_SRC}
          alt=""
          width={465}
          height={316}
          className="eb-splash__mark"
          draggable={false}
          decoding="sync"
        />
        <p className="eb-splash__wordmark">Eternal Beam</p>
        {error ? (
          <div className="eb-splash__error">
            <p className="eb-splash__error-copy">
              {en ? "Couldn't start." : "시작하지 못했습니다."}
            </p>
            <button
              type="button"
              className="eb-splash__retry"
              onClick={() => {
                setError(false);
                setAttempt((n) => n + 1);
              }}
            >
              {en ? "Try again" : "다시 시도"}
            </button>
          </div>
        ) : null}
      </div>
    </div>
  );
}
