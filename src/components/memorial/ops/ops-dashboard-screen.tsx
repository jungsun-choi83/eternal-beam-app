"use client";

/**
 * Ops 대시보드 — `/ops`.
 *
 * 하루를 시작할 때 **무엇을 해야 하는가**만 답한다. 분석 도구가 아니다:
 * 차트도, 추세도, 매출도 없다. 스태프가 여기서 원하는 것은 "지금 손댈 주문"이고,
 * 그 외의 것은 화면을 느리게 만들 뿐이다.
 *
 * 데이터는 **이미 있는 검색 결과 한 번**으로 만든다(집계는 lib/ops-dashboard.ts).
 */

import { useCallback, useEffect, useState } from "react";

import { OpsLayout, type OpsChildProps } from "./ops-layout";
import {
  Button,
  Card,
  CardTitle,
  EmptyState,
  ErrorNote,
  OPS,
  Pill,
  statusTone,
  statusText,
} from "./ops-ui";
import { searchPaidOrders, type OpsOrderRow } from "@/lib/ops-production-api";
import { OPS_ORDERS_PATH } from "@/lib/ops-nav";
import {
  countOrders,
  needsAttention,
  recentOrders,
  type AttentionItem,
} from "@/lib/ops-dashboard";
import { formatKrw } from "@/lib/order-checkout-flow";

const PRODUCT_LABEL: Record<string, string> = {
  LETTER: "편지",
  MEMORY_BOX: "메모리 박스",
};

export function OpsDashboardScreen() {
  return (
    <OpsLayout active="dashboard" title="Dashboard" subtitle="오늘 처리할 주문">
      {(p) => <Body {...p} />}
    </OpsLayout>
  );
}

function Body({ token, onAuthError }: OpsChildProps) {
  const [rows, setRows] = useState<OpsOrderRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      setRows(await searchPaidOrders({ accessToken: token }));
      setError(null);
    } catch (e) {
      onAuthError(e);
      setError((e as { message?: string })?.message ?? "주문을 불러오지 못했습니다.");
    } finally {
      setLoading(false);
    }
  }, [token, onAuthError]);

  useEffect(() => {
    void load();
  }, [load]);

  const counts = countOrders(rows);
  const attention = needsAttention(rows);
  const recent = recentOrders(rows);

  return (
    <div className="mx-auto max-w-5xl space-y-5">
      {error ? <ErrorNote>{error}</ErrorNote> : null}

      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <Stat label="Paid" value={counts.paid} hint="생산 준비 전" />
        <Stat label="Preparing" value={counts.preparing} hint="준비 · 제작 중" />
        <Stat label="Ready" value={counts.ready} hint="제작 완료 · 미발송" />
        <Stat label="Shipping" value={counts.shipping} hint="발송 · 배송 완료" />
      </div>

      <Card>
        <CardTitle
          action={
            <Button size="sm" onClick={() => void load()} busy={loading}>
              새로고침
            </Button>
          }
        >
          Needs Attention
        </CardTitle>
        {loading && rows.length === 0 ? (
          <EmptyState>불러오는 중…</EmptyState>
        ) : attention.length === 0 ? (
          <EmptyState>지금 손댈 주문이 없습니다.</EmptyState>
        ) : (
          <ul className="divide-y" style={{ borderColor: OPS.border }}>
            {attention.map((a) => (
              <AttentionRow key={`${a.orderId}-${a.kind}`} item={a} />
            ))}
          </ul>
        )}
      </Card>

      <Card>
        <CardTitle
          action={
            <a
              href={OPS_ORDERS_PATH}
              className="text-[12px] underline"
              style={{ color: OPS.textMuted }}
            >
              모든 주문
            </a>
          }
        >
          Recent Orders
        </CardTitle>
        {recent.length === 0 ? (
          <EmptyState>결제된 주문이 없습니다.</EmptyState>
        ) : (
          <ul className="divide-y" style={{ borderColor: OPS.border }}>
            {recent.map((o) => (
              <li key={o.orderId} className="flex items-center gap-3 py-3">
                <div className="min-w-0 flex-1">
                  <p className="truncate text-[13px] font-medium" style={{ color: OPS.text }}>
                    {o.recipientName || o.petId}
                  </p>
                  <p className="truncate text-[12px]" style={{ color: OPS.textFaint }}>
                    {PRODUCT_LABEL[o.productType] ?? o.productType} · {formatKrw(o.amount)}
                  </p>
                </div>
                <Pill tone={statusTone("production", o.productionStatus)}>
                  {statusText(o.productionStatus)}
                </Pill>
                <a
                  href={`${OPS_ORDERS_PATH}?order=${encodeURIComponent(o.orderId)}`}
                  className="text-[12px] underline"
                  style={{ color: OPS.textMuted }}
                >
                  열기
                </a>
              </li>
            ))}
          </ul>
        )}
      </Card>
    </div>
  );
}

function Stat({ label, value, hint }: { label: string; value: number; hint: string }) {
  return (
    <Card>
      <p className="text-[12px] uppercase tracking-wider" style={{ color: OPS.textFaint }}>
        {label}
      </p>
      <p className="mt-1 text-[26px] font-semibold leading-none" style={{ color: OPS.text }}>
        {value}
      </p>
      <p className="mt-1.5 text-[12px]" style={{ color: OPS.textFaint }}>
        {hint}
      </p>
    </Card>
  );
}

function AttentionRow({ item }: { item: AttentionItem }) {
  return (
    <li className="flex items-center gap-3 py-3">
      <div className="min-w-0 flex-1">
        <p className="truncate text-[13px] font-medium" style={{ color: OPS.text }}>
          {item.order.recipientName || item.order.petId}
        </p>
        <p className="truncate text-[12px]" style={{ color: OPS.textMuted }}>
          {item.reason}
        </p>
      </div>
      <Pill tone="warn">확인 필요</Pill>
      <a
        href={`${OPS_ORDERS_PATH}?order=${encodeURIComponent(item.orderId)}`}
        className="text-[12px] underline"
        style={{ color: OPS.textMuted }}
      >
        열기
      </a>
    </li>
  );
}
