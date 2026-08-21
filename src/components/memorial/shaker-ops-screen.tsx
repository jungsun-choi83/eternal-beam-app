"use client";

/**
 * 판매자/운영 Shaker 콘솔 — 물리 제품에 인쇄할 QR 을 만드는 자리.
 *
 *   고객이 사진을 올린다 → canonical petId 가 생긴다
 *   → **운영이 여기서 공유를 만든다** → QR 을 받는다
 *   → 편지 / 메모리 카드에 붙인다 → 배송
 *   → 고객이 스캔한다 → 같은 펫이 열린다
 *
 * ── 최소 도구다 ──────────────────────────────────────────────────────────────
 * 이행(fulfillment) 대시보드가 아니다. 주문 상태·배송·생산은 Phase 12–13 의
 * 몫이고 여기 없다. 이 화면은 그 사슬의 **QR 구간**만 담당한다.
 *
 * ── 펫을 만들지 않는다 ───────────────────────────────────────────────────────
 * 검색은 이미 만들어진 펫만 보여 주고, 발급은 그 petId 를 그대로 쓴다.
 * 소유자와 BREATHING 위치는 **서버가** 찾는다 — 운영자가 타이핑하지 않는다.
 * 오타 하나로 남의 펫에 QR 이 붙으면 그건 이미 인쇄된 뒤다.
 */

import { useCallback, useEffect, useMemo, useState } from "react";

import { getPremiumAccessToken } from "@/lib/premium-auth-token";
import { copyToClipboard } from "@/lib/shaker-share-panel";
import { deriveOpsPhase } from "@/lib/shaker-ops-entry";
import {
  createOpsShare,
  fetchOpsQr,
  listOpsShares,
  revokeOpsShare,
  searchOpsPets,
  type OpsCreatedShare,
  type OpsPet,
  type OpsShareSummary,
} from "@/lib/shaker-ops-api";

/** 인쇄물 종류 — shaker_ops_v1._VALID_PURPOSES 와 같아야 한다. */
const PURPOSES = ["OPS", "LETTER", "MEMORY_BOX"] as const;

