"use client";

/**
 * 실물 주문 확인 화면 — Toss 결제창에서 돌아온 직후.
 *
 *   /orders/success?paymentKey=…&orderId=…&amount=…  → 서버 확인 → 주문 PAID
 *   /orders/fail?code=…&message=…                    → 안내만
 *
 * 테마 복귀 화면과 **분리한** 이유: 보여 줘야 하는 것이 다르다. 실물은 주문번호·
 * 아이·제품·수령인·결제 상태를 확인시켜야 한다 — 고객이 "무엇이 어디로 가는지"를
 * 이 화면에서 마지막으로 본다.
 *
 * ⚠️ confirm 은 **한 번만** 부른다. 서버가 멱등이라 재승인되지는 않지만, 화면이
 *    반복 호출할 이유가 없다.
 *
 * ⚠️ 확인이 실패해도 **재조정 안전망이 있다** — 결제가 실제로 승인됐다면 다음에
 *    앱을 열 때 주문이 PAID 로 정리된다. 그 사실을 사용자에게 말해 준다.
 */

import { useCallback, useEffect, useRef, useState } from "react";

import { orderReturnEntry, readThemeReturnParams } from "@/lib/app-entry";
import { getPremiumAccessToken } from "@/lib/premium-auth-token";
import {
  OrderApiError,
  confirmOrderPayment,
  findMyOrder,
  reconcileMyOrders,
  type PhysicalOrder,
} from "@/lib/orders-api";
import { describeOrderStatus, formatKrw } from "@/lib/order-checkout-flow";

const PRODUCT_LABEL: Record<string, string> = {
  LETTER: "편지",
  MEMORY_BOX: "메모리 박스",
};

const MESSAGES: Record<string, string> = {
  ORDER_NOT_FOUND: "주문을 찾을 수 없습니다.",
  ORDER_NOT_PENDING: "이미 종료된 주문입니다.",
  ORDER_AMOUNT_MISMATCH: "주문 금액이 맞지 않습니다.",
  ORDER_PAYMENT_FAILED: "결제가 완료되지 않았습니다.",
  UNAUTHENTICATED: "로그인이 필요합니다.",
};

type Phase =
  | { kind: "working" }
  | { kind: "done"; order: PhysicalOrder | null; alreadyPaid: boolean }
  | { kind: "failed"; message: string; reconciling: boolean };

export function OrderConfirmationScreen() {
  const outcome = orderReturnEntry();
  const [phase, setPhase] = useState<Phase>({ kind: "working" });
  const startedRef = useRef(false);

  useEffect(() => {
    if (startedRef.current) return;
    startedRef.current = true;

    const params = readThemeReturnParams(window.location.search);

    if (outcome === "fail") {
      setPhase({
        kind: "failed",
        message: params.message || "결제가 취소되었습니다.",
        reconciling: false,
      });
      return;
    }

    if (!params.paymentKey || !params.orderId) {
      setPhase({
        kind: "failed",
        message: "결제 정보를 확인할 수 없습니다.",
        reconciling: false,
      });
      return;
    }

    void (async () => {
      const auth = await getPremiumAccessToken();
      if (!auth.token) {
        setPhase({ kind: "failed", message: MESSAGES.UNAUTHENTICATED, reconciling: false });
        return;
      }
      const token = auth.token;
      try {
        const r = await confirmOrderPayment({
          paymentKey: params.paymentKey as string,
          orderId: params.orderId as string,
          amount: params.amount,
          accessToken: token,
        });
        const order = await findMyOrder({ orderId: r.orderId, accessToken: token }).catch(
          () => null
        );
        setPhase({ kind: "done", order, alreadyPaid: r.alreadyPaid });
      } catch (e) {
        // 확인이 실패해도 결제 자체는 승인됐을 수 있다. 재조정을 한 번 돌려
        // 실제로 승인돼 있으면 지금 바로 정리한다 — 사용자를 불안한 채로 두지 않는다.
        const confirmed = await reconcileMyOrders({ accessToken: token });
        if (params.orderId && confirmed.includes(params.orderId)) {
          const order = await findMyOrder({
            orderId: params.orderId, accessToken: token,
          }).catch(() => null);
          setPhase({ kind: "done", order, alreadyPaid: true });
          return;
        }
        const code = e instanceof OrderApiError ? e.code : "UNKNOWN";
        setPhase({
          kind: "failed",
          message: MESSAGES[code] || "결제를 확인하지 못했습니다.",
          reconciling: true,
        });
      }
    })();
  }, [outcome]);

  const goBack = useCallback(() => window.location.replace("/"), []);

  return (
    <div className="fixed inset-0 flex flex-col items-center justify-center gap-4 bg-[#0a0a0a] px-8">
      {phase.kind === "working" && (
        <p className="text-sm text-white/50">결제를 확인하는 중…</p>
      )}

      {phase.kind === "done" && (
        <>
          <p className="text-base font-medium text-[#EDE3CE]">
            {phase.alreadyPaid ? "이미 접수된 주문입니다" : "주문이 접수되었습니다"}
          </p>

          {phase.order && (
            <dl className="mt-1 w-full max-w-sm rounded-2xl border border-white/12 bg-white/[0.03] px-4 py-4 text-[12px]">
              <Row label="주문번호" value={phase.order.orderId} mono />
              <Row
                label="제품"
                value={PRODUCT_LABEL[phase.order.productType] ?? phase.order.productType}
              />
              <Row label="금액" value={formatKrw(phase.order.amount)} accent />
              <Row label="아이" value={phase.order.petId} mono />
              {phase.order.soulTraceLetterId && (
                <Row label="편지" value={phase.order.soulTraceLetterId} mono />
              )}
              <Row label="상태" value={describeOrderStatus(phase.order)} />
            </dl>
          )}

          <p className="max-w-xs text-[11px] leading-relaxed text-white/40">
            제작·배송은 결제 확인 후 시작됩니다. 진행 상황은 기념품 화면에서 볼 수 있습니다.
          </p>
        </>
      )}

      {phase.kind === "failed" && (
        <>
          <p className="text-base font-medium text-white/90">결제를 완료하지 못했습니다</p>
          <p className="max-w-xs text-sm leading-relaxed text-white/55">{phase.message}</p>
          {phase.reconciling && (
            // 결제가 실제로 승인됐다면 다음 방문에서 주문이 정리된다.
            <p className="max-w-xs text-[11px] leading-relaxed text-white/35">
              결제가 이미 승인되었다면 잠시 후 자동으로 주문에 반영됩니다.
              중복 결제하지 마시고 기념품 화면에서 확인해 주세요.
            </p>
          )}
        </>
      )}

      {phase.kind !== "working" && (
        <button
          type="button"
          onClick={goBack}
          className="mt-2 rounded-full border border-white/20 px-5 py-2 text-sm text-white/80 active:bg-white/10"
        >
          돌아가기
        </button>
      )}
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
    <div className="flex gap-3 py-1">
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
