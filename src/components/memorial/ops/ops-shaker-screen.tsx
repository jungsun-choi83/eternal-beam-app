"use client";

/**
 * Shaker — `/ops/shaker`. 물리 제품에 인쇄할 QR 을 만드는 자리.
 *
 * 예전 콘솔과 **같은 API·같은 규칙**이다. 바뀐 것은 표현뿐이다:
 *   * 프로바이더·스토리지 내부값은 기술 정보로 접어 둔다
 *   * 펫 검색 → 선택 → 발급/폐기라는 흐름은 그대로다
 *
 * ── 펫을 만들지 않는다 ─────────────────────────────────────────────────────
 * 검색은 이미 만들어진 펫만 보여 주고, 발급은 그 petId 를 그대로 쓴다.
 * 소유자와 BREATHING 위치는 **서버가** 찾는다 — 운영자가 타이핑하지 않는다.
 */

import { useCallback, useEffect, useState } from "react";

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
  Select,
  TechnicalDetails,
  TextInput,
} from "./ops-ui";
import { copyToClipboard } from "@/lib/shaker-share-panel";
import { saveBlob } from "@/lib/ops-production-api";
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
const PURPOSES = [
  { value: "LETTER", label: "편지" },
  { value: "MEMORY_BOX", label: "메모리 박스" },
  { value: "OPS", label: "운영 확인용" },
];

export function OpsShakerScreen() {
  return (
    <OpsLayout active="shaker" title="Shaker" subtitle="펫 QR 공유 발급과 관리">
      {(p) => <Body {...p} />}
    </OpsLayout>
  );
}

