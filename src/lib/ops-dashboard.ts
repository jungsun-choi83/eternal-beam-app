/**
 * 대시보드 집계 — **순수 함수.** 화면은 그리기만 한다.
 *
 * ── 왜 목록 데이터만 쓰는가 ─────────────────────────────────────────────────
 * 주문 상세를 주문마다 부르면 대시보드 한 번에 N 개의 요청이 나간다. 스태프가
 * 하루에 수십 번 여는 화면에서 그것은 그대로 서버 부하이고, 느린 대시보드는
 * 아무도 보지 않는다.
 *
 * 그래서 검색 결과 **한 번**으로 계산한다.
 *
 * ── 처리 필요 판정은 서버가 준다 ───────────────────────────────────────────
 * 목록 행만으로는 답할 수 없는 질문이 있었다 — 메모리 박스의 사진 카드 원본이
 * 있는가. 그것은 생산 패키지에 있어서, 예전에는 주문마다 상세를 부르는 수밖에
 * 없었다. 이제 서버가 패키지를 **한 번의 일괄 질의**로 읽어 판정해 보내 준다
 * (backend/services/order_attention.py). 여기서는 그 값을 그대로 쓰고, 없을
 * 때만(구버전 응답) 행에서 유도한다.
 */

export interface DashboardOrder {
  orderId: string;
  petId: string;
  productType: string;
  amount: number;
  paymentStatus: string;
  productionStatus: string;
  shippingStatus: string;
  trackingNumber: string | null;
  recipientName: string | null;
  /** 주문 생성 시각. 최근 순의 유일한 근거다. */
  createdAt?: string | null;
  /** 서버 판정. 있으면 **이것이 정본**이다 — 목록 행만으로는 알 수 없는
   *  상태(메모리 박스 사진 원본 등)까지 서버가 보고 판단한다. */
  needsAttention?: boolean;
  attentionCode?: string | null;
  attentionReason?: string | null;
}

export interface OpsCounts {
  paid: number;
  preparing: number;
  ready: number;
  shipping: number;
}

/**
 * 네 칸으로 접는다. 서버 상태 축이 두 개(production/shipping)라 그대로 보여
 * 주면 스태프가 조합을 머리로 계산해야 한다.
 *
 *   Paid       결제됐고 아직 생산 준비 전
 *   Preparing  준비됨 · 제작 중
 *   Ready      제작 완료, 아직 미발송
 *   Shipping   발송됨 · 배송 완료
 */
export function countOrders(rows: readonly DashboardOrder[]): OpsCounts {
  const c: OpsCounts = { paid: 0, preparing: 0, ready: 0, shipping: 0 };
  for (const o of rows) {
    const ship = (o.shippingStatus || "").toLowerCase();
    const prod = (o.productionStatus || "").toLowerCase();
    if (ship === "shipped" || ship === "delivered") c.shipping += 1;
    else if (prod === "produced") c.ready += 1;
    else if (prod === "ready" || prod === "in_production") c.preparing += 1;
    else c.paid += 1;
  }
  return c;
}

export type AttentionKind =
  | "not_prepared"
  | "tracking_missing"
  | "shipped_without_tracking"
  /** 서버가 준 사유 — 프론트가 아는 목록에 없을 수 있다(예: PHOTO_MISSING). */
  | "server";

export interface AttentionItem {
  orderId: string;
  kind: AttentionKind;
  reason: string;
  order: DashboardOrder;
}

const REASON: Record<AttentionKind, string> = {
  not_prepared: "결제됐지만 생산 준비가 아직입니다.",
  tracking_missing: "제작이 끝났지만 송장이 없어 발송할 수 없습니다.",
  shipped_without_tracking: "송장 없이 발송으로 표시되어 있습니다.",
};

/**
 * 지금 사람이 손을 대야 하는 주문.
 *
 * **이미 있는 상태**에서만 뽑는다 — 새 신호를 만들지 않는다. 각 항목은
 * "무엇을 하면 사라지는가"가 분명해야 하고, 그렇지 않은 것은 넣지 않는다.
 */
export function needsAttention(rows: readonly DashboardOrder[]): AttentionItem[] {
  const out: AttentionItem[] = [];
  for (const o of rows) {
    // ── 서버 판정이 있으면 그것을 쓴다 ───────────────────────────────────
    // 서버는 생산 패키지까지 보고 판단하므로 목록 행만으로는 알 수 없는 사유
    // (메모리 박스 사진 원본 누락)도 낸다. 프론트가 다시 계산하면 두 규칙이
    // 갈라지고, 갈라지는 순간 대시보드와 상세가 서로 다른 말을 한다.
    if (typeof o.needsAttention === "boolean") {
      if (o.needsAttention) {
        out.push({
          orderId: o.orderId,
          kind: "server",
          reason: o.attentionReason || "확인이 필요합니다.",
          order: o,
        });
      }
      continue;
    }

    const ship = (o.shippingStatus || "").toLowerCase();
    const prod = (o.productionStatus || "").toLowerCase();
    const hasTracking = Boolean((o.trackingNumber || "").trim());

    if ((ship === "shipped" || ship === "delivered") && !hasTracking) {
      out.push({ orderId: o.orderId, kind: "shipped_without_tracking", reason: REASON.shipped_without_tracking, order: o });
      continue;
    }
    if (ship !== "shipped" && ship !== "delivered") {
      if (prod === "produced" && !hasTracking) {
        out.push({ orderId: o.orderId, kind: "tracking_missing", reason: REASON.tracking_missing, order: o });
        continue;
      }
      if (prod === "pending") {
        out.push({ orderId: o.orderId, kind: "not_prepared", reason: REASON.not_prepared, order: o });
      }
    }
  }
  return out;
}

/**
 * 최근 주문 — **created_at 내림차순.**
 *
 * 예전에는 주문 id 역순으로 정렬했다. id 에는 시각이 들어 있지 않으므로 그것은
 * 최근순이 아니라 "흔들리지 않는 임의 순서"였을 뿐이고, 스태프에게는 최근
 * 주문이라고 적힌 목록이 실제로는 아무 순서도 아니었다.
 *
 * created_at 이 없는 행(구버전 응답)은 **뒤로 보낸다** — 앞에 섞이면 진짜 최근
 * 주문을 밀어낸다. 동률은 id 로 갈라 호출마다 순서가 바뀌지 않게 한다.
 */
export function recentOrders(
  rows: readonly DashboardOrder[],
  limit = 8
): DashboardOrder[] {
  return [...rows]
    .sort((a, b) => {
      const at = (a.createdAt || "").trim();
      const bt = (b.createdAt || "").trim();
      if (at && bt && at !== bt) return bt.localeCompare(at);
      if (at && !bt) return -1;
      if (!at && bt) return 1;
      return b.orderId.localeCompare(a.orderId);
    })
    .slice(0, limit);
}
