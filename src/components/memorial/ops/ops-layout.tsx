"use client";

/**
 * Ops 워크스페이스 셸 — **모든 Ops 화면이 이것을 쓴다.**
 *
 * ── 왜 인증을 여기로 모으는가 ───────────────────────────────────────────────
 * 세 화면이 각자 같은 코드를 갖고 있었다: 토큰 읽기, deriveOpsPhase, AuthScreen
 * 인라인, 권한 없음 문구. 네 벌이면 한 곳만 고쳐지는 날이 오고, 그 한 곳이
 * 인가 관련이면 조용히 열린 문이 된다.
 *
 * ⚠️ **인가를 약화하지 않는다.** 판정은 여전히 서버가 한다
 * (JWT + SHAKER_OPS_USER_IDS). 이 셸은 서버가 돌려준 결과(403/401)를 보고
 * 어떤 화면을 그릴지만 정한다 — 그것이 예전 세 화면이 하던 일과 똑같다.
 *
 * ── 로그인 직후 새로고침이 없다 ─────────────────────────────────────────────
 * AuthScreen 의 onAuthComplete 에서 토큰만 다시 읽는다. 페이지를 떠나지 않으므로
 * 원래 가려던 Ops 경로가 그대로 유지된다.
 */

import { useCallback, useEffect, useMemo, useState, type ReactNode } from "react";

import { getPremiumAccessToken } from "@/lib/premium-auth-token";
import { AuthScreen } from "@/components/memorial/auth-screen";
import { deriveOpsPhase } from "@/lib/shaker-ops-entry";
import { OPS_NAV, type OpsRoute } from "@/lib/ops-nav";
import { Card, OPS } from "./ops-ui";

export interface OpsChildProps {
  token: string;
  /** 서버가 401/403 을 주면 셸이 로그인/권한 화면으로 되돌린다. */
  onAuthError: (e: unknown) => void;
}

export function OpsLayout({
  active,
  title,
  subtitle,
  children,
}: {
  active: OpsRoute;
  title: string;
  subtitle?: string;
  children: (props: OpsChildProps) => ReactNode;
}) {
  const [token, setToken] = useState<string | null>(null);
  const [tokenLoaded, setTokenLoaded] = useState(false);
  const [errorCode, setErrorCode] = useState<string | null>(null);
  const [navOpen, setNavOpen] = useState(false);

  const readToken = useCallback(() => {
    void getPremiumAccessToken().then((r) => {
      setToken(r.token);
      setTokenLoaded(true);
    });
  }, []);

  useEffect(readToken, [readToken]);

  const phase = useMemo(
    () => deriveOpsPhase({ hasAuth: Boolean(token), errorCode }),
    [token, errorCode]
  );

  /**
   * 화면이 API 오류를 넘기는 자리.
   *
   * 인증이 끊긴 경우에만 토큰을 버린다 — 다른 오류로 로그아웃시키면 스태프가
   * 작업 도중 튕겨 나간다.
   */
  const onAuthError = useCallback((e: unknown) => {
    const code = (e as { code?: string })?.code ?? "";
    if (code === "UNAUTHENTICATED") setToken(null);
    if (code === "UNAUTHENTICATED" || code === "OPS_FORBIDDEN") setErrorCode(code);
  }, []);

  const signOut = useCallback(() => {
    void (async () => {
      try {
        const m = await import("@/lib/supabase-auth");
        await m.signOut();
      } catch {
        /* 세션이 없어도 화면은 로그인으로 돌아가야 한다 */
      }
      setToken(null);
      setErrorCode(null);
      setTokenLoaded(true);
    })();
  }, []);

  if (!tokenLoaded) {
    return (
      <Shell>
        <p className="p-8 text-[13px]" style={{ color: OPS.textFaint }}>
          불러오는 중…
        </p>
      </Shell>
    );
  }

  // 로그인은 **이 경로 안에서** 끝난다 — 앱 루트로 내보내면 스태프가 고객
  // 온보딩(사진 업로드)으로 떨어진다.
  if (phase === "signed-out") {
    return (
      <div className="h-[100dvh] w-full overflow-hidden" style={{ background: "#0a0a0a" }}>
        <AuthScreen initialMode="login" onAuthComplete={readToken} />
      </div>
    );
  }

  if (phase === "forbidden") {
    return (
      <Shell>
        <div className="mx-auto max-w-md p-8">
          <Card>
            <h1
              className="text-[15px] font-semibold"
              style={{ color: OPS.text, fontSize: "15px", lineHeight: 1.35 }}
            >
              접근 권한이 없습니다
            </h1>
            <p className="mt-2 text-[13px]" style={{ color: OPS.textMuted }}>
              이 계정은 운영자로 등록되어 있지 않습니다. 관리자에게 문의해 주세요.
            </p>
            <div className="mt-4">
              <button
                type="button"
                onClick={signOut}
                className="text-[13px] underline"
                style={{ color: OPS.textMuted }}
              >
                다른 계정으로 로그인
              </button>
            </div>
          </Card>
        </div>
      </Shell>
    );
  }

  return (
    <Shell>
      <div className="flex h-[100dvh] overflow-hidden">
        <Sidebar active={active} open={navOpen} onClose={() => setNavOpen(false)} onSignOut={signOut} />

        <div className="flex min-h-0 min-w-0 flex-1 flex-col">
          <header
            className="z-10 flex shrink-0 items-center gap-3 border-b px-5 py-3.5"
            style={{ background: "#fff", borderColor: OPS.border }}
          >
            <button
              type="button"
              onClick={() => setNavOpen(true)}
              aria-label="메뉴"
              className="rounded-lg border px-2.5 py-1.5 text-[13px] md:hidden"
              style={{ borderColor: OPS.borderStrong, color: OPS.text }}
            >
              ☰
            </button>
            <div className="min-w-0">
              <h1
                className="truncate text-[15px] font-semibold"
                style={{ color: OPS.text, fontSize: "15px", lineHeight: 1.35 }}
              >
                {title}
              </h1>
              {subtitle ? (
                <p className="truncate text-[12px]" style={{ color: OPS.textFaint }}>
                  {subtitle}
                </p>
              ) : null}
            </div>
          </header>

          <main className="min-h-0 min-w-0 flex-1 overflow-y-auto px-5 py-6">
            {token ? children({ token, onAuthError }) : null}
          </main>
        </div>
      </div>
    </Shell>
  );
}

