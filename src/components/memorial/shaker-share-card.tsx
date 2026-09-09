"use client";

/**
 * 고객용 QR 공유 카드 — **선택 기능이며 기본은 꺼져 있다.**
 *
 * ⚠️ 소유 모델상 **생산용 QR 은 판매자(운영)가 만든다** (/ops/shaker).
 * 편지·메모리 카드에 인쇄되어 나가는 QR 은 전부 그쪽에서 나온다. 이 카드는
 * 고객이 자기 펫 링크를 직접 만들어 보고 싶을 때를 위한 부가 기능이고,
 * 기본 워크플로가 아니다.
 *
 * 그래서 기본이 꺼짐이다: 두 경로가 동시에 열려 있으면 "이 QR 은 누가 만든
 * 것인가"가 흐려지고, 고객이 만든 링크가 인쇄물에 섞여 들어갈 수 있다.
 * VITE_SHAKER_CUSTOMER_SHARE=1 로만 켜진다.
 *
 * ── 펫 데이터를 만들지 않는다 ────────────────────────────────────────────────
 * petId 는 이미 있는 content_id 에서 파생된 값이고(pet-identity.ts), breathing_url 은
 * 이미 생성된 파이프라인 결과다. 이 카드는 그것들을 **가리키는 링크**만 만든다.
 * 물리 주문·QR 이 canonical petId 를 참조해야 한다는 규칙이 여기서 시작된다.
 *
 * ── 생성하지 않는다 ─────────────────────────────────────────────────────────
 * 공유 발급은 프로바이더를 호출하지 않는다. 이 파일은 생성 관련 모듈을 import 하지
 * 않으며, 자산이 없으면 링크를 만들 수 있게 하지 않고 **그냥 카드를 숨긴다**.
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import { Check, Copy, Link2, QrCode } from "lucide-react";

import { memorialT } from "@/components/memorial/memorial-i18n";
import { getPetName } from "@/lib/pet-profile";
import { getPremiumAccessToken } from "@/lib/premium-auth-token";
import {
  absoluteShareUrl,
  createShakerShare,
  listShakerShares,
  revokeShakerShare,
  type ShareSummary,
} from "@/lib/shaker-share";
import {
  copyToClipboard,
  deriveSharePanel,
  pickSharePoster,
} from "@/lib/shaker-share-panel";

interface ShakerShareCardProps {
  /** 이미 있는 펫의 id. null 이면 카드를 그리지 않는다. */
  petId: string | null;
  /** 이미 생성된 BREATHING URL. 없으면 공유할 대상이 없다. */
  breathingUrl: string | null;
  /** 포스터 후보 (누끼 우선). data: URL 은 자동으로 걸러진다. */
  posterCandidates?: Array<string | null | undefined>;
  /** BREATHING 자산이 실제로 있을 때만 켠다 — MembershipCard 와 같은 게이트. */
  enabled: boolean;
  language?: string;
}

/**
 * 고객이 직접 QR 을 만들 수 있는가. **기본 꺼짐** — 생산 워크플로는 운영이다.
 *
 * 플래그를 읽지 못하는 환경(빌드 설정 누락 등)에서도 꺼진 쪽으로 떨어진다.
 * 켜는 것이 명시적이어야 하고, 꺼지는 것이 기본이어야 한다.
 */
export function customerShareEnabled(): boolean {
  try {
    const v = (import.meta as { env?: Record<string, string> }).env
      ?.VITE_SHAKER_CUSTOMER_SHARE;
    return String(v ?? "").trim() === "1";
  } catch {
    return false;
  }
}

