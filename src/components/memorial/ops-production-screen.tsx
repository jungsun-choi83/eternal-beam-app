"use client";

/**
 * 운영 생산 콘솔 (Phase 13.1) — `/ops/production`.
 *
 *   주문 검색 → PAID 주문 열기 → 펫/편지/QR 확인 → 생산 준비
 *   → 미리보기 · 내려받기 → 생산 시작 → 생산 완료 → 송장 → 발송
 *
 * Phase 13 API 와 상태 기계를 **그대로** 쓴다. 새 엔드포인트도 새 전이도 없다.
 * 버튼 활성 판정은 lib/ops-production-flow.ts 의 순수 함수가 하고, 서버가 최종
 * 판정을 다시 한다 — 화면이 틀려도 잘못된 전이는 일어나지 않는다.
 *
 * ⚠️ 브라우저 인쇄를 하지 않는다. 인쇄소에 넘길 **파일**을 만들어 준다 —
 *    페이지를 인쇄하면 화면 해상도로 래스터화되고 브라우저 장식이 함께 찍힌다.
 */

import { useCallback, useEffect, useMemo, useState } from "react";

import { getPremiumAccessToken } from "@/lib/premium-auth-token";
import { AuthScreen } from "./auth-screen";
import { deriveOpsPhase } from "@/lib/shaker-ops-entry";
import {
  FILE_LABEL,
  fileName,
  opsActions,
  productionStep,
  shipBlockedReason,
} from "@/lib/ops-production-flow";
import {
  OpsError,
  addTracking,
  fetchProductionFile,
  fetchProductionState,
  fetchProductionZip,
  fetchShareQrAgain,
  markDelivered,
  markProduced,
  markShipped,
  prepareProduction,
  saveBlob,
  searchPaidOrders,
  startProduction,
  type OpsOrderRow,
  type OpsProductionState,
} from "@/lib/ops-production-api";

const PARTNER_TYPE_LABEL: Record<string, string> = {
  HOSPITAL: "동물병원",
  FUNERAL: "장례식장",
};

const PRODUCT_LABEL: Record<string, string> = {
  LETTER: "편지",
  MEMORY_BOX: "메모리 박스",
};

const STEPS = ["PENDING", "READY", "IN_PRODUCTION", "PRODUCED"];