function Shell({ children }: { children: ReactNode }) {
  return (
    <div className="min-h-[100dvh] w-full" style={{ background: OPS.pageBg, color: OPS.text }}>
      {children}
    </div>
  );
}

function Sidebar({
  active,
  open,
  onClose,
  onSignOut,
}: {
  active: OpsRoute;
  open: boolean;
  onClose: () => void;
  onSignOut: () => void;
}) {
  const nav = (
    <nav className="flex h-full flex-col gap-1 p-4">
      <div className="px-2 pb-5 pt-1">
        <p className="text-[13px] font-semibold leading-tight" style={{ color: OPS.text }}>
          Eternal Beam
        </p>
        <p className="text-[12px]" style={{ color: OPS.gold }}>
          Ops
        </p>
      </div>

      {OPS_NAV.map((item) => {
        const on = item.route === active;
        return (
          <a
            key={item.route}
            href={item.path}
            onClick={onClose}
            className="rounded-lg px-3 py-2 text-[13px] font-medium"
            style={{
              background: on ? OPS.goldSoft : "transparent",
              color: on ? OPS.gold : OPS.textMuted,
            }}
          >
            {item.label}
          </a>
        );
      })}

      <div className="mt-auto px-1 pt-4">
        <button
          type="button"
          onClick={onSignOut}
          className="w-full rounded-lg px-3 py-2 text-left text-[13px]"
          style={{ color: OPS.textMuted }}
        >
          Sign Out
        </button>
      </div>
    </nav>
  );

  return (
    <>
      {/* 데스크톱: 고정 사이드바 */}
      <aside
        className="hidden w-[208px] shrink-0 border-r md:block"
        style={{ background: "#fff", borderColor: OPS.border }}
      >
        <div className="sticky top-0 h-[100dvh]">{nav}</div>
      </aside>

      {/* 모바일/태블릿: 접히는 서랍 */}
      {open ? (
        <div className="fixed inset-0 z-30 md:hidden">
          <button
            type="button"
            aria-label="닫기"
            onClick={onClose}
            className="absolute inset-0"
            style={{ background: "rgba(20,18,16,0.35)" }}
          />
          <aside
            className="absolute left-0 top-0 h-full w-[220px] border-r"
            style={{ background: "#fff", borderColor: OPS.border }}
          >
            {nav}
          </aside>
        </div>
      ) : null}
    </>
  );
}
