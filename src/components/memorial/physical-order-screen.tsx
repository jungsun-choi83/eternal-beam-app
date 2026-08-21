"use client";

/**
 * 실물 구매 화면 (Phase 12) — LETTER ₩14,900 / MEMORY BOX ₩49,000.
 *
 *   제품 선택 → 배송지 → **주문 확인** → Toss 결제창 → (/orders/success) → 확인 완료
 *
 * 한 화면 안에서 단계를 넘긴다. 메인 앱의 화면 열거형에 네 개를 밀어 넣지 않기
 * 위해서다 — 구매 흐름은 한 덩어리이고, 중간 단계가 앱 네비게이션에 노출될 이유가 없다.
 *
 * ── 이 화면이 만들지 않는 것 ────────────────────────────────────────────────
 *   * AI 편지 — Soul Trace 가 만든 것을 **연결**만 한다
 *   * 펫 — 이미 있는 canonical petId 를 가리킨다
 *   * Shaker 공유 — 서버가 기존 것을 재사용한다
 * 판정은 전부 lib/order-checkout-flow.ts 의 순수 함수가 한다.
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import { ArrowLeft, Check, Package } from "lucide-react";

import { ShippingAddressScreen } from "@/components/memorial/shipping-address-screen";
import {
  readShippingAddress,
  type ShippingAddress,
} from "@/lib/finalize-preview-content";
import { getPremiumAccessToken } from "@/lib/premium-auth-token";
import {
  OrderApiError,
  fetchMyOrders,
  fetchProducts,
  fetchMyLetters,
  openOrderPaymentWindow,
  reconcileMyOrders,
  startOrderCheckout,
  type PhysicalOrder,
  type PhysicalProduct,
} from "@/lib/orders-api";
import {
  STEP_PRODUCT,
  STEP_REVIEW,
  STEP_SHIPPING,
  buildReview,
  describeOrderStatus,
  formatKrw,
  nextStep,
  orderBlockers,
  previousStep,
  type OrderDraft,
  type OrderStep,
} from "@/lib/order-checkout-flow";

const PRODUCT_LABEL: Record<string, string> = {
  LETTER: "편지",
  MEMORY_BOX: "메모리 박스",
};

const CONTENT_LABEL: Record<string, string> = {
  printed_letter: "인쇄된 AI 편지",
  envelope: "봉투",
  qr: "QR",
  photo_card: "반려 사진 카드",
  qr_memory_card: "QR 메모리 카드",
  rigid_box: "하드 케이스",
  black_tissue: "블랙 티슈",
  message_card: "메시지 카드",
};

const BLOCKER_TEXT: Record<string, string> = {
  "signed-out": "로그인이 필요합니다.",
  "no-pet": "먼저 아이의 영상을 만들어 주세요.",
  // ⚠️ 여기서 편지를 만들어 주지 않는다 — Soul Trace 가 만든 편지를 연결해야 한다.
  "no-letter": "Soul Trace 편지를 먼저 연결해 주세요.",
  "no-product": "제품을 선택해 주세요.",
  "incomplete-shipping": "배송지를 입력해 주세요.",
};

interface PhysicalOrderScreenProps {
  /** 이미 존재하는 canonical petId. 없으면 구매할 수 없다. */
  petId: string | null;
  onBack: () => void;
}