export function OpsProductionScreen() {
  const [token, setToken] = useState<string | null>(null);
  const [tokenLoaded, setTokenLoaded] = useState(false);
  const [errorCode, setErrorCode] = useState<string | null>(null);
  const [errorText, setErrorText] = useState<string | null>(null);

  const [query, setQuery] = useState("");
  const [orders, setOrders] = useState<OpsOrderRow[]>([]);
  const [state, setState] = useState<OpsProductionState | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [preview, setPreview] = useState<{ kind: string; url: string } | null>(null);

  const [qrUrlInput, setQrUrlInput] = useState("");
  const [photoInput, setPhotoInput] = useState("");
  const [trackingInput, setTrackingInput] = useState("");

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
    setErrorCode(err?.code ?? "UNKNOWN");
    setErrorText(err?.message ?? "요청에 실패했습니다.");
  }, []);

  const run = useCallback(
    async (label: string, fn: () => Promise<OpsProductionState>) => {
      setBusy(label);
      setErrorText(null);
      try {
        setState(await fn());
        setErrorCode(null);
      } catch (e) {
        fail(e);
      } finally {
        setBusy(null);
      }
    },
    [fail]
  );

  const doSearch = useCallback(async () => {
    if (!token) return;
    setBusy("search");
    try {
      setOrders(await searchPaidOrders({ query, accessToken: token }));
      setErrorCode(null);
    } catch (e) {
      fail(e);
    } finally {
      setBusy(null);
    }
  }, [token, query, fail]);

  useEffect(() => {
    if (token) void doSearch();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [token]);

  const open = useCallback(
    async (orderId: string) => {
      if (!token) return;
      setPreview(null);
      setTrackingInput("");
      await run("open", () => fetchProductionState(orderId, token));
    },
    [token, run]
  );

  const doPreview = useCallback(
    async (kind: string) => {
      if (!token || !state) return;
      setBusy(`preview:${kind}`);
      try {
        const blob = await fetchProductionFile({
          orderId: state.orderId, kind, accessToken: token,
        });
        setPreview((old) => {
          if (old) URL.revokeObjectURL(old.url);
          return { kind, url: URL.createObjectURL(blob) };
        });
        setErrorCode(null);
      } catch (e) {
        fail(e);
      } finally {
        setBusy(null);
      }
    },
    [token, state, fail]
  );

  const doDownload = useCallback(
    async (kind: string) => {
      if (!token || !state) return;
      setBusy(`dl:${kind}`);
      try {
        const blob = await fetchProductionFile({
          orderId: state.orderId, kind, accessToken: token,
        });
        saveBlob(blob, fileName(state.orderId, kind));
      } catch (e) {
        fail(e);
      } finally {
        setBusy(null);
      }
    },
    [token, state, fail]
  );

  const doZip = useCallback(async () => {
    if (!token || !state) return;
    setBusy("zip");
    try {
      const blob = await fetchProductionZip({ orderId: state.orderId, accessToken: token });
      saveBlob(blob, `${state.orderId}-production.zip`);
    } catch (e) {
      fail(e);
    } finally {
      setBusy(null);
    }
  }, [token, state, fail]);

  const doQrAgain = useCallback(
    async (kind: "svg" | "png") => {
      if (!token || !state?.shakerShareId) return;
      setBusy(`qr:${kind}`);
      try {
        const blob = await fetchShareQrAgain({
          shareId: state.shakerShareId, kind, accessToken: token,
        });
        saveBlob(blob, `${state.petId}-qr.${kind}`);
      } catch (e) {
        fail(e);
      } finally {
        setBusy(null);
      }
    },
    [token, state, fail]
  );

  useEffect(() => () => {
    if (preview) URL.revokeObjectURL(preview.url);
  }, [preview]);

  const actions = state
    ? opsActions(state)
    : { canPrepare: false, canPreview: false, canDownload: false, canStart: false,
        canMarkProduced: false, canAddTracking: false, canShip: false,
        canMarkDelivered: false, blockedReason: null };

  const shell = "min-h-screen bg-[#0b0b0d] px-5 py-8 text-[#E6E6E6]";

  if (!tokenLoaded) {
    return <div className={shell}><p className="text-sm text-white/40">불러오는 중…</p></div>;
  }

  // ── 로그인은 **이 경로 안에서** 끝난다 ───────────────────────────────────
  // 예전에는 "운영자 계정으로 로그인해야 합니다" 라는 안내만 띄우고 로그인
  // 수단을 주지 않았다. 그래서 스태프는 앱 루트로 나가야 했고, 루트는 고객
  // 온보딩(qrConnection → photoUpload)이다 — 스태프가 사진 업로드로 떨어진
  // 두 번째 이유가 이것이다.
  //
  // 여기서 로그인하면 **페이지를 떠나지 않으므로** 원래 가려던 Ops 경로가
  // 그대로 유지된다. 목적지를 따로 저장할 필요가 없다.
  if (phase === "signed-out") {
    return (
      <div className="memorial-ui h-[100dvh] w-full overflow-hidden bg-[#0a0a0a]">
        <AuthScreen
          initialMode="login"
          onAuthComplete={() => {
            // 같은 화면에 머문 채 토큰만 다시 읽는다.
            void getPremiumAccessToken().then((r) => {
              setToken(r.token);
              setTokenLoaded(true);
            });
          }}
        />
      </div>
    );
  }

  // 인가는 서버가 한다(SHAKER_OPS_USER_IDS). 로그인했는데 권한이 없으면
  // **여기서 멈춘다** — 고객 화면으로 흘려보내지 않는다.
  if (phase === "forbidden") {
    return (
      <div className={shell}>
        <h1 className="text-lg font-medium">Eternal Beam · 생산 콘솔</h1>
        <p className="mt-3 text-sm text-white/60">
          이 계정에는 운영 권한이 없습니다 (SHAKER_OPS_USER_IDS).
        </p>
      </div>
    );
  }

  return (
    <div className={shell}>
      <header className="mb-6">
        <h1 className="text-lg font-medium">Eternal Beam · 생산 콘솔</h1>
        <p className="mt-1 text-xs text-white/45">
          결제된 주문만 생산에 들어갑니다. 펫·편지·QR 은 새로 만들어지지 않고 기존 것을 씁니다.
        </p>
      </header>

      {errorText && (
        <p className="mb-4 rounded-lg bg-red-500/10 px-3 py-2 text-xs text-red-300">
          {errorCode}: {errorText}
        </p>
      )}

      {/* 1. 주문 찾기 */}
      <section className="mb-7">
        <h2 className="mb-2 text-sm font-medium text-white/80">1. 결제된 주문</h2>
        <div className="flex gap-2">
          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && void doSearch()}
            placeholder="주문번호 · 고객 · pet_id · 수령인"
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
          {orders.map((o) => (
            <li key={o.orderId}>
              <button
                type="button"
                onClick={() => void open(o.orderId)}
                className={`w-full rounded-lg px-3 py-2 text-left text-xs ${
                  state?.orderId === o.orderId ? "bg-white/15" : "bg-white/5"
                }`}
              >
                <span className="font-mono">{o.orderId.slice(0, 20)}</span>
                <span className="ml-2">{PRODUCT_LABEL[o.productType] ?? o.productType}</span>
                <span className="ml-2 text-white/45">{o.recipientName ?? "—"}</span>
                <span className="ml-2 text-emerald-300/70">{o.paymentStatus}</span>
                <span className="ml-2 text-white/35">
                  {o.productionStatus} / {o.shippingStatus}
                </span>
              </button>
            </li>
          ))}
          {orders.length === 0 && (
            <li className="px-1 py-2 text-xs text-white/35">결제된 주문이 없습니다.</li>
          )}
        </ul>
      </section>

      {state && (
        <>
          {/* 2. 주문 상세 */}
          <section className="mb-7">
            <h2 className="mb-2 text-sm font-medium text-white/80">
              2. Order <span className="font-mono">{state.orderId}</span>
            </h2>
            <dl className="grid grid-cols-2 gap-x-4 gap-y-1.5 rounded-xl bg-white/[0.04] px-4 py-3 text-[12px]">
              <Row label="Customer" value={state.userId} mono />
              {/* 파트너 귀속 — 주문 시점 스냅샷. 없으면 직접 유입이며 정상이다.
                  이메일·주소·이름으로 추정하지 않는다: 서버가 코드로 확정한 값만 쓴다. */}
              <Row
                label="유입"
                value={
                  state.partnerName
                    ? `${PARTNER_TYPE_LABEL[state.partnerType ?? ""] ?? state.partnerType ?? "파트너"} · ${state.partnerName}`
                    : "직접 유입"
                }
                accent={Boolean(state.partnerId)}
              />
              {state.partnerId ? (
                <Row label="파트너 ID" value={state.partnerId} mono />
              ) : null}
              <Row label="Pet" value={state.petId} mono />
              <Row label="Product" value={PRODUCT_LABEL[state.productType] ?? state.productType} />
              <Row label="Payment" value={state.paymentStatus} accent={state.paymentStatus === "paid"} />
              <Row label="Soul Trace Letter" value={state.soulTraceLetterId ?? "—"} mono />
              {/* 식별자만으로는 "맞는 편지인가"를 알 수 없다 — 아이 이름과 발췌를
                  함께 보여 인쇄 전에 사람이 확인할 수 있게 한다. 본문은 오지 않는다. */}
              <Row label="아이 이름" value={state.letterChildName ?? "—"} />
              <Row label="편지 발췌" value={state.letterExcerpt ?? "—"} />
              {/* BREATHING 이 없으면 QR 을 찍어도 열리지 않는다. 인쇄 전에 막아야 한다. */}
              <Row
                label="BREATHING"
                value={state.breathingReady ? "READY" : "MISSING"}
                accent={state.breathingReady}
              />
              <Row label="Shaker QR" value={state.shakerShareId ?? "미연결"} mono />
              <Row
                label="QR 주소"
                value={
                  state.qrShareUrl ??
                  (state.qrArtifactStored ? "보관된 산출물 사용" : "—")
                }
                mono
              />
              <Row label="Production" value={state.productionStatus} />
              <Row label="Shipping" value={state.shippingStatus} />
              <Row label="Tracking" value={state.trackingNumber ?? "—"} />
              <Row label="Recipient" value={state.recipientName ?? "—"} />
            </dl>

            <div className="mt-2 flex gap-1">
              {STEPS.map((s, i) => (
                <span
                  key={s}
                  className={`flex-1 rounded-full py-1 text-center text-[10px] ${
                    i <= productionStep(state.productionStatus)
                      ? "bg-[#d8c9a8]/25 text-[#EDE3CE]"
                      : "bg-white/5 text-white/30"
                  }`}
                >
                  {s}
                </span>
              ))}
            </div>

            {actions.blockedReason && (
              <p className="mt-2 text-[11px] text-[#d99]">{actions.blockedReason}</p>
            )}
          </section>

          {/* 3. 생산 준비 */}
          <section className="mb-7">
            <h2 className="mb-2 text-sm font-medium text-white/80">3. 생산 준비</h2>
            {!state.packageReady && (
              <div className="mb-2 flex flex-col gap-2">
                <input
                  value={qrUrlInput}
                  onChange={(e) => setQrUrlInput(e.target.value)}
                  placeholder="Shaker 공유 URL (보관된 QR 이 있으면 비워 두세요)"
                  className="rounded-lg bg-white/5 px-3 py-2 text-xs outline-none placeholder:text-white/25"
                />
                <input
                  value={photoInput}
                  onChange={(e) => setPhotoInput(e.target.value)}
                  placeholder="사진 카드 이미지 URL (메모리 박스)"
                  className="rounded-lg bg-white/5 px-3 py-2 text-xs outline-none placeholder:text-white/25"
                />
              </div>
            )}
            <button
              type="button"
              disabled={!actions.canPrepare || busy !== null}
              onClick={() =>
                void run("prepare", () =>
                  prepareProduction(state.orderId, token as string, {
                    qrShareUrl: qrUrlInput.trim() || null,
                    photoImageUrl: photoInput.trim() || null,
                  })
                )
              }
              className="rounded-full bg-[#d8c9a8]/20 px-4 py-2 text-sm text-[#EDE3CE] disabled:opacity-40"
            >
              {busy === "prepare" ? "준비 중…" : "Prepare Production"}
            </button>
            <p className="mt-1.5 text-[11px] text-white/35">
              멱등입니다 — 다시 눌러도 같은 패키지가 나오고 QR·편지는 다시 만들어지지 않습니다.
            </p>
          </section>

          {/* 4. 파일 */}
          {state.packageReady && (
            <section className="mb-7">
              <h2 className="mb-2 text-sm font-medium text-white/80">4. 생산 파일</h2>
              <ul className="space-y-1.5">
                {state.files.map((k) => (
                  <li key={k} className="flex items-center gap-2 rounded-lg bg-white/5 px-3 py-2 text-xs">
                    <span className="text-white/70">{FILE_LABEL[k] ?? k}</span>
                    <span className="text-emerald-300/70">READY</span>
                    <button
                      type="button"
                      onClick={() => void doPreview(k)}
                      disabled={busy !== null}
                      className="ml-auto rounded-full border border-white/20 px-2.5 py-1 text-[11px] disabled:opacity-40"
                    >
                      Preview
                    </button>
                    <button
                      type="button"
                      onClick={() => void doDownload(k)}
                      disabled={busy !== null}
                      className="rounded-full border border-white/20 px-2.5 py-1 text-[11px] disabled:opacity-40"
                    >
                      Download
                    </button>
                  </li>
                ))}
              </ul>

              <div className="mt-2 flex flex-wrap gap-2">
                <button
                  type="button"
                  onClick={() => void doZip()}
                  disabled={busy !== null}
                  className="rounded-full bg-white/10 px-3.5 py-1.5 text-xs disabled:opacity-40"
                >
                  {busy === "zip" ? "묶는 중…" : "Download Full ZIP"}
                </button>
                {state.shakerShareId && (
                  <>
                    <button
                      type="button"
                      onClick={() => void doQrAgain("svg")}
                      disabled={busy !== null}
                      className="rounded-full border border-white/20 px-3.5 py-1.5 text-xs disabled:opacity-40"
                    >
                      Download QR Again (SVG)
                    </button>
                    <button
                      type="button"
                      onClick={() => void doQrAgain("png")}
                      disabled={busy !== null}
                      className="rounded-full border border-white/20 px-3.5 py-1.5 text-xs disabled:opacity-40"
                    >
                      QR (PNG)
                    </button>
                  </>
                )}
              </div>
              <p className="mt-1.5 text-[11px] text-white/35">
                다시 받은 QR 은 처음 것과 같은 파일입니다 — 이미 인쇄된 QR 이 계속 유효합니다.
              </p>

              {preview && (
                <div className="mt-3 rounded-xl bg-white p-2">
                  {preview.kind === "letter_pdf" ? (
                    <iframe title="letter preview" src={preview.url} className="h-[520px] w-full" />
                  ) : (
                    <img src={preview.url} alt={preview.kind} className="mx-auto max-h-[320px]" />
                  )}
                </div>
              )}
            </section>
          )}

          {/* 5. 생산 · 배송 */}
          <section>
            <h2 className="mb-2 text-sm font-medium text-white/80">5. 생산 · 배송</h2>
            <div className="flex flex-wrap gap-2">
              <Action
                label="Start Production"
                busy={busy === "start"}
                disabled={!actions.canStart || busy !== null}
                onClick={() => void run("start", () => startProduction(state.orderId, token as string))}
              />
              <Action
                label="Mark Produced"
                busy={busy === "produced"}
                disabled={!actions.canMarkProduced || busy !== null}
                onClick={() => void run("produced", () => markProduced(state.orderId, token as string))}
              />
              <Action
                label="Mark Shipped"
                busy={busy === "ship"}
                disabled={!actions.canShip || busy !== null}
                onClick={() => void run("ship", () => markShipped(state.orderId, token as string))}
              />
              <Action
                label="Mark Delivered"
                busy={busy === "delivered"}
                disabled={!actions.canMarkDelivered || busy !== null}
                onClick={() => void run("delivered", () => markDelivered(state.orderId, token as string))}
              />
            </div>

            <div className="mt-3 flex gap-2">
              <input
                value={trackingInput}
                onChange={(e) => setTrackingInput(e.target.value)}
                placeholder="송장 번호"
                className="flex-1 rounded-lg bg-white/5 px-3 py-2 text-xs outline-none placeholder:text-white/25"
              />
              <button
                type="button"
                disabled={!actions.canAddTracking || !trackingInput.trim() || busy !== null}
                onClick={() =>
                  void run("tracking", () =>
                    addTracking(state.orderId, token as string, trackingInput.trim())
                  )
                }
                className="rounded-lg bg-white/10 px-4 py-2 text-xs disabled:opacity-40"
              >
                Add Tracking
              </button>
            </div>

            {shipBlockedReason(state) && (
              <p className="mt-2 text-[11px] text-white/40">{shipBlockedReason(state)}</p>
            )}
          </section>
        </>
      )}
    </div>
  );
}

function Row({
  label, value, mono, accent,
}: { label: string; value: string; mono?: boolean; accent?: boolean }) {
  return (
    <>
      <dt className="text-white/40">{label}</dt>
      <dd
        className={`break-all ${mono ? "font-mono text-[11px]" : ""}`}
        style={{ color: accent ? "#8fe3a8" : "#D8D8D8" }}
      >
        {value}
      </dd>
    </>
  );
}

function Action({
  label, onClick, disabled, busy,
}: { label: string; onClick: () => void; disabled: boolean; busy: boolean }) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      className="rounded-full border border-white/20 px-3.5 py-1.5 text-xs disabled:opacity-40"
    >
      {busy ? "처리 중…" : label}
    </button>
  );
}
