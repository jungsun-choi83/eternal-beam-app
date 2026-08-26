"use client";

/**
 * Partners — `/ops/partners`. 제휴처 등록과 QR 발급.
 *
 * 예전 화면과 **같은 API·같은 보안 경계**다. 바뀐 것은 배치뿐이다:
 *   * 목록 → 상세로 나눴다(한 화면에 모든 파트너의 모든 코드를 펼치면 스크롤만 남는다)
 *   * QR 은 Living / Memorial 로 묶어 보여 준다
 *
 * ⚠️ 코드 문자열과 partner_id 는 **서버가 만든다.** 이 화면은 요청만 보낸다 —
 *    브라우저가 고를 수 있으면 남의 병원에 귀속시켜 정산을 훔칠 수 있다.
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
import { saveBlob } from "@/lib/ops-production-api";
import {
  createPartner,
  fetchCodeQr,
  issueCode,
  listPartners,
  partnerPublicUrl,
  setCodeActive,
  updatePartner,
  type PartnerCodeRow,
  type PartnerRow,
  type PartnerTrack,
  type PartnerType,
} from "@/lib/ops-partners-api";

const TYPE_LABEL: Record<string, string> = {
  HOSPITAL: "동물병원",
  FUNERAL: "장례식장",
};
const TRACK_LABEL: Record<string, string> = {
  living: "Living · 곁에 있는 아이",
  memorial: "Memorial · 떠난 아이",
};

function percent(rate: number): string {
  const p = rate * 100;
  return `${Number.isInteger(p) ? p : Math.round(p * 100) / 100}%`;
}

export function OpsPartnersScreen() {
  return (
    <OpsLayout active="partners" title="Partners" subtitle="제휴처와 QR 코드">
      {(p) => <Body {...p} />}
    </OpsLayout>
  );
}

function Body({ token, onAuthError }: OpsChildProps) {
  const [partners, setPartners] = useState<PartnerRow[]>([]);
  const [openId, setOpenId] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [copied, setCopied] = useState<string | null>(null);
  const [qrPreviews, setQrPreviews] = useState<Record<string, string>>({});

  const [showForm, setShowForm] = useState(false);
  const [fName, setFName] = useState("");
  const [fType, setFType] = useState<PartnerType>("HOSPITAL");
  const [fRate, setFRate] = useState("0.15");
  const [fTrack, setFTrack] = useState<string>("memorial");
  const [formError, setFormError] = useState<string | null>(null);

  const fail = useCallback(
    (e: unknown) => {
      onAuthError(e);
      setError((e as { message?: string })?.message ?? "요청에 실패했습니다.");
    },
    [onAuthError]
  );

  useEffect(
    () => () => {
      for (const u of Object.values(qrPreviews)) URL.revokeObjectURL(u);
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    []
  );

  const refresh = useCallback(async () => {
    setBusy("load");
    try {
      setPartners(await listPartners(token));
      setError(null);
    } catch (e) {
      fail(e);
    } finally {
      setBusy(null);
    }
  }, [token, fail]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const submit = useCallback(async () => {
    const name = fName.trim();
    if (!name) {
      setFormError("제휴처 이름을 입력하세요.");
      return;
    }
    // 15 를 15% 로 알고 넣는 실수가 가장 흔하고 가장 비싸다. 서버와 DB 가 다시
    // 막지만, 여기서 막아야 운영자가 이유를 읽을 수 있다.
    const rate = Number(fRate);
    if (!Number.isFinite(rate) || rate < 0 || rate > 1) {
      setFormError("정산 비율은 0 과 1 사이의 소수입니다. 15% 는 0.15 입니다.");
      return;
    }
    setFormError(null);
    setBusy("create");
    try {
      await createPartner(token, {
        partnerName: name,
        partnerType: fType,
        shareRate: rate,
        active: true,
        initialTrack: (fTrack || null) as PartnerTrack | null,
      });
      setShowForm(false);
      setFName("");
      setFRate("0.15");
      await refresh();
    } catch (e) {
      fail(e);
    } finally {
      setBusy(null);
    }
  }, [token, fName, fType, fRate, fTrack, refresh, fail]);

  const act = useCallback(
    async (label: string, fn: () => Promise<unknown>) => {
      setBusy(label);
      try {
        await fn();
        await refresh();
        setError(null);
      } catch (e) {
        fail(e);
      } finally {
        setBusy(null);
      }
    },
    [refresh, fail]
  );

  const doCopy = useCallback(async (code: string) => {
    try {
      await navigator.clipboard.writeText(partnerPublicUrl(code));
      setCopied(code);
      window.setTimeout(() => setCopied((c) => (c === code ? null : c)), 1500);
    } catch {
      /* 클립보드가 막힌 환경 — 주소는 화면에 그대로 보인다 */
    }
  }, []);

  const doPreview = useCallback(
    async (code: string) => {
      setBusy(`qr:${code}`);
      try {
        const blob = await fetchCodeQr(token, code, "png");
        const url = URL.createObjectURL(blob);
        setQrPreviews((old) => {
          if (old[code]) URL.revokeObjectURL(old[code]!);
          return { ...old, [code]: url };
        });
      } catch (e) {
        fail(e);
      } finally {
        setBusy(null);
      }
    },
    [token, fail]
  );

  const doDownload = useCallback(
    async (code: string) => {
      setBusy(`dl:${code}`);
      try {
        // svg 로 받는다 — 인쇄용이라 벡터여야 어떤 크기로 뽑아도 선명하다.
        saveBlob(await fetchCodeQr(token, code, "svg"), `partner-${code}-qr.svg`);
      } catch (e) {
        fail(e);
      } finally {
        setBusy(null);
      }
    },
    [token, fail]
  );

  const open = partners.find((p) => p.partnerId === openId) ?? null;

  if (open) {
    const living = open.codes.filter((c) => c.track === "living");
    const memorial = open.codes.filter((c) => c.track === "memorial");
    const untracked = open.codes.filter((c) => !c.track);

    return (
      <div className="mx-auto max-w-4xl space-y-5">
        <button
          type="button"
          onClick={() => setOpenId(null)}
          className="text-[13px] underline"
          style={{ color: OPS.textMuted }}
        >
          ← 제휴처 목록
        </button>

        {error ? <ErrorNote>{error}</ErrorNote> : null}

        <Card>
          <div className="flex flex-wrap items-center gap-2">
            <h2
              className="mr-auto text-[15px] font-semibold"
              style={{ color: OPS.text, fontSize: "15px", lineHeight: 1.35 }}
            >
              {open.partnerName}
            </h2>
            <Pill tone={open.active ? "good" : "neutral"}>
              {open.active ? "Active" : "Disabled"}
            </Pill>
            <Button
              size="sm"
              onClick={() =>
                void act(`partner:${open.partnerId}`, () =>
                  updatePartner(token, open.partnerId, { active: !open.active })
                )
              }
              busy={busy === `partner:${open.partnerId}`}
            >
              {open.active ? "제휴처 끄기" : "제휴처 켜기"}
            </Button>
          </div>

          <div className="mt-3">
            <FieldGrid>
              <Field label="유형" value={TYPE_LABEL[open.partnerType] ?? open.partnerType} />
              <Field label="정산 비율" value={percent(open.shareRate)} />
            </FieldGrid>
          </div>

          {!open.active ? (
            <p className="mt-3 text-[12px]" style={{ color: "#8A4B16" }}>
              제휴처가 꺼져 있어 모든 코드가 새 귀속을 만들지 않습니다. 이미 귀속된
              편지·주문은 그대로입니다.
            </p>
          ) : null}

          <TechnicalDetails>
            <Field label="Partner ID" value={open.partnerId} mono />
          </TechnicalDetails>
        </Card>

        <Card>
          <CardTitle
            action={
              <div className="flex gap-2">
                <Button
                  size="sm"
                  onClick={() =>
                    void act(`issue:${open.partnerId}`, () =>
                      issueCode(token, { partnerId: open.partnerId, track: "memorial" })
                    )
                  }
                  busy={busy === `issue:${open.partnerId}`}
                >
                  + Memorial QR
                </Button>
                <Button
                  size="sm"
                  onClick={() =>
                    void act(`issue:${open.partnerId}`, () =>
                      issueCode(token, { partnerId: open.partnerId, track: "living" })
                    )
                  }
                  busy={busy === `issue:${open.partnerId}`}
                >
                  + Living QR
                </Button>
              </div>
            }
          >
            QR 코드
          </CardTitle>

          {open.codes.length === 0 ? (
            <EmptyState>발급된 QR 이 없습니다.</EmptyState>
          ) : (
            <div className="space-y-5">
              <CodeGroup
                title="Living"
                codes={living}
                busy={busy}
                copied={copied}
                previews={qrPreviews}
                onCopy={doCopy}
                onPreview={doPreview}
                onDownload={doDownload}
                onToggle={(c) =>
                  void act(`code:${c.code}`, () => setCodeActive(token, c.code, !c.active))
                }
              />
              <CodeGroup
                title="Memorial"
                codes={memorial}
                busy={busy}
                copied={copied}
                previews={qrPreviews}
                onCopy={doCopy}
                onPreview={doPreview}
                onDownload={doDownload}
                onToggle={(c) =>
                  void act(`code:${c.code}`, () => setCodeActive(token, c.code, !c.active))
                }
              />
              {untracked.length > 0 ? (
                <CodeGroup
                  title="갈래 없음 · 고객이 선택"
                  codes={untracked}
                  busy={busy}
                  copied={copied}
                  previews={qrPreviews}
                  onCopy={doCopy}
                  onPreview={doPreview}
                  onDownload={doDownload}
                  onToggle={(c) =>
                    void act(`code:${c.code}`, () => setCodeActive(token, c.code, !c.active))
                  }
                />
              ) : null}
            </div>
          )}
        </Card>
      </div>
    );
  }

  return (
    <div className="mx-auto max-w-4xl space-y-5">
      {error ? <ErrorNote>{error}</ErrorNote> : null}

      <div className="flex gap-2">
        <Button tone="primary" onClick={() => setShowForm((v) => !v)}>
          {showForm ? "닫기" : "+ 제휴처 등록"}
        </Button>
        <Button onClick={() => void refresh()} busy={busy === "load"}>
          새로고침
        </Button>
      </div>

      {showForm ? (
        <Card>
          <CardTitle>새 제휴처</CardTitle>
          <div className="grid gap-3 sm:grid-cols-2">
            <div>
              <p className="mb-1 text-[12px]" style={{ color: OPS.textFaint }}>
                제휴처 이름
              </p>
              <TextInput value={fName} onChange={setFName} placeholder="예: 신림동물병원" />
            </div>
            <div>
              <p className="mb-1 text-[12px]" style={{ color: OPS.textFaint }}>
                유형
              </p>
              <Select
                value={fType}
                onChange={(v) => setFType(v as PartnerType)}
                options={[
                  { value: "HOSPITAL", label: "동물병원" },
                  { value: "FUNERAL", label: "장례식장" },
                ]}
              />
            </div>
            <div>
              <p className="mb-1 text-[12px]" style={{ color: OPS.textFaint }}>
                정산 비율 (0.15 = 15%)
              </p>
              <TextInput value={fRate} onChange={setFRate} placeholder="0.15" />
            </div>
            <div>
              <p className="mb-1 text-[12px]" style={{ color: OPS.textFaint }}>
                첫 QR 갈래
              </p>
              <Select
                value={fTrack}
                onChange={setFTrack}
                options={[
                  { value: "memorial", label: "Memorial · 떠난 아이" },
                  { value: "living", label: "Living · 곁에 있는 아이" },
                  { value: "", label: "발급하지 않음" },
                ]}
              />
            </div>
          </div>
          {formError ? (
            <p className="mt-3 text-[12px]" style={{ color: "#8A4B16" }}>
              {formError}
            </p>
          ) : null}
          <div className="mt-4">
            <Button tone="primary" onClick={() => void submit()} busy={busy === "create"}>
              등록
            </Button>
          </div>
        </Card>
      ) : null}

      <Card padded={false}>
        {partners.length === 0 ? (
          <EmptyState>{busy === "load" ? "불러오는 중…" : "등록된 제휴처가 없습니다."}</EmptyState>
        ) : (
          <ul className="divide-y" style={{ borderColor: OPS.border }}>
            {partners.map((p) => (
              <li key={p.partnerId}>
                <button
                  type="button"
                  onClick={() => setOpenId(p.partnerId)}
                  className="flex w-full items-center gap-3 px-5 py-3.5 text-left"
                >
                  <div className="min-w-0 flex-1">
                    <p className="truncate text-[13px] font-medium" style={{ color: OPS.text }}>
                      {p.partnerName}
                    </p>
                    <p className="truncate text-[12px]" style={{ color: OPS.textFaint }}>
                      {TYPE_LABEL[p.partnerType] ?? p.partnerType} · 정산 {percent(p.shareRate)} ·
                      QR {p.codes.length}
                    </p>
                  </div>
                  <Pill tone={p.active ? "good" : "neutral"}>
                    {p.active ? "Active" : "Disabled"}
                  </Pill>
                </button>
              </li>
            ))}
          </ul>
        )}
      </Card>
    </div>
  );
}