export function ShakerOpsScreen() {
  const [token, setToken] = useState<string | null>(null);
  const [tokenLoaded, setTokenLoaded] = useState(false);
  const [errorCode, setErrorCode] = useState<string | null>(null);
  const [errorText, setErrorText] = useState<string | null>(null);

  const [query, setQuery] = useState("");
  const [pets, setPets] = useState<OpsPet[]>([]);
  const [degraded, setDegraded] = useState(false);
  const [selected, setSelected] = useState<OpsPet | null>(null);
  const [shares, setShares] = useState<OpsShareSummary[]>([]);

  const [purpose, setPurpose] = useState<(typeof PURPOSES)[number]>("LETTER");
  const [orderRef, setOrderRef] = useState("");
  const [created, setCreated] = useState<OpsCreatedShare | null>(null);
  const [qrUrl, setQrUrl] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    void getPremiumAccessToken().then((r) => {
      setToken(r.token);
      setTokenLoaded(true);
    });
  }, []);

  const phase = useMemo(
    () => deriveOpsPhase({ hasAuth: Boolean(token), errorCode }),
    [token, errorCode]
  );

  const fail = useCallback((e: unknown) => {
    const err = e as { code?: string; message?: string };
    if (err?.code === "UNAUTHENTICATED") setToken(null);
    setErrorCode(err?.code ?? "UNKNOWN");
    setErrorText(err?.message ?? "요청에 실패했습니다.");
  }, []);

  const doSearch = useCallback(async () => {
    if (!token) return;
    setBusy("search");
    setErrorCode(null);
    try {
      const result = await searchOpsPets({ query });
      setPets(result.pets);
      setDegraded(result.degraded);
    } catch (e) {
      fail(e);
    } finally {
      setBusy(null);
    }
  }, [token, query, fail]);

  // 토큰이 준비되면 한 번 훑는다 — 운영자가 검색어 없이도 목록을 본다.
  useEffect(() => {
    if (token) void doSearch();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [token]);

  const selectPet = useCallback(
    async (pet: OpsPet) => {
      setSelected(pet);
      setCreated(null);
      setQrUrl(null);
      if (!token) return;
      setBusy("shares");
      try {
        setShares(await listOpsShares({ petId: pet.petId }));
        setErrorCode(null);
      } catch (e) {
        fail(e);
      } finally {
        setBusy(null);
      }
    },
    [token, fail]
  );

  const loadQr = useCallback(
    async (shareUrl: string, petId: string) => {
      if (!token) return;
      try {
        const blob = await fetchOpsQr({
          shareUrl, kind: "png", filename: petId,
        });
        setQrUrl((old) => {
          if (old) URL.revokeObjectURL(old);
          return URL.createObjectURL(blob);
        });
      } catch (e) {
        fail(e);
      }
    },
    [token, fail]
  );

  const doCreate = useCallback(async () => {
    if (!token || !selected) return;
    setBusy("create");
    setErrorCode(null);
    try {
      const share = await createOpsShare({
        petId: selected.petId,
        purpose,
        orderRef: orderRef.trim() || null,
      });
      setCreated(share);
      setCopied(false);
      await loadQr(share.shareUrl, share.petId);
      setShares(await listOpsShares({ petId: selected.petId }));
    } catch (e) {
      fail(e);
    } finally {
      setBusy(null);
    }
  }, [token, selected, purpose, orderRef, loadQr, fail]);

  const doRevoke = useCallback(
    async (shareId: string) => {
      if (!token || !selected) return;
      setBusy(shareId);
      try {
        await revokeOpsShare({ shareId, petId: selected.petId });
        setShares(await listOpsShares({ petId: selected.petId }));
        if (created?.shareId === shareId) {
          setCreated(null);
          setQrUrl(null);
        }
      } catch (e) {
        fail(e);
      } finally {
        setBusy(null);
      }
    },
    [token, selected, created, fail]
  );

  const downloadQr = useCallback(
    async (kind: "svg" | "png") => {
      if (!token || !created) return;
      try {
        const blob = await fetchOpsQr({
          shareUrl: created.shareUrl, kind, filename: created.petId,
        });
        const href = URL.createObjectURL(blob);
        const a = document.createElement("a");
        a.href = href;
        a.download = `${created.petId}-${created.purpose.toLowerCase()}-qr.${kind}`;
        a.click();
        URL.revokeObjectURL(href);
      } catch (e) {
        fail(e);
      }
    },
    [token, created, fail]
  );

  useEffect(() => () => {
    if (qrUrl) URL.revokeObjectURL(qrUrl);
  }, [qrUrl]);

  // ── 렌더 ────────────────────────────────────────────────────────────────────
  const shell = "min-h-screen bg-[#0b0b0d] text-[#E6E6E6] px-5 py-8";

  if (!tokenLoaded) {
    return <div className={shell}><p className="text-sm text-white/40">불러오는 중…</p></div>;
  }

  if (phase === "signed-out" || phase === "forbidden") {
    return (
      <div className={shell}>
        <h1 className="text-lg font-medium">Eternal Beam · 운영 콘솔</h1>
        <p className="mt-3 text-sm text-white/60">
          {phase === "signed-out"
            ? "운영자 계정으로 로그인해야 합니다."
            : "이 계정에는 운영 권한이 없습니다 (SHAKER_OPS_USER_IDS)."}
        </p>
      </div>
    );
  }

  return (
    <div className={shell}>
      <header className="mb-6">
        <h1 className="text-lg font-medium">Eternal Beam · Shaker QR 콘솔</h1>
        <p className="mt-1 text-xs text-white/45">
          고객 펫을 찾아 공유 링크를 만들고, 편지·메모리 카드에 인쇄할 QR 을 내려받습니다.
          펫은 새로 만들어지지 않습니다 — 이미 있는 petId 를 그대로 가리킵니다.
        </p>
      </header>

      {errorText && (
        <p className="mb-4 rounded-lg bg-red-500/10 px-3 py-2 text-xs text-red-300">
          {errorCode}: {errorText}
        </p>
      )}

      {degraded && (
        <p className="mb-4 rounded-lg bg-amber-500/10 px-3 py-2 text-xs text-amber-200">
          등록 펫 목록을 불러오지 못해 레거시 결과만 표시 중입니다.
        </p>
      )}

      {/* 1. 펫 찾기 */}
      <section className="mb-7">
        <h2 className="mb-2 text-sm font-medium text-white/80">1. 고객 펫 찾기</h2>
        <div className="flex gap-2">
          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && void doSearch()}
            placeholder="pet_id 또는 고객 계정 일부"
            className="flex-1 rounded-lg bg-white/5 px-3 py-2 text-sm outline-none placeholder:text-white/25"
          />
          <button
            type="button"
            onClick={() => void doSearch()}
            disabled={busy === "search"}
            className="rounded-lg bg-white/10 px-4 py-2 text-sm disabled:opacity-50"
          >
            {busy === "search" ? "검색 중…" : "검색"}
          </button>
        </div>

        <ul className="mt-3 space-y-1.5">
          {pets.map((p) => (
            <li key={`${p.ownerUserId}:${p.petId}`}>
              <button
                type="button"
                onClick={() => void selectPet(p)}
                className={`w-full rounded-lg px-3 py-2 text-left text-xs ${
                  selected?.petId === p.petId ? "bg-white/15" : "bg-white/5"
                }`}
              >
                <span className="font-mono">{p.petId}</span>
                <span className="ml-2 text-white/45">{p.ownerUserId}</span>
                <span className="ml-2 text-white/35">· 모션 {p.readyCount}</span>
                <span className="ml-2 text-white/35">· {p.source}</span>
              </button>
            </li>
          ))}
          {pets.length === 0 && (
            <li className="px-1 py-2 text-xs text-white/35">
              검색 결과가 없습니다. 아직 경험이 만들어지지 않은 펫에는 QR 을 붙일 수 없습니다.
            </li>
          )}
        </ul>
      </section>

      {/* 2. 공유 만들기 */}
      {selected && (
        <section className="mb-7">
          <h2 className="mb-2 text-sm font-medium text-white/80">
            2. 공유 만들기 — <span className="font-mono">{selected.petId}</span>
          </h2>
          <div className="flex flex-wrap items-center gap-2">
            <select
              value={purpose}
              onChange={(e) => setPurpose(e.target.value as (typeof PURPOSES)[number])}
              className="rounded-lg bg-white/5 px-3 py-2 text-sm outline-none"
            >
              {PURPOSES.map((p) => (
                <option key={p} value={p} className="bg-[#15151a]">{p}</option>
              ))}
            </select>
            <input
              value={orderRef}
              onChange={(e) => setOrderRef(e.target.value)}
              placeholder="주문 번호 (선택 · Phase 12–13)"
              className="flex-1 rounded-lg bg-white/5 px-3 py-2 text-sm outline-none placeholder:text-white/25"
            />
            <button
              type="button"
              onClick={() => void doCreate()}
              disabled={busy === "create"}
              className="rounded-lg bg-[#d8c9a8]/20 px-4 py-2 text-sm text-[#EDE3CE] disabled:opacity-50"
            >
              {busy === "create" ? "만드는 중…" : "공유 + QR 만들기"}
            </button>
          </div>
        </section>
      )}

      {/* 3. QR */}
      {created && (
        <section className="mb-7">
          <h2 className="mb-2 text-sm font-medium text-white/80">3. QR</h2>
          <div className="flex flex-wrap items-start gap-4">
            {qrUrl && (
              <img
                src={qrUrl}
                alt="Shaker QR"
                className="h-44 w-44 rounded-lg bg-white p-2"
              />
            )}
            <div className="min-w-[240px] flex-1">
              <p className="break-all rounded-lg bg-black/30 px-2.5 py-2 text-[11px] leading-relaxed text-white/80">
                {created.shareUrl}
              </p>
              <p className="mt-1.5 text-[11px] text-white/40">
                이 링크는 지금 한 번만 볼 수 있습니다. 서버는 해시만 저장합니다.
              </p>
              <div className="mt-2 flex flex-wrap gap-2">
                <button
                  type="button"
                  onClick={async () => setCopied(await copyToClipboard(created.shareUrl))}
                  className="rounded-full border border-white/20 px-3 py-1.5 text-xs"
                >
                  {copied ? "복사됨" : "링크 복사"}
                </button>
                <button
                  type="button"
                  onClick={() => void downloadQr("svg")}
                  className="rounded-full border border-white/20 px-3 py-1.5 text-xs"
                >
                  SVG 내려받기 (인쇄용)
                </button>
                <button
                  type="button"
                  onClick={() => void downloadQr("png")}
                  className="rounded-full border border-white/20 px-3 py-1.5 text-xs"
                >
                  PNG 내려받기
                </button>
                <a
                  href={created.shareUrl}
                  target="_blank"
                  rel="noreferrer"
                  className="rounded-full border border-white/20 px-3 py-1.5 text-xs"
                >
                  Shaker 미리보기
                </a>
              </div>
            </div>
          </div>
        </section>
      )}

      {/* 4. 기존 공유 */}
      {selected && (
        <section>
          <h2 className="mb-2 text-sm font-medium text-white/80">4. 이 펫의 공유 링크</h2>
          <ul className="space-y-1.5">
            {shares.map((s) => (
              <li
                key={s.shareId}
                className="flex items-center gap-2 rounded-lg bg-white/5 px-3 py-2 text-xs"
              >
                <span className="font-mono text-white/70">{s.shareId}</span>
                <span className="text-white/45">{s.purpose ?? "—"}</span>
                {s.orderRef && <span className="text-white/45">#{s.orderRef}</span>}
                <span className={s.active ? "text-emerald-300/70" : "text-white/30"}>
                  {s.active ? "활성" : "해제됨"}
                </span>
                {s.active && (
                  <button
                    type="button"
                    onClick={() => void doRevoke(s.shareId)}
                    disabled={busy === s.shareId}
                    className="ml-auto rounded-full border border-white/20 px-2.5 py-1 text-[11px] disabled:opacity-50"
                  >
                    {busy === s.shareId ? "해제 중…" : "해제"}
                  </button>
                )}
              </li>
            ))}
            {shares.length === 0 && (
              <li className="px-1 py-2 text-xs text-white/35">아직 공유 링크가 없습니다.</li>
            )}
          </ul>
          <p className="mt-3 text-[11px] leading-relaxed text-white/35">
            ⚠️ 해제하면 이미 배송된 인쇄물의 QR 도 함께 죽습니다. 재발급하면 새 토큰이 나오므로
            인쇄물을 다시 만들어야 합니다.
          </p>
        </section>
      )}
    </div>
  );
}
