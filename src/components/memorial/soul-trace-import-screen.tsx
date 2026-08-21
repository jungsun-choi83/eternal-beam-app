"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { AuthScreen } from "./auth-screen";
import { HolographicBackground } from "./holographic-background";
import { claimSoulTraceLetter, OrderApiError } from "@/lib/orders-api";
import { getPremiumAccessToken } from "@/lib/premium-auth-token";
import {
  captureSoulTraceHandoff,
  clearSoulTraceHandoff,
  type SoulTraceHandoff,
} from "@/lib/soul-trace-handoff";

/**
 * `/soul-trace/import` — Soul Trace 편지를 이 계정으로 가져오는 화면.
 *
 *   URL: ?traceId=<uuid>&handoff=<불투명 토큰>     ← 편지 본문은 없다
 *
 * ── 이 화면이 하는 일의 순서 ────────────────────────────────────────────────
 *   1. URL 에서 핸드오프를 집어 sessionStorage 로 옮기고 **주소창을 지운다**
 *   2. 로그인돼 있지 않으면 로그인/가입 (핸드오프는 그 왕복을 넘어 살아남는다)
 *   3. 인증되면 POST /api/v1/orders/letter/claim { trace_id, handoff }
 *   4. 성공하면 브라우저에서 원문 토큰을 **즉시 지운다**
 *
 * ── 이 화면이 하지 않는 일 ──────────────────────────────────────────────────
 * 편지 본문을 받지도, 보내지도, 저장하지도 않는다. 본문은 EB 백엔드가 Soul Trace
 * 에서 서버 대 서버로 가져가고, 이 화면은 성공 여부만 안다. 그래서 여기서
 * 편지를 보여 줄 수 없고, 그것이 의도다 — 화면에 띄우려면 본문이 브라우저까지
 * 내려와야 하는데, 그 순간 "본문은 서버 경로로만 이동한다"가 깨진다.
 *
 * 펫도 여기서 만들지 않는다. Soul Trace 만 마친 사용자는 아직 펫이 없고,
 * 편지는 pet_id = NULL 로 들어온다. 펫 연결은 주문 화면에서 따로 한다.
 */

type Phase =
  | { kind: "loading" }
  | { kind: "needsAuth" }
  | { kind: "claiming" }
  | { kind: "done"; letterId: string }
  | { kind: "error"; message: string; recoverable: boolean }
  | { kind: "noHandoff" };

const SOUL_TRACE_URL = "https://soultrace.eternalbeam.com";

/** 서버 오류 코드 → 사용자가 실제로 할 수 있는 행동이 담긴 문장. */
function messageFor(e: unknown): { message: string; recoverable: boolean } {
  const code = e instanceof OrderApiError ? e.code : "";
  if (code === "HANDOFF_CONSUMED" || code === "HANDOFF_INVALID") {
    return {
      // 1회용이라 "다시 시도"가 통하지 않는다 — Soul Trace 에서 새로 시작해야 한다.
      message:
        "이 링크는 만료되었거나 이미 사용되었습니다. Soul Trace 에서 다시 편지를 열어 주세요.",
      recoverable: false,
    };
  }
  if (code === "IMPORT_NOT_CONFIGURED") {
    return {
      message: "지금은 편지를 가져올 수 없습니다. 잠시 후 다시 시도해 주세요.",
      recoverable: true,
    };
  }
  if (code === "SOURCE_UNAVAILABLE") {
    return {
      message: "Soul Trace 에 연결하지 못했습니다. 잠시 후 다시 시도해 주세요.",
      recoverable: true,
    };
  }
  if (code === "SOURCE_LETTER_NOT_FOUND" || code === "SOURCE_BODY_EMPTY") {
    return { message: "편지를 찾을 수 없습니다.", recoverable: false };
  }
  return {
    message: e instanceof OrderApiError ? e.message : "편지를 가져오지 못했습니다.",
    recoverable: true,
  };
}