function CodeGroup(p: {
  title: string;
  codes: PartnerCodeRow[];
  busy: string | null;
  copied: string | null;
  previews: Record<string, string>;
  onCopy: (code: string) => void;
  onPreview: (code: string) => void;
  onDownload: (code: string) => void;
  onToggle: (c: PartnerCodeRow) => void;
}) {
  if (p.codes.length === 0) return null;
  return (
    <div>
      <p className="mb-2 text-[12px] font-medium" style={{ color: OPS.textMuted }}>
        {TRACK_LABEL[p.title.toLowerCase()] ?? p.title}
      </p>
      <ul className="space-y-3">
        {p.codes.map((c) => (
          <li key={c.code} className="rounded-lg border p-3" style={{ borderColor: OPS.border }}>
            <div className="flex flex-wrap items-center gap-2">
              <Pill tone={c.active ? "good" : "neutral"}>{c.active ? "Active" : "Disabled"}</Pill>
              <span className="ml-auto" />
            </div>
            <p className="mt-2 break-all font-mono text-[11px]" style={{ color: OPS.textMuted }}>
              {partnerPublicUrl(c.code)}
            </p>
            {p.previews[c.code] ? (
              <img
                src={p.previews[c.code]}
                alt={`QR ${c.code}`}
                className="mt-3 h-28 w-28 rounded border bg-white p-1.5"
                style={{ borderColor: OPS.border }}
              />
            ) : null}
            <div className="mt-3 flex flex-wrap gap-2">
              <Button size="sm" onClick={() => p.onCopy(c.code)}>
                {p.copied === c.code ? "복사됨" : "링크 복사"}
              </Button>
              <Button size="sm" onClick={() => p.onPreview(c.code)} busy={p.busy === `qr:${c.code}`}>
                QR 미리보기
              </Button>
              <Button size="sm" onClick={() => p.onDownload(c.code)} busy={p.busy === `dl:${c.code}`}>
                내려받기 (SVG)
              </Button>
              <Button size="sm" onClick={() => p.onToggle(c)} busy={p.busy === `code:${c.code}`}>
                {c.active ? "끄기" : "켜기"}
              </Button>
            </div>
          </li>
        ))}
      </ul>
    </div>
  );
}