export function PhysicalOrderScreen({ petId, onBack }: PhysicalOrderScreenProps) {
  /**
   * 연결된 Soul Trace 편지. **앱 상태에서 오지 않는다** — Soul Trace 는 아직
   * 백엔드가 없고, 편지는 link seam(POST /orders/letter/link)을 통해서만 서버에
   * 존재한다. 그래서 화면이 서버에 직접 묻는다.
   *
   * 없으면 구매를 막는다. **여기서 편지를 만들어 주지 않는다.**
   */
  const [soulTraceLetterId, setSoulTraceLetterId] = useState<string | null>(null);
  const [token, setToken] = useState<string | null>(null);
  const [tokenLoaded, setTokenLoaded] = useState(false);
  const [products, setProducts] = useState<PhysicalProduct[]>([]);
  const [orders, setOrders] = useState<PhysicalOrder[]>([]);
  const [step, setStep] = useState<OrderStep>(STEP_PRODUCT);
  const [productType, setProductType] = useState<string | null>(null);
  const [shipping, setShipping] = useState<ShippingAddress | null>(() =>
    readShippingAddress()
  );
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    void getPremiumAccessToken().then((r) => {
      setToken(r.token);
      setTokenLoaded(true);
    });
  }, []);

  useEffect(() => {
    void fetchProducts().then(setProducts).catch(() => setProducts([]));
  }, []);

  // 이 펫에 연결된 편지를 고른다. 펫에 묶이지 않은 편지는 마지막 폴백이다
  // (Soul Trace 만 하고 펫을 나중에 만든 경우).
  useEffect(() => {
    if (!token) return;
    void fetchMyLetters({ accessToken: token })
      .then((rows) => {
        const forPet = rows.find((l) => l.petId && l.petId === petId);
        setSoulTraceLetterId((forPet ?? rows[0])?.letterId ?? null);
      })
      .catch(() => setSoulTraceLetterId(null));
  }, [token, petId]);

  // 내 주문 + **재조정**. 결제 직후 브라우저가 닫혔던 주문이 여기서 정리된다.
  const refreshOrders = useCallback(async () => {
    if (!token) return;
    try {
      // 재조정을 먼저 부른다 — 그래야 아래 목록이 정리된 상태를 보여 준다.
      await reconcileMyOrders({ accessToken: token });
      setOrders(await fetchMyOrders({ accessToken: token }));
    } catch {
      /* 목록 실패가 구매를 막지 않는다 */
    }
  }, [token]);

  useEffect(() => {
    void refreshOrders();
  }, [refreshOrders]);

  const draft: OrderDraft = useMemo(
    () => ({ petId, soulTraceLetterId, productType, shipping, hasAuth: Boolean(token) }),
    [petId, soulTraceLetterId, productType, shipping, token]
  );

  const price = useMemo(
    () => products.find((p) => p.productType === productType)?.priceKrw ?? 0,
    [products, productType]
  );
  const review = useMemo(() => buildReview(draft, price), [draft, price]);
  const blockers = useMemo(() => orderBlockers(draft), [draft]);

  const pay = useCallback(async () => {
    if (!token || !review) return;
    setBusy(true);
    setError(null);
    try {
      const checkout = await startOrderCheckout({
        petId: review.petId,
        productType: review.productType,
        soulTraceLetterId: review.soulTraceLetterId,
        shipping: {
          recipientName: review.recipientName,
          recipientPhone: (shipping as ShippingAddress).phone,
          postalCode: (shipping as ShippingAddress).postalCode,
          addressLine1: (shipping as ShippingAddress).addressLine1,
          addressLine2: (shipping as ShippingAddress).addressLine2 ?? null,
        },
        accessToken: token,
      });
      // 페이지가 결제창으로 이동한다 — 이 아래는 실행되지 않는다.
      await openOrderPaymentWindow(checkout);
    } catch (e) {
      setError(e instanceof OrderApiError ? e.message : "결제를 시작하지 못했습니다.");
      setBusy(false);
    }
  }, [token, review, shipping]);

  const shell = "flex h-full min-h-0 flex-col overflow-hidden bg-[#0a0a0a] text-[#EDE3CE]";

  if (!tokenLoaded) {
    return (
      <div className={`${shell} items-center justify-center`}>
        <p className="text-sm text-white/40">불러오는 중…</p>
      </div>
    );
  }

  // ── 배송지 단계는 기존 화면을 그대로 재사용한다 ────────────────────────────
  if (step === STEP_SHIPPING) {
    return (
      <ShippingAddressScreen
        initialAddress={shipping}
        submitLabel="주문 확인"
        onSubmitAddress={(a) => setShipping(a)}
        onComplete={() => setStep(STEP_REVIEW)}
        onBack={() => setStep(previousStep(STEP_SHIPPING) ?? STEP_PRODUCT)}
      />
    );
  }

  return (
    <div className={shell}>
      <header className="shrink-0 px-5 pt-[max(2.75rem,env(safe-area-inset-top,0px))] pb-3 flex items-center gap-2">
        <button
          type="button"
          onClick={() => {
            const prev = previousStep(step);
            if (prev) setStep(prev);
            else onBack();
          }}
          className="p-1"
          aria-label="뒤로"
        >
          <ArrowLeft className="h-5 w-5 text-white/70" />
        </button>
        <p className="text-sm font-medium">
          {step === STEP_REVIEW ? "주문 확인" : "기념품"}
        </p>
      </header>

      <div className="flex-1 min-h-0 overflow-y-auto hide-scrollbar px-5 pb-8">
        {/* ── 제품 선택 ─────────────────────────────────────────────────── */}
        {step === STEP_PRODUCT && (
          <>
            <p className="mb-4 text-xs leading-relaxed text-white/45">
              Soul Trace 편지와 QR 을 담아 실물로 보내 드립니다.
              <br />
              숨쉬기(BREATHING)는 언제나 무료이며, 이 주문과 무관합니다.
            </p>

            <div className="flex flex-col gap-3">
              {products.map((p) => {
                const selected = productType === p.productType;
                return (
                  <button
                    key={p.productType}
                    type="button"
                    onClick={() => setProductType(p.productType)}
                    className={`rounded-2xl border px-4 py-3.5 text-left ${
                      selected ? "border-[#c9a227] bg-white/[0.06]" : "border-white/12 bg-white/[0.03]"
                    }`}
                  >
                    <div className="flex items-center gap-2">
                      <Package className="h-4 w-4 shrink-0 text-[#d8c9a8]" />
                      <span className="text-sm font-medium">
                        {PRODUCT_LABEL[p.productType] ?? p.productType}
                      </span>
                      <span className="ml-auto text-sm text-[#f5d77a]">
                        {formatKrw(p.priceKrw)}
                      </span>
                      {selected && <Check className="h-4 w-4 text-[#c9a227]" />}
                    </div>
                    <p className="mt-1.5 text-[11px] leading-relaxed text-white/45">
                      {p.contents.map((c) => CONTENT_LABEL[c] ?? c).join(" · ")}
                    </p>
                  </button>
                );
              })}
            </div>

            {blockers.filter((b) => b !== "no-product" && b !== "incomplete-shipping").map((b) => (
              <p key={b} className="mt-3 text-[11px] text-[#d99]">
                {BLOCKER_TEXT[b]}
              </p>
            ))}

            <button
              type="button"
              disabled={!productType}
              onClick={() => setStep(nextStep(STEP_PRODUCT))}
              className="mt-5 w-full rounded-full bg-[#d8c9a8]/20 py-3 text-sm text-[#EDE3CE] disabled:opacity-40"
            >
              배송지 입력
            </button>

            {/* 내 주문 — 재조정이 끝난 상태를 보여 준다. */}
            {orders.length > 0 && (
              <section className="mt-8">
                <p className="mb-2 text-xs text-white/50">내 주문</p>
                <ul className="flex flex-col gap-1.5">
                  {orders.map((o) => (
                    <li
                      key={o.orderId}
                      className="rounded-xl bg-white/[0.04] px-3 py-2 text-[11px] text-white/60"
                    >
                      <span className="font-mono text-white/70">{o.orderId.slice(0, 18)}</span>
                      <span className="ml-2">{PRODUCT_LABEL[o.productType] ?? o.productType}</span>
                      <span className="ml-2 text-white/45">{describeOrderStatus(o)}</span>
                    </li>
                  ))}
                </ul>
              </section>
            )}
          </>
        )}

        {/* ── 주문 확인 (결제 직전) ─────────────────────────────────────── */}
        {step === STEP_REVIEW && review && (
          <>
            <dl className="flex flex-col gap-2.5 rounded-2xl border border-white/12 bg-white/[0.03] px-4 py-4 text-[12px]">
              <Row label="제품" value={PRODUCT_LABEL[review.productType] ?? review.productType} />
              <Row label="금액" value={formatKrw(review.priceKrw)} accent />
              <Row label="아이" value={review.petId} mono />
              <Row label="편지" value={review.soulTraceLetterId} mono />
              <Row label="받는 분" value={review.recipientName} />
              <Row label="연락처" value={review.phone} />
              <Row label="주소" value={review.address} />
            </dl>

            <p className="mt-3 text-[11px] leading-relaxed text-white/40">
              결제하면 주문이 접수됩니다. 제작·배송은 결제 확인 후 시작됩니다.
            </p>

            {error && <p className="mt-3 text-[11px] text-[#d99]">{error}</p>}

            <button
              type="button"
              disabled={busy}
              onClick={() => void pay()}
              className="mt-5 w-full rounded-full bg-[#d8c9a8]/25 py-3 text-sm text-[#EDE3CE] disabled:opacity-40"
            >
              {busy ? "결제창을 여는 중…" : `${formatKrw(review.priceKrw)} 결제하기`}
            </button>
          </>
        )}

        {step === STEP_REVIEW && !review && (
          <div className="flex flex-col gap-2">
            {blockers.map((b) => (
              <p key={b} className="text-[12px] text-[#d99]">
                {BLOCKER_TEXT[b]}
              </p>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

function Row({
  label,
  value,
  mono,
  accent,
}: {
  label: string;
  value: string;
  mono?: boolean;
  accent?: boolean;
}) {
  return (
    <div className="flex gap-3">
      <dt className="w-16 shrink-0 text-white/40">{label}</dt>
      <dd
        className={`flex-1 break-all ${mono ? "font-mono text-[11px]" : ""}`}
        style={{ color: accent ? "#f5d77a" : "#D8D8D8" }}
      >
        {value}
      </dd>
    </div>
  );
}