function Body({ token, onAuthError }: OpsChildProps) {
  const [query, setQuery] = useState("");
  const [pets, setPets] = useState<OpsPet[]>([]);
  const [degraded, setDegraded] = useState(false);
  const [selected, setSelected] = useState<OpsPet | null>(null);
  const [shares, setShares] = useState<OpsShareSummary[]>([]);

  const [purpose, setPurpose] = useState("LETTER");
  const [orderRef, setOrderRef] = useState("");
  const [created, setCreated] = useState<OpsCreatedShare | null>(null);
  const [qrUrl, setQrUrl] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const fail = useCallback(
    (e: unknown) => {
      onAuthError(e);
      setError((e as { message?: string })?.message ?? "요청에 실패했습니다.");
    },
    [onAuthError]
  );

  useEffect(
    () => () => {
      if (qrUrl) URL.revokeObjectURL(qrUrl);
    },
    [qrUrl]
  );

  const doSearch = useCallback(async () => {
    setBusy("search");
    try {
      const r = await searchOpsPets({ query });
      setPets(r.pets);
      setDegraded(r.degraded || !r.registryAvailable);
      setError(null);
    } catch (e) {
      fail(e);
    } finally {
      setBusy(null);
    }
  }, [query, fail]);

  useEffect(() => {
    void doSearch();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [token]);

  const selectPet = useCallback(
    async (pet: OpsPet) => {
      setSelected(pet);
      setCreated(null);
      setQrUrl(null);
      setBusy("shares");
      try {
        setShares(await listOpsShares({ petId: pet.petId }));
        setError(null);
      } catch (e) {
        fail(e);
      } finally {
        setBusy(null);
      }
    },
    [fail]
  );

  const loadQr = useCallback(
    async (shareUrl: string, petId: string) => {
      try {
        const blob = await fetchOpsQr({ shareUrl, kind: "png", filename: petId });
        setQrUrl((old) => {
          if (old) URL.revokeObjectURL(old);
          return URL.createObjectURL(blob);
        });
      } catch (e) {
        fail(e);
      }
    },
    [fail]
  );

  const doCreate = useCallback(async () => {
    if (!selected) return;
    setBusy("create");
    try {
      const share = await createOpsShare({
        petId: selected.petId,
        purpose,
        orderRef: orderRef.trim() || undefined,
      });
      setCreated(share);
      await loadQr(share.shareUrl, share.petId);
      setShares(await listOpsShares({ petId: selected.petId }));
      setError(null);
    } catch (e) {
      fail(e);
    } finally {
      setBusy(null);
    }
  }, [selected, purpose, orderRef, loadQr, fail]);

  const doRevoke = useCallback(
    async (shareId: string) => {
      if (!selected) return;
      setBusy(`revoke:${shareId}`);
      try {
        await revokeOpsShare({ shareId, petId: selected.petId });
        setShares(await listOpsShares({ petId: selected.petId }));
        setError(null);
      } catch (e) {
        fail(e);
      } finally {
        setBusy(null);
      }
    },
    [selected, fail]
  );

  const downloadQr = useCallback(
    async (kind: "svg" | "png") => {
      if (!created) return;
      setBusy(`qr:${kind}`);
      try {
        const blob = await fetchOpsQr({
          shareUrl: created.shareUrl,
          kind,
          filename: created.petId,
        });
        saveBlob(blob, `${created.petId}-qr.${kind}`);
      } catch (e) {
        fail(e);
      } finally {
        setBusy(null);
      }
    },
    [created, fail]
  );

  return (
    <div className="mx-auto max-w-4xl space-y-5">
      {error ? <ErrorNote>{error}</ErrorNote> : null}

      <Card>
        <div className="flex flex-col gap-3 sm:flex-row">
          <div className="flex-1">
            <TextInput
              value={query}
              onChange={setQuery}
              onEnter={() => void doSearch()}
              placeholder="아이 · 고객"
            />
          </div>
          <Button tone="primary" onClick={() => void doSearch()} busy={busy === "search"}>
            검색
          </Button>
        </div>
        {degraded ? (
          <p className="mt-2 text-[12px]" style={{ color: "#8A4B16" }}>
            펫 레지스트리 응답이 불완전합니다. 목록이 일부만 보일 수 있습니다.
          </p>
        ) : null}
      </Card>

      {!selected ? (
        <Card padded={false}>
          {pets.length === 0 ? (
            <EmptyState>{busy === "search" ? "불러오는 중…" : "펫이 없습니다."}</EmptyState>
          ) : (
            <ul className="divide-y" style={{ borderColor: OPS.border }}>
              {pets.map((p) => (
                <li key={p.petId}>
                  <button
                    type="button"
                    onClick={() => void selectPet(p)}
                    className="flex w-full items-center gap-3 px-5 py-3.5 text-left"
                  >
                    <div className="min-w-0 flex-1">
                      <p className="truncate text-[13px] font-medium" style={{ color: OPS.text }}>
                        {p.petId}
                      </p>
                      <p className="truncate text-[12px]" style={{ color: OPS.textFaint }}>
                        {p.ownerUserId}
                      </p>
                    </div>
                    <Pill tone={p.readyCount > 0 ? "good" : "warn"}>
                      {p.readyCount > 0 ? `경험 ${p.readyCount}` : "BREATHING 없음"}
                    </Pill>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </Card>
      ) : (
        <>
          <button
            type="button"
            onClick={() => setSelected(null)}
            className="text-[13px] underline"
            style={{ color: OPS.textMuted }}
          >
            ← 펫 목록
          </button>

          <Card>
            <CardTitle>{selected.petId}</CardTitle>
            <FieldGrid>
              <Field label="고객" value={selected.ownerUserId} />
              <Field label="BREATHING">
                <Pill tone={selected.readyCount > 0 ? "good" : "warn"}>
                  {selected.readyCount > 0 ? "준비됨" : "없음"}
                </Pill>
              </Field>
            </FieldGrid>
            <TechnicalDetails>
              <FieldGrid>
                <Field label="Pet ID" value={selected.petId} mono />
                <Field label="출처" value={selected.source} mono />
                <Field label="canonical 모션" value={String(selected.readyCount)} mono />
              </FieldGrid>
            </TechnicalDetails>
          </Card>

          <Card>
            <CardTitle>새 PRINT 공유 발급</CardTitle>
            <div className="grid gap-2 sm:grid-cols-2">
              <Select value={purpose} onChange={setPurpose} options={PURPOSES} />
              <TextInput value={orderRef} onChange={setOrderRef} placeholder="주문 참조 (선택)" />
            </div>
            <div className="mt-3">
              <Button tone="primary" onClick={() => void doCreate()} busy={busy === "create"}>
                공유 발급
              </Button>
            </div>

            {created ? (
              <div className="mt-4 rounded-lg border p-3" style={{ borderColor: OPS.border }}>
                <p className="text-[12px]" style={{ color: OPS.textMuted }}>
                  이 주소는 **지금만** 볼 수 있습니다. 서버는 해시만 저장합니다.
                </p>
                <p className="mt-2 break-all font-mono text-[11px]" style={{ color: OPS.text }}>
                  {created.shareUrl}
                </p>
                {qrUrl ? (
                  <img
                    src={qrUrl}
                    alt="QR"
                    className="mt-3 h-32 w-32 rounded border bg-white p-1.5"
                    style={{ borderColor: OPS.border }}
                  />
                ) : null}
                <div className="mt-3 flex flex-wrap gap-2">
                  <Button
                    size="sm"
                    onClick={() => void copyToClipboard(created.shareUrl).then(setCopied)}
                  >
                    {copied ? "복사됨" : "링크 복사"}
                  </Button>
                  <Button size="sm" onClick={() => void downloadQr("svg")} busy={busy === "qr:svg"}>
                    QR SVG
                  </Button>
                  <Button size="sm" onClick={() => void downloadQr("png")} busy={busy === "qr:png"}>
                    QR PNG
                  </Button>
                </div>
              </div>
            ) : null}
          </Card>

          <Card padded={false}>
            <div className="px-5 pt-5">
              <CardTitle>발급된 공유</CardTitle>
            </div>
            {shares.length === 0 ? (
              <EmptyState>발급된 공유가 없습니다.</EmptyState>
            ) : (
              <ul className="divide-y" style={{ borderColor: OPS.border }}>
                {shares.map((s) => (
                  <li key={s.shareId} className="flex flex-wrap items-center gap-2 px-5 py-3">
                    <div className="mr-auto min-w-0">
                      <p className="truncate text-[13px]" style={{ color: OPS.text }}>
                        {s.purpose ?? "—"}
                        {s.orderRef ? ` · ${s.orderRef}` : ""}
                      </p>
                      <p className="truncate text-[12px]" style={{ color: OPS.textFaint }}>
                        {s.createdAt ?? ""}
                      </p>
                    </div>
                    <Pill tone={s.active ? "good" : "neutral"}>
                      {s.active ? "활성" : "폐기됨"}
                    </Pill>
                    {s.active ? (
                      <Button
                        size="sm"
                        tone="danger"
                        onClick={() => void doRevoke(s.shareId)}
                        busy={busy === `revoke:${s.shareId}`}
                      >
                        폐기
                      </Button>
                    ) : null}
                  </li>
                ))}
              </ul>
            )}
          </Card>
        </>
      )}
    </div>
  );
}