export function ShakerShareCard({
  petId,
  breathingUrl,
  posterCandidates = [],
  enabled,
  language = "ko",
}: ShakerShareCardProps) {
  const t = memorialT(language).shakerShare;

  const [token, setToken] = useState<string | null>(null);
  const [shares, setShares] = useState<ShareSummary[] | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState<"create" | "revoke" | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [justCreatedUrl, setJustCreatedUrl] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);

  // 토큰 취득 — 없으면 signed-out 으로 그린다(발급은 인증 경로다).
  useEffect(() => {
    let alive = true;
    void getPremiumAccessToken().then((r) => {
      if (alive) setToken(r.token);
    });
    return () => {
      alive = false;
    };
  }, []);

  // 목록 조회. 읽기 전용이고 생성과 무관하다.
  const refresh = useCallback(async () => {
    if (!token || !petId) {
      setLoading(false);
      return;
    }
    setLoading(true);
    try {
      setShares(await listShakerShares({ petId, accessToken: token }));
      setError(null);
    } catch {
      setError("load");
    } finally {
      setLoading(false);
    }
  }, [token, petId]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const state = useMemo(
    () =>
      deriveSharePanel({
        hasBreathingAsset: Boolean(enabled && breathingUrl && petId),
        hasAuth: Boolean(token),
        loading,
        shares,
        error,
        justCreatedUrl,
      }),
    [enabled, breathingUrl, petId, token, loading, shares, error, justCreatedUrl]
  );

  const onCreate = useCallback(async () => {
    if (!token || !petId || !breathingUrl) return;
    setBusy("create");
    setError(null);
    try {
      const created = await createShakerShare({
        petId,
        breathingUrl,
        petName: getPetName() || null,
        posterUrl: pickSharePoster(posterCandidates),
        accessToken: token,
      });
      // 원문 토큰은 **이 응답에서만** 온다. 즉시 화면에 올린다.
      setJustCreatedUrl(absoluteShareUrl(created.sharePath));
      setCopied(false);
      await refresh();
    } catch {
      setError("create");
    } finally {
      setBusy(null);
    }
  }, [token, petId, breathingUrl, posterCandidates, refresh]);

  const onRevokeAll = useCallback(async () => {
    if (!token) return;
    setBusy("revoke");
    setError(null);
    try {
      for (const id of state.activeShareIds) {
        await revokeShakerShare({ shareId: id, accessToken: token });
      }
      setJustCreatedUrl(null);
      await refresh();
    } catch {
      setError("revoke");
    } finally {
      setBusy(null);
    }
  }, [token, state.activeShareIds, refresh]);

  const onCopy = useCallback(async () => {
    if (!state.shareUrl) return;
    const ok = await copyToClipboard(state.shareUrl);
    // 실패해도 URL 은 화면에 그대로 보인다 — 직접 선택해 복사할 수 있다.
    setCopied(ok);
    if (ok) window.setTimeout(() => setCopied(false), 2000);
  }, [state.shareUrl]);

  // 생산 QR 은 운영이 만든다. 플래그로 켜지 않으면 이 카드는 존재하지 않는다.
  if (!customerShareEnabled()) return null;
  // 자산이 없으면 아무것도 그리지 않는다 — MembershipCard 와 같은 규칙이다.
  if (state.phase === "no-asset") return null;

  const shell =
    "w-full max-w-[320px] rounded-2xl px-4 py-3.5 border backdrop-blur-sm text-left";
  const shellStyle = {
    background: "rgba(255,255,255,0.04)",
    borderColor: "rgba(255,255,255,0.12)",
  };

  if (state.phase === "signed-out") {
    return (
      <div className={shell} style={shellStyle}>
        <div className="flex items-center gap-2">
          <QrCode className="w-4 h-4 shrink-0" style={{ color: "#9a9a9a" }} />
          <p className="text-sm" style={{ color: "#E2E2E2" }}>
            {t.signInRequired}
          </p>
        </div>
      </div>
    );
  }

  return (
    <div className={shell} style={shellStyle}>
      <div className="flex items-center gap-2">
        <QrCode className="w-4 h-4 shrink-0" style={{ color: "#d8c9a8" }} />
        <p className="text-sm font-medium" style={{ color: "#F2F2F2" }}>
          {t.title}
        </p>
        {state.activeCount > 0 && (
          <span className="ml-auto text-[11px]" style={{ color: "#9a9a9a" }}>
            {t.activeCount(state.activeCount)}
          </span>
        )}
      </div>

      <p className="mt-1.5 text-[12px] leading-relaxed" style={{ color: "#B8B8B8" }}>
        {t.subtitle}
      </p>
      <p className="mt-1 text-[11px]" style={{ color: "#8a8a8a" }}>
        {t.breathingFree}
      </p>

      {/* 방금 만든 링크 — 서버가 원문을 저장하지 않으므로 이때만 보여 줄 수 있다. */}
      {state.shareUrl && (
        <div className="mt-3">
          <p
            className="break-all rounded-lg px-2.5 py-2 text-[11px] leading-relaxed"
            style={{ background: "rgba(0,0,0,0.28)", color: "#D8D8D8" }}
          >
            {state.shareUrl}
          </p>
          <div className="mt-2 flex gap-2">
            <button
              type="button"
              onClick={() => void onCopy()}
              className="flex items-center gap-1.5 rounded-full border px-3 py-1.5 text-[12px]"
              style={{ borderColor: "rgba(255,255,255,0.18)", color: "#E2E2E2" }}
            >
              {copied ? <Check className="w-3.5 h-3.5" /> : <Copy className="w-3.5 h-3.5" />}
              {copied ? t.copied : t.copy}
            </button>
            <a
              href={state.shareUrl}
              target="_blank"
              rel="noreferrer"
              className="flex items-center gap-1.5 rounded-full border px-3 py-1.5 text-[12px]"
              style={{ borderColor: "rgba(255,255,255,0.18)", color: "#E2E2E2" }}
            >
              <Link2 className="w-3.5 h-3.5" />
              {t.open}
            </a>
          </div>
        </div>
      )}

      {/* 링크는 있는데 원문을 모르는 상태 — 왜 안 보이는지 설명해야 한다. */}
      {state.hasUnviewableLink && (
        <p className="mt-3 text-[11px] leading-relaxed" style={{ color: "#8a8a8a" }}>
          {t.linkNotViewable}
        </p>
      )}

      {state.phase === "error" && (
        <p className="mt-3 text-[11px]" style={{ color: "#d99" }}>
          {t.error}
        </p>
      )}

      <div className="mt-3 flex flex-wrap gap-2">
        {state.canCreate && (
          <button
            type="button"
            disabled={busy !== null}
            onClick={() => void onCreate()}
            className="rounded-full px-3.5 py-1.5 text-[12px] disabled:opacity-50"
            style={{ background: "rgba(216,201,168,0.16)", color: "#EDE3CE" }}
          >
            {busy === "create"
              ? t.creating
              : state.activeCount > 0
                ? t.createAnother
                : t.create}
          </button>
        )}
        {state.canRevoke && (
          <button
            type="button"
            disabled={busy !== null}
            onClick={() => void onRevokeAll()}
            className="rounded-full border px-3.5 py-1.5 text-[12px] disabled:opacity-50"
            style={{ borderColor: "rgba(255,255,255,0.18)", color: "#B8B8B8" }}
          >
            {busy === "revoke" ? t.revoking : t.revoke}
          </button>
        )}
      </div>
    </div>
  );
}
