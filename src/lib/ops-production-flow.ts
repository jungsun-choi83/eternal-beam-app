/**
 * 운영 생산 콘솔의 **순수 판정** — 어떤 버튼을 켜도 되는가.
 *
 * 상태 기계는 서버(physical_order.PRODUCTION_FLOW / SHIPPING_FLOW)가 쥔다.
 * 여기서는 같은 규칙을 **표시용으로** 다시 쓴다 — 눌러도 409 가 나는 버튼을
 * 보여 주지 않기 위해서다. 서버가 여전히 최종 판정을 하므로, 이 함수가 틀려도
 * 잘못된 전이가 일어나지는 않는다(느슨한 쪽으로 틀리면 오류 메시지가 뜰 뿐이다).
 */

export type ProductionStatus = "pending" | "ready" | "in_production" | "produced";
export type ShippingStatus = "pending" | "shipped" | "delivered";

export interface OpsOrderView {
  paymentStatus: string;
  productionStatus: string;
  shippingStatus: string;
  trackingNumber: string | null;
  packageReady: boolean;
  files: string[];
}

/** 결제된 주문만 생산에 들어간다 — 서버와 같은 규칙. */
export function isPaid(o: Pick<OpsOrderView, "paymentStatus">): boolean {
  return o.paymentStatus === "paid";
}

export interface OpsActions {
  canPrepare: boolean;
  canPreview: boolean;
  canDownload: boolean;
  canStart: boolean;
  canMarkProduced: boolean;
  canAddTracking: boolean;
  canShip: boolean;
  canMarkDelivered: boolean;
  /** 버튼이 꺼진 이유 — 화면이 안내 문구를 정한다. */
  blockedReason: string | null;
}

export function opsActions(o: OpsOrderView): OpsActions {
  const paid = isPaid(o);
  const prep = o.packageReady;

  const off: OpsActions = {
    canPrepare: false, canPreview: false, canDownload: false, canStart: false,
    canMarkProduced: false, canAddTracking: false, canShip: false,
    canMarkDelivered: false, blockedReason: null,
  };

  if (!paid) {
    // 돈을 받기 전에 인쇄하면 취소 시 그대로 손실이다.
    return { ...off, blockedReason: "결제된 주문만 생산할 수 있습니다." };
  }

  return {
    // 준비는 멱등이라 이미 준비돼도 다시 눌러도 된다(같은 패키지가 돌아온다).
    canPrepare: true,
    canPreview: prep,
    canDownload: prep,
    canStart: prep && o.productionStatus === "ready",
    canMarkProduced: o.productionStatus === "in_production",
    // 송장은 언제든 등록할 수 있다 — 발송보다 먼저 받아 두는 것이 보통이다.
    canAddTracking: true,
    // 만들지 않은 것을 보낼 수 없고, 송장 없는 발송은 문의만 만든다.
    canShip:
      o.productionStatus === "produced" &&
      o.shippingStatus === "pending" &&
      Boolean((o.trackingNumber || "").trim()),
    canMarkDelivered: o.shippingStatus === "shipped",
    blockedReason: null,
  };
}

/** 발송 버튼이 꺼진 구체적 이유 — "왜 안 눌리지"를 없앤다. */
export function shipBlockedReason(o: OpsOrderView): string | null {
  if (!isPaid(o)) return "결제된 주문만 생산할 수 있습니다.";
  if (o.shippingStatus !== "pending") return null;
  if (o.productionStatus !== "produced") return "생산이 완료되어야 발송할 수 있습니다.";
  if (!(o.trackingNumber || "").trim()) return "송장 번호를 먼저 등록하세요.";
  return null;
}

/** 진행 단계 표시(0~4). Ops 화면의 진척 바. */
export function productionStep(status: string): number {
  return { pending: 0, ready: 1, in_production: 2, produced: 3 }[status] ?? 0;
}

export const FILE_LABEL: Record<string, string> = {
  letter_pdf: "Letter PDF",
  photo_card: "Photo Card",
  qr_card: "QR Memory Card",
  message_card: "Message Card",
};

/** 구성 파일의 저장 파일명 — 서버 Content-Disposition 과 같은 규칙. */
export function fileName(orderId: string, kind: string): string {
  if (kind === "letter_pdf") return `${orderId}-letter-a5.pdf`;
  if (kind === "photo_card") return `${orderId}-photo-card-85x55.png`;
  if (kind === "qr_card") return `${orderId}-qr-card-85x55.png`;
  // 문구 미승인 상태에서는 서버가 교정지를 주고 파일명도 다르다 — 인쇄용
  // 파일과 섞이지 않도록 이름 자체가 다르게 남아야 한다.
  if (kind === "message_card") return `${orderId}-message-card-85x55.png`;
  return `${orderId}-${kind}`;
}