export function SoulTraceImportScreen() {
  const [phase, setPhase] = useState<Phase>({ kind: "loading" });
  const handoffRef = useRef<SoulTraceHandoff | null>(null);
  // 토큰은 1회용이다. StrictMode 의 이중 마운트나 리렌더로 claim 이 두 번 나가면
  // 두 번째는 반드시 실패한다 — 정당한 사용자에게 "이미 사용됨"을 보여 주게 된다.
  const claimedRef = useRef(false);

  const claim = useCallback(async () => {
    const handoff = handoffRef.current;
    if (!handoff || claimedRef.current) return;

    const auth = await getPremiumAccessToken();
    if (!auth.token) {
      setPhase({ kind: "needsAuth" });
      return;
    }

    claimedRef.current = true;
    setPhase({ kind: "claiming" });
    try {
      const r = await claimSoulTraceLetter({
        traceId: handoff.traceId,
        handoff: handoff.handoff,
        accessToken: auth.token,
      });
      // 성공했다 — 원문 토큰을 더 들고 있을 이유가 없다.
      clearSoulTraceHandoff();
      handoffRef.current = null;
      setPhase({ kind: "done", letterId: r.letterId });
    } catch (e) {
      const { message, recoverable } = messageFor(e);
      if (!recoverable) {
        // 토큰이 죽었다. 남겨 두면 쓸모없는 자격 증명이 탭에 계속 굴러다닌다.
        clearSoulTraceHandoff();
        handoffRef.current = null;
      } else {
        // 일시적 실패 — 사용자가 다시 누를 수 있어야 한다.
        claimedRef.current = false;
      }
      setPhase({ kind: "error", message, recoverable });
    }
  }, []);

  useEffect(() => {
    const captured = captureSoulTraceHandoff(window.location.search);
    if (!captured) {
      setPhase({ kind: "noHandoff" });
      return;
    }
    handoffRef.current = captured;
    void claim();
  }, [claim]);

  if (phase.kind === "needsAuth") {
    // 로그인 왕복 동안 핸드오프는 sessionStorage 에 남아 있다.
    return <AuthScreen onAuthComplete={() => void claim()} />;
  }

  const shell =
    "relative flex min-h-screen flex-col items-center justify-center px-6 text-center text-[#EDE3CE]";

  return (
    <div className={shell} style={{ background: "#050505" }}>
      <HolographicBackground />
      <div className="relative z-[2] w-full max-w-md space-y-5">
        {phase.kind === "loading" || phase.kind === "claiming" ? (
          <>
            <p className="text-xs uppercase tracking-[0.32em] text-[#C9A227]">SOUL TRACE</p>
            <h1 className="text-lg font-light">편지를 가져오는 중…</h1>
            <p className="text-sm font-light text-white/50">
              원본은 Soul Trace 에 그대로 남고, 주문에 쓰일 사본만 안전하게 옮깁니다.
            </p>
          </>
        ) : null}

        {phase.kind === "done" ? (
          <>
            <p className="text-xs uppercase tracking-[0.32em] text-[#C9A227]">SOUL TRACE</p>
            <h1 className="text-lg font-light">편지를 가져왔습니다</h1>
            <p className="text-sm font-light text-white/60">
              이제 실물 편지나 메모리 박스를 주문할 때 이 편지가 그대로 인쇄됩니다.
              아이를 아직 만들지 않았다면 먼저 아이의 영상을 만들어 주세요.
            </p>
            <button
              type="button"
              onClick={() => window.location.assign("/")}
              className="mt-2 w-full rounded-2xl bg-[#b89a2e] px-5 py-3.5 text-base font-light text-black transition hover:bg-[#a88928]"
            >
              계속하기
            </button>
          </>
        ) : null}

        {phase.kind === "error" ? (
          <>
            <p className="text-xs uppercase tracking-[0.32em] text-[#C9A227]">SOUL TRACE</p>
            <h1 className="text-lg font-light">편지를 가져오지 못했습니다</h1>
            <p className="text-sm font-light text-white/60">{phase.message}</p>
            {phase.recoverable ? (
              <button
                type="button"
                onClick={() => void claim()}
                className="mt-2 w-full rounded-2xl bg-[#b89a2e] px-5 py-3.5 text-base font-light text-black transition hover:bg-[#a88928]"
              >
                다시 시도
              </button>
            ) : (
              <a
                href={SOUL_TRACE_URL}
                className="mt-2 block w-full rounded-2xl border border-[rgba(201,162,39,0.4)] px-5 py-3.5 text-base font-light text-[#F5E6B8] transition hover:bg-white/5"
              >
                Soul Trace 로 이동
              </a>
            )}
          </>
        ) : null}

        {phase.kind === "noHandoff" ? (
          <>
            <p className="text-xs uppercase tracking-[0.32em] text-[#C9A227]">SOUL TRACE</p>
            <h1 className="text-lg font-light">가져올 편지가 없습니다</h1>
            <p className="text-sm font-light text-white/60">
              Soul Trace 결과 화면에서 &ldquo;이터널빔으로 계속하기&rdquo;를 눌러 주세요.
            </p>
            <a
              href={SOUL_TRACE_URL}
              className="mt-2 block w-full rounded-2xl border border-[rgba(201,162,39,0.4)] px-5 py-3.5 text-base font-light text-[#F5E6B8] transition hover:bg-white/5"
            >
              Soul Trace 로 이동
            </a>
          </>
        ) : null}
      </div>
    </div>
  );
}
