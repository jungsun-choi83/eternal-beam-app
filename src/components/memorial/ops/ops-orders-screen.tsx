"use client";

/**
 * Orders — `/ops/production`. **스태프의 주 작업 화면.**
 *
 * 예전 생산 콘솔과 **같은 API·같은 상태 기계**를 쓴다. 바뀐 것은 배치와 표현뿐이다:
 *   * 검색은 별도 개념이 아니라 이 화면의 기능이다(`/ops/search` 를 노출하지 않는다)
 *   * 버튼 활성 판정은 그대로 lib/ops-production-flow.ts 의 순수 함수가 한다
 *   * 서버가 최종 판정을 다시 하므로 화면이 틀려도 잘못된 전이는 일어나지 않는다
 *
 * ⚠️ 브라우저 인쇄를 하지 않는다. 인쇄소에 넘길 **파일**을 만들어 준다.
 */

import { useCallback, useEffect, useMemo, useState } from "react";

import { OpsLayout, type OpsChildProps } from "./ops-layout";
import {
  Button,
  Card,
  CardTitle,
  EmptyState,
  ErrorNote,
  Field,
  FieldGrid,
  OPS,
  Pill,
  TechnicalDetails,
  TextInput,
  statusText,
  statusTone,
} from "./ops-ui";
import {
  FILE_LABEL,
  fileName,
  opsActions,
  productionStep,
  shipBlockedReason,
} from "@/lib/ops-production-flow";
import {
  addTracking,
  attachPhoto,
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
import { formatKrw } from "@/lib/order-checkout-flow";

const PRODUCT_LABEL: Record<string, string> = {
  LETTER: "편지",
  MEMORY_BOX: "메모리 박스",
};
const PARTNER_TYPE_LABEL: Record<string, string> = {
  HOSPITAL: "동물병원",
  FUNERAL: "장례식장",
};
const TRACK_LABEL: Record<string, string> = {
  living: "Living",
  memorial: "Memorial",
};

/** 상태 필터 — 대시보드의 네 칸과 **같은 규칙**이다(두 화면이 갈라지면 안 된다). */
type Filter = "all" | "paid" | "preparing" | "ready" | "shipping";

const FILTERS: { id: Filter; label: string }[] = [
  { id: "all", label: "전체" },
  { id: "paid", label: "Paid" },
  { id: "preparing", label: "Preparing" },
  { id: "ready", label: "Ready" },
  { id: "shipping", label: "Shipping" },
];

export function bucketOf(o: { productionStatus: string; shippingStatus: string }): Filter {
  const ship = (o.shippingStatus || "").toLowerCase();
  const prod = (o.productionStatus || "").toLowerCase();
  if (ship === "shipped" || ship === "delivered") return "shipping";
  if (prod === "produced") return "ready";
  if (prod === "ready" || prod === "in_production") return "preparing";
  return "paid";
}

export function OpsOrdersScreen() {
  return (
    <OpsLayout active="orders" title="Orders" subtitle="결제된 주문의 생산과 배송">
      {(p) => <Body {...p} />}
    </OpsLayout>
  );
}

function Body({ token, onAuthError }: OpsChildProps) {
  const [query, setQuery] = useState("");
  const [filter, setFilter] = useState<Filter>("all");
  const [orders, setOrders] = useState<OpsOrderRow[]>([]);
  const [state, setState] = useState<OpsProductionState | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [preview, setPreview] = useState<{ kind: string; url: string } | null>(null);

  const [qrUrlInput, setQrUrlInput] = useState("");
  const [photoInput, setPhotoInput] = useState("");
  const [trackingInput, setTrackingInput] = useState("");

  const fail = useCallback(
    (e: unknown) => {
      onAuthError(e);
      setError((e as { message?: string })?.message ?? "요청에 실패했습니다.");
    },
    [onAuthError]
  );

  const doSearch = useCallback(async () => {
    setBusy("search");
    try {
      setOrders(await searchPaidOrders({ query, accessToken: token }));
      setError(null);
    } catch (e) {
      fail(e);
    } finally {
      setBusy(null);
    }
  }, [token, query, fail]);

  const open = useCallback(
    async (orderId: string) => {
      setPreview(null);
      setTrackingInput("");
      setBusy("open");
      try {
        setState(await fetchProductionState(orderId, token));
        setError(null);
      } catch (e) {
        fail(e);
      } finally {
        setBusy(null);
      }
    },
    [token, fail]
  );

  // 첫 진입 로드. 대시보드가 `?order=` 로 특정 주문을 지정하면 바로 연다.
  useEffect(() => {
    void (async () => {
      await doSearch();
      const wanted = new URLSearchParams(window.location.search).get("order");
      if (wanted) await open(wanted);
    })();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [token]);

  useEffect(
    () => () => {
      if (preview) URL.revokeObjectURL(preview.url);
    },
    [preview]
  );

  const run = useCallback(
    async (label: string, fn: () => Promise<OpsProductionState>) => {
      setBusy(label);
      setError(null);
      try {
        setState(await fn());
      } catch (e) {
        fail(e);
      } finally {
        setBusy(null);
      }
    },
    [fail]
  );

  const withBlob = useCallback(
    async (label: string, fn: () => Promise<Blob>, after: (b: Blob) => void) => {
      setBusy(label);
      try {
        after(await fn());
        setError(null);
      } catch (e) {
        fail(e);
      } finally {
        setBusy(null);
      }
    },
    [fail]
  );

  const visible = useMemo(
    () => (filter === "all" ? orders : orders.filter((o) => bucketOf(o) === filter)),
    [orders, filter]
  );

  const actions = state
    ? opsActions(state)
    : {
        canPrepare: false, canPreview: false, canDownload: false, canStart: false,
        canMarkProduced: false, canAddTracking: false, canShip: false,
        canMarkDelivered: false, blockedReason: null,
      };

  const needsPhoto =
    state != null &&
    (state.files.includes("photo_card") ||
      state.pendingFiles.some((f) => f.kind === "photo_card"));

  if (state) {
    return (
      <div className="mx-auto max-w-4xl space-y-5">
        <button
          type="button"
          onClick={() => setState(null)}
          className="text-[13px] underline"
          style={{ color: OPS.textMuted }}
        >
          ← 주문 목록
        </button>

        {error ? <ErrorNote>{error}</ErrorNote> : null}

        <Detail
          state={state}
          actions={actions}
          busy={busy}
          needsPhoto={needsPhoto}
          preview={preview}
          qrUrlInput={qrUrlInput}
          photoInput={photoInput}
          trackingInput={trackingInput}
          setQrUrlInput={setQrUrlInput}
          setPhotoInput={setPhotoInput}
          setTrackingInput={setTrackingInput}
          onPrepare={() =>
            void run("prepare", () =>
              prepareProduction(state.orderId, token, {
                qrShareUrl: qrUrlInput.trim() || null,
                photoImageUrl: photoInput.trim() || null,
              })
            )
          }
          onAttachPhoto={() =>
            void run("photo", () => attachPhoto(state.orderId, token, photoInput.trim()))
          }
          onStart={() => void run("start", () => startProduction(state.orderId, token))}
          onProduced={() => void run("produced", () => markProduced(state.orderId, token))}
          onShip={() => void run("ship", () => markShipped(state.orderId, token))}
          onDelivered={() => void run("delivered", () => markDelivered(state.orderId, token))}
          onTracking={() =>
            void run("tracking", () =>
              addTracking(state.orderId, token, trackingInput.trim())
            )
          }
          onPreview={(kind) =>
            void withBlob(
              `preview:${kind}`,
              () => fetchProductionFile({ orderId: state.orderId, kind, accessToken: token }),
              (b) =>
                setPreview((old) => {
                  if (old) URL.revokeObjectURL(old.url);
                  return { kind, url: URL.createObjectURL(b) };
                })
            )
          }
          onDownload={(kind) =>
            void withBlob(
              `dl:${kind}`,
              () => fetchProductionFile({ orderId: state.orderId, kind, accessToken: token }),
              (b) => saveBlob(b, fileName(state.orderId, kind))
            )
          }
          onZip={() =>
            void withBlob(
              "zip",
              () => fetchProductionZip({ orderId: state.orderId, accessToken: token }),
              (b) => saveBlob(b, `${state.orderId}-production.zip`)
            )
          }
          onShareQr={(kind) =>
            void withBlob(
              `qr:${kind}`,
              () =>
                fetchShareQrAgain({
                  shareId: state.shakerShareId as string,
                  kind,
                  accessToken: token,
                }),
              (b) => saveBlob(b, `${state.petId}-qr.${kind}`)
            )
          }
        />
      </div>
    );
  }

  return (
    <div className="mx-auto max-w-5xl space-y-5">
      {error ? <ErrorNote>{error}</ErrorNote> : null}

      <Card>
        <div className="flex flex-col gap-3 sm:flex-row">
          <div className="flex-1">
            <TextInput
              value={query}
              onChange={setQuery}
              onEnter={() => void doSearch()}
              placeholder="고객 · 주문번호 · 아이 · 수령인 · 송장"
            />
          </div>
          <Button tone="primary" onClick={() => void doSearch()} busy={busy === "search"}>
            검색
          </Button>
        </div>

        <div className="mt-3 flex flex-wrap gap-1.5">
          {FILTERS.map((f) => {
            const on = filter === f.id;
            return (
              <button
                key={f.id}
                type="button"
                onClick={() => setFilter(f.id)}
                className="rounded-full px-3 py-1.5 text-[12px] font-medium"
                style={{
                  background: on ? OPS.goldSoft : "#fff",
                  color: on ? OPS.gold : OPS.textMuted,
                  border: `1px solid ${on ? OPS.goldSoft : OPS.border}`,
                }}
              >
                {f.label}
              </button>
            );
          })}
        </div>
      </Card>

      <Card padded={false}>
        {visible.length === 0 ? (
          <EmptyState>{busy === "search" ? "불러오는 중…" : "주문이 없습니다."}</EmptyState>
        ) : (
          <ul className="divide-y" style={{ borderColor: OPS.border }}>
            {visible.map((o) => (
              <li key={o.orderId}>
                <button
                  type="button"
                  onClick={() => void open(o.orderId)}
                  className="flex w-full items-center gap-3 px-5 py-3.5 text-left"
                >
                  <div className="min-w-0 flex-1">
                    <p className="truncate text-[13px] font-medium" style={{ color: OPS.text }}>
                      {o.recipientName || o.petId}
                    </p>
                    <p className="truncate text-[12px]" style={{ color: OPS.textFaint }}>
                      {PRODUCT_LABEL[o.productType] ?? o.productType} · {formatKrw(o.amount)}
                      {o.partnerName ? ` · ${o.partnerName}` : ""}
                      {o.trackingNumber ? ` · 송장 ${o.trackingNumber}` : ""}
                    </p>
                  </div>
                  <div className="hidden shrink-0 gap-1.5 sm:flex">
                    {o.needsAttention ? <Pill tone="warn">확인 필요</Pill> : null}
                    <Pill tone={statusTone("payment", o.paymentStatus)}>
                      {statusText(o.paymentStatus)}
                    </Pill>
                    <Pill tone={statusTone("production", o.productionStatus)}>
                      {statusText(o.productionStatus)}
                    </Pill>
                    <Pill tone={statusTone("shipping", o.shippingStatus)}>
                      {statusText(o.shippingStatus)}
                    </Pill>
                  </div>
                </button>
              </li>
            ))}
          </ul>
        )}
      </Card>
    </div>
  );
}

const STEPS = ["대기", "준비됨", "제작 중", "제작 완료"];

function Detail(p: {
  state: OpsProductionState;
  actions: ReturnType<typeof opsActions>;
  busy: string | null;
  needsPhoto: boolean;
  preview: { kind: string; url: string } | null;
  qrUrlInput: string;
  photoInput: string;
  trackingInput: string;
  setQrUrlInput: (v: string) => void;
  setPhotoInput: (v: string) => void;
  setTrackingInput: (v: string) => void;
  onPrepare: () => void;
  onAttachPhoto: () => void;
  onStart: () => void;
  onProduced: () => void;
  onShip: () => void;
  onDelivered: () => void;
  onTracking: () => void;
  onPreview: (kind: string) => void;
  onDownload: (kind: string) => void;
  onZip: () => void;
  onShareQr: (kind: "svg" | "png") => void;
}) {
  const s = p.state;
  const busy = p.busy;
  const step = productionStep(s.productionStatus);

  return (
    <>
      <Card>
        <div className="flex flex-wrap items-center gap-2">
          <h2
            className="mr-auto text-[15px] font-semibold"
            style={{ color: OPS.text, fontSize: "15px", lineHeight: 1.35 }}
          >
            {s.recipientName || s.petId}
          </h2>
          <Pill tone={statusTone("payment", s.paymentStatus)}>{statusText(s.paymentStatus)}</Pill>
          <Pill tone={statusTone("production", s.productionStatus)}>
            {statusText(s.productionStatus)}
          </Pill>
          <Pill tone={statusTone("shipping", s.shippingStatus)}>
            {statusText(s.shippingStatus)}
          </Pill>
        </div>

        <div className="mt-4 flex gap-1.5">
          {STEPS.map((label, i) => (
            <div key={label} className="flex-1">
              <div
                className="h-1 rounded-full"
                style={{ background: i <= step ? OPS.gold : OPS.border }}
              />
              <p className="mt-1.5 text-[11px]" style={{ color: i <= step ? OPS.text : OPS.textFaint }}>
                {label}
              </p>
            </div>
          ))}
        </div>

        {p.actions.blockedReason ? (
          <p className="mt-3 text-[12px]" style={{ color: "#8A4B16" }}>
            {p.actions.blockedReason}
          </p>
        ) : null}
      </Card>

      <Card>
        <CardTitle>주문</CardTitle>
        <FieldGrid>
          <Field label="아이" value={s.letterChildName || s.petId} />
          <Field label="제품" value={PRODUCT_LABEL[s.productType] ?? s.productType} />
          <Field label="금액" value={formatKrw(s.amount)} />
          <Field label="수령인" value={s.recipientName ?? undefined} />
          <Field label="주소" value={s.addressLine1 ?? undefined} />
          <Field label="BREATHING" >
            <Pill tone={s.breathingReady ? "good" : "warn"}>
              {s.breathingReady ? "준비됨" : "없음"}
            </Pill>
          </Field>
        </FieldGrid>
        <TechnicalDetails>
          <FieldGrid>
            <Field label="주문번호" value={s.orderId} mono />
            <Field label="고객" value={s.userId} mono />
            <Field label="Pet ID" value={s.petId} mono />
            <Field label="Shaker share" value={s.shakerShareId ?? undefined} mono />
            <Field
              label="인쇄 QR"
              value={s.qrShareUrl ?? (s.qrArtifactStored ? "보관된 산출물 사용" : undefined)}
              mono
            />
          </FieldGrid>
        </TechnicalDetails>
      </Card>

      <Card>
        <CardTitle>Soul Trace</CardTitle>
        <FieldGrid cols={1}>
          <Field label="아이 이름" value={s.letterChildName ?? undefined} />
          <Field label="편지 발췌" value={s.letterExcerpt ?? undefined} />
        </FieldGrid>
        <TechnicalDetails>
          <Field label="편지 ID" value={s.soulTraceLetterId ?? undefined} mono />
        </TechnicalDetails>
      </Card>

      {s.partnerId ? (
        <Card>
          <CardTitle>파트너 귀속</CardTitle>
          <FieldGrid>
            <Field label="유형" value={PARTNER_TYPE_LABEL[s.partnerType ?? ""] ?? s.partnerType ?? undefined} />
            <Field label="이름" value={s.partnerName ?? undefined} />
            <Field
              label="갈래"
              value={s.partnerTrack ? TRACK_LABEL[s.partnerTrack] ?? s.partnerTrack : undefined}
            />
            <Field
              label="정산 비율 (주문 시점)"
              value={
                s.partnerShareRate != null
                  ? `${Math.round(s.partnerShareRate * 10000) / 100}%`
                  : undefined
              }
            />
          </FieldGrid>
          <p className="mt-1 text-[12px]" style={{ color: OPS.textFaint }}>
            정산 비율은 **주문 시점 스냅샷**입니다. 파트너의 현재 비율과 다를 수 있으며,
            계약이 바뀌어도 이미 결제된 주문은 움직이지 않습니다.
          </p>
          <TechnicalDetails>
            <FieldGrid>
              <Field label="파트너 ID" value={s.partnerId} mono />
              <Field label="코드" value={s.partnerCode ?? undefined} mono />
            </FieldGrid>
          </TechnicalDetails>
        </Card>
      ) : null}

      <Card>
        <CardTitle
          action={
            <Button size="sm" onClick={p.onPrepare} busy={busy === "prepare"} disabled={!p.actions.canPrepare}>
              {s.packageReady ? "다시 준비" : "생산 준비"}
            </Button>
          }
        >
          제작 파일
        </CardTitle>

        {!s.packageReady ? (
          <div className="space-y-2">
            <TextInput
              value={p.qrUrlInput}
              onChange={p.setQrUrlInput}
              placeholder="Shaker 공유 URL (보관된 QR 이 있으면 비워 두세요)"
            />
            <TextInput
              value={p.photoInput}
              onChange={p.setPhotoInput}
              placeholder="사진 카드 이미지 URL (메모리 박스)"
            />
            <p className="text-[12px]" style={{ color: OPS.textFaint }}>
              멱등입니다 — 다시 눌러도 같은 패키지가 나오고 QR·편지는 다시 만들어지지 않습니다.
            </p>
          </div>
        ) : (
          <>
            <ul className="divide-y" style={{ borderColor: OPS.border }}>
              {s.files.map((k) => (
                <li key={k} className="flex flex-wrap items-center gap-2 py-2.5">
                  <span className="mr-auto text-[13px]" style={{ color: OPS.text }}>
                    {FILE_LABEL[k] ?? k}
                  </span>
                  <Pill tone="good">준비됨</Pill>
                  <Button size="sm" onClick={() => p.onPreview(k)} busy={busy === `preview:${k}`}>
                    미리보기
                  </Button>
                  <Button size="sm" onClick={() => p.onDownload(k)} busy={busy === `dl:${k}`}>
                    내려받기
                  </Button>
                </li>
              ))}

              {s.pendingFiles.map((f) => (
                <li key={f.kind} className="flex flex-wrap items-center gap-2 py-2.5">
                  <div className="mr-auto min-w-0">
                    <p className="text-[13px]" style={{ color: OPS.text }}>
                      {FILE_LABEL[f.kind] ?? f.kind}
                    </p>
                    <p className="text-[12px]" style={{ color: OPS.textMuted }}>
                      {f.reason}
                    </p>
                  </div>
                  <Pill tone="warn">{f.status}</Pill>
                  <Button size="sm" onClick={() => p.onPreview(f.kind)} busy={busy === `preview:${f.kind}`}>
                    교정지
                  </Button>
                </li>
              ))}
            </ul>

            <div className="mt-3 flex flex-wrap gap-2">
              <Button tone="primary" onClick={p.onZip} busy={busy === "zip"} disabled={!p.actions.canDownload}>
                생산 ZIP
              </Button>
              {s.shakerShareId ? (
                <>
                  <Button size="sm" onClick={() => p.onShareQr("svg")} busy={busy === "qr:svg"}>
                    QR SVG
                  </Button>
                  <Button size="sm" onClick={() => p.onShareQr("png")} busy={busy === "qr:png"}>
                    QR PNG
                  </Button>
                </>
              ) : null}
            </div>

            {p.needsPhoto ? (
              <div className="mt-4 rounded-lg border p-3" style={{ borderColor: OPS.border }}>
                <p className="text-[12px]" style={{ color: OPS.textMuted }}>
                  사진 카드 원본{" "}
                  {s.photoReady ? (
                    <span style={{ color: "#2F6B44" }}>확정됨</span>
                  ) : (
                    <span style={{ color: "#8A4B16" }}>없음 — 사진 카드와 ZIP 을 만들 수 없습니다</span>
                  )}
                </p>
                <div className="mt-2 flex flex-col gap-2 sm:flex-row">
                  <div className="flex-1">
                    <TextInput
                      value={p.photoInput}
                      onChange={p.setPhotoInput}
                      placeholder="사진 카드 이미지 URL"
                    />
                  </div>
                  <Button
                    onClick={p.onAttachPhoto}
                    busy={busy === "photo"}
                    disabled={!p.photoInput.trim()}
                  >
                    {s.photoReady ? "교체" : "지정"}
                  </Button>
                </div>
              </div>
            ) : null}

            {p.preview ? (
              <div className="mt-4">
                <p className="mb-2 text-[12px]" style={{ color: OPS.textFaint }}>
                  {FILE_LABEL[p.preview.kind] ?? p.preview.kind}
                </p>
                {p.preview.kind === "letter_pdf" ? (
                  <iframe
                    title="letter"
                    src={p.preview.url}
                    className="h-[520px] w-full rounded-lg border"
                    style={{ borderColor: OPS.border }}
                  />
                ) : (
                  <img
                    src={p.preview.url}
                    alt=""
                    className="max-h-[320px] rounded-lg border"
                    style={{ borderColor: OPS.border }}
                  />
                )}
              </div>
            ) : null}
          </>
        )}
      </Card>

      <Card>
        <CardTitle>생산 · 배송</CardTitle>
        <div className="flex flex-wrap gap-2">
          <Button onClick={p.onStart} busy={busy === "start"} disabled={!p.actions.canStart}>
            제작 시작
          </Button>
          <Button onClick={p.onProduced} busy={busy === "produced"} disabled={!p.actions.canMarkProduced}>
            제작 완료
          </Button>
          <Button onClick={p.onShip} busy={busy === "ship"} disabled={!p.actions.canShip}>
            발송 처리
          </Button>
          <Button onClick={p.onDelivered} busy={busy === "delivered"} disabled={!p.actions.canMarkDelivered}>
            배송 완료
          </Button>
        </div>

        <div className="mt-3 flex flex-col gap-2 sm:flex-row">
          <div className="flex-1">
            <TextInput
              value={p.trackingInput}
              onChange={p.setTrackingInput}
              placeholder={s.trackingNumber ?? "송장 번호"}
            />
          </div>
          <Button
            onClick={p.onTracking}
            busy={busy === "tracking"}
            disabled={!p.actions.canAddTracking || !p.trackingInput.trim()}
          >
            송장 등록
          </Button>
        </div>

        {s.trackingNumber ? (
          <p className="mt-2 text-[12px]" style={{ color: OPS.textMuted }}>
            현재 송장 · {s.trackingNumber}
          </p>
        ) : null}
        {shipBlockedReason(s) ? (
          <p className="mt-2 text-[12px]" style={{ color: OPS.textFaint }}>
            {shipBlockedReason(s)}
          </p>
        ) : null}
      </Card>
    </>
  );
}
