"use client";

/**
 * 제휴처 콘솔 (Phase 16) — `/ops/partners`.
 *
 *   제휴처 등록 → QR 발급(갈래별) → 링크 복사 · QR 내려받기 → 켜기/끄기
 *
 * 이 화면이 있기 전에는 위 작업이 전부 터미널과 Supabase 직접 INSERT 였다.
 * 그 방식의 문제는 손이 많이 간다는 것이 아니라, **사람이 코드를 손으로 만들게
 * 된다는 것**이다. 손으로 만든 코드는 읽히고, 읽히는 코드는 추측되고, 추측되는
 * 코드는 정산을 훔칠 수 있다. 여기서는 코드를 서버가 만든다.
 *
 * ── 이 화면이 하지 않는 것 ─────────────────────────────────────────────────
 * 정산을 실행하지 않는다. 송금·인보이스가 없다. 통계 대시보드가 아니다.
 * 나중에 정산을 계산할 수 있을 만큼의 사실만 만들고 보여 준다.
 *
 * ⚠️ 인가는 서버가 한다(JWT + SHAKER_OPS_USER_IDS). 경로를 아는 것만으로는
 *    아무것도 못 한다 — 여기서 하는 일은 어느 화면을 그릴지 정하는 것뿐이다.
 */

import { useCallback, useEffect, useMemo, useState } from "react";

import { getPremiumAccessToken } from "@/lib/premium-auth-token";
import { AuthScreen } from "./auth-screen";
import { deriveOpsPhase } from "@/lib/shaker-ops-entry";
import { saveBlob } from "@/lib/ops-production-api";
import {
  createPartner,
  fetchCodeQr,
  issueCode,
  listPartners,
  partnerPublicUrl,
  setCodeActive,
  updatePartner,
  type PartnerRow,
  type PartnerTrack,
  type PartnerType,
} from "@/lib/ops-partners-api";

const PARTNER_TYPE_LABEL: Record<string, string> = {
  HOSPITAL: "동물병원",
  FUNERAL: "장례식장",
};

const TRACK_LABEL: Record<string, string> = {
  living: "LIVING · 곁에 있는 아이",
  memorial: "MEMORIAL · 떠난 아이",
};

/** 0.15 → "15%". 표시 전용이다 — 저장값은 언제나 0..1 의 소수다. */
function percentLabel(rate: number): string {
  const pct = rate * 100;
  return `${Number.isInteger(pct) ? pct : pct.toFixed(2)}%`;
}

export function OpsPartnersScreen() {
  const [token, setToken] = useState<string | null>(null);
  const [tokenLoaded, setTokenLoaded] = useState(false);
  const [errorCode, setErrorCode] = useState<string | null>(null);
  const [errorText, setErrorText] = useState<string | null>(null);

  const [partners, setPartners] = useState<PartnerRow[]>([]);
  const [busy, setBusy] = useState<string | null>(null);
  const [copied, setCopied] = useState<string | null>(null);
  /** code → objectURL. 미리보기용이며 언마운트에서 revoke 한다. */
  const [qrPreviews, setQrPreviews] = useState<Record<string, string>>({});

  const [showForm, setShowForm] = useState(false);
  const [formName, setFormName] = useState("");
  const [formType, setFormType] = useState<PartnerType>("HOSPITAL");
  const [formRate, setFormRate] = useState("0.15");
  const [formTrack, setFormTrack] = useState<PartnerTrack | "">("memorial");
  const [formError, setFormError] = useState<string | null>(null);

  useEffect(() => {
    void getPremiumAccessToken().then((r) => {
      setToken(r.token);
      setTokenLoaded(true);
    });
  }, []);

  // objectURL 은 명시적으로 놓아 주지 않으면 탭이 살아 있는 동안 메모리에 남는다.
  useEffect(() => {
    return () => {
      for (const url of Object.values(qrPreviews)) URL.revokeObjectURL(url);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const phase = useMemo(
    () => deriveOpsPhase({ hasAuth: Boolean(token), errorCode }),
    [token, errorCode],
  );

  const fail = useCallback((e: unknown) => {
    const err = e as { code?: string; message?: string };
    setErrorCode(err?.code ?? "UNKNOWN");
    setErrorText(err?.message ?? "요청에 실패했습니다.");
  }, []);

  const refresh = useCallback(async () => {
    if (!token) return;
    setBusy("load");
    try {
      setPartners(await listPartners(token));
      setErrorCode(null);
      setErrorText(null);
    } catch (e) {
      fail(e);
    } finally {
      setBusy(null);
    }
  }, [token, fail]);

  useEffect(() => {
    if (token) void refresh();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [token]);

  const submitPartner = useCallback(async () => {
    if (!token) return;
    const name = formName.trim();
    if (!name) {
      setFormError("제휴처 이름을 입력하세요.");
      return;
    }
    // 15 를 15% 로 알고 넣는 실수가 가장 흔하고 가장 비싸다. 서버와 DB 가 다시
    // 막지만, 여기서 막아야 운영자가 이유를 읽을 수 있다.
    const rate = Number(formRate);
    if (!Number.isFinite(rate) || rate < 0 || rate > 1) {
      setFormError("정산 비율은 0 과 1 사이의 소수입니다. 15% 는 0.15 입니다.");
      return;
    }
    setFormError(null);
    setBusy("create");
    try {
      await createPartner(token, {
        partnerName: name,
        partnerType: formType,
        shareRate: rate,
        active: true,
        initialTrack: formTrack || null,
      });
      setShowForm(false);
      setFormName("");
      setFormRate("0.15");
      await refresh();
    } catch (e) {
      fail(e);
    } finally {
      setBusy(null);
    }
  }, [token, formName, formType, formRate, formTrack, refresh, fail]);

  const doIssue = useCallback(
    async (partnerId: string, track: PartnerTrack | null) => {
      if (!token) return;
      setBusy(`issue:${partnerId}`);
      try {
        await issueCode(token, { partnerId, track });
        await refresh();
      } catch (e) {
        fail(e);
      } finally {
        setBusy(null);
      }
    },
    [token, refresh, fail],
  );

  const doToggleCode = useCallback(
    async (code: string, next: boolean) => {
      if (!token) return;
      setBusy(`code:${code}`);
      try {
        await setCodeActive(token, code, next);
        await refresh();
      } catch (e) {
        fail(e);
      } finally {
        setBusy(null);
      }
    },
    [token, refresh, fail],
  );

  const doTogglePartner = useCallback(
    async (partnerId: string, next: boolean) => {
      if (!token) return;
      setBusy(`partner:${partnerId}`);
      try {
        await updatePartner(token, partnerId, { active: next });
        await refresh();
      } catch (e) {
        fail(e);
      } finally {
        setBusy(null);
      }
    },
    [token, refresh, fail],
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
      if (!token) return;
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
    [token, fail],
  );

  const doDownload = useCallback(
    async (code: string) => {
      if (!token) return;
      setBusy(`dl:${code}`);
      try {
        // svg 로 받는다 — 인쇄용이라 벡터여야 어떤 크기로 뽑아도 선명하다.
        const blob = await fetchCodeQr(token, code, "svg");
        saveBlob(blob, `partner-${code}-qr.svg`);
      } catch (e) {
        fail(e);
      } finally {
        setBusy(null);
      }
    },
    [token, fail],
  );

  const shell = "min-h-screen bg-[#0b0b0d] px-5 py-8 text-[#E6E6E6]";

  if (!tokenLoaded) {
    return (
      <div className={shell}>
        <p className="text-sm text-white/40">불러오는 중…</p>
      </div>
    );
  }

  // 로그인은 **이 경로 안에서** 끝난다 — 앱 루트로 내보내면 스태프가 고객
  // 온보딩(사진 업로드)으로 떨어진다. 생산 콘솔과 같은 처리다.
  if (phase === "signed-out") {
    return (
      <div className="memorial-ui h-[100dvh] w-full overflow-hidden bg-[#0a0a0a]">
        <AuthScreen
          initialMode="login"
          onAuthComplete={() => {
            void getPremiumAccessToken().then((r) => {
              setToken(r.token);
              setTokenLoaded(true);
            });
          }}
        />
      </div>
    );
  }

  if (phase === "forbidden") {
    return (
      <div className={shell}>
        <h1 className="text-lg font-medium">Eternal Beam · 제휴처 콘솔</h1>
        <p className="mt-3 text-sm text-white/60">
          이 계정에는 운영 권한이 없습니다 (SHAKER_OPS_USER_IDS).
        </p>
      </div>
    );
  }

  return (
    <div className={shell}>
      <header className="mb-6">
        <h1 className="text-lg font-medium">Eternal Beam · 제휴처 콘솔</h1>
        <p className="mt-1 text-xs text-white/45">
          QR 코드는 서버가 무작위로 만듭니다. 코드를 끄면 새 귀속만 멈추고, 이미
          귀속된 편지·주문은 그대로 남습니다.
        </p>
      </header>

      {errorText ? (
        <p className="mb-4 rounded-lg bg-red-500/10 px-3 py-2 text-xs text-red-300">
          {errorText}
        </p>
      ) : null}

      <div className="mb-6 flex items-center gap-3">
        <button
          type="button"
          onClick={() => setShowForm((v) => !v)}
          className="rounded-lg bg-[#c9a227] px-4 py-2 text-sm font-semibold text-black"
        >
          {showForm ? "닫기" : "+ 제휴처 등록"}
        </button>
        <button
          type="button"
          onClick={() => void refresh()}
          disabled={busy !== null}
          className="rounded-lg border border-white/15 px-3 py-2 text-xs text-white/70"
        >
          {busy === "load" ? "새로고침 중…" : "새로고침"}
        </button>
      </div>

      {showForm ? (
        <section className="mb-8 rounded-xl border border-white/10 bg-black/40 p-4">
          <div className="grid gap-3 sm:grid-cols-2">
            <label className="block">
              <span className="mb-1 block text-xs text-white/50">제휴처 이름</span>
              <input
                value={formName}
                onChange={(e) => setFormName(e.target.value)}
                placeholder="예: 신림동물병원"
                className="w-full rounded-lg bg-black/50 px-3 py-2 text-sm outline-none ring-1 ring-white/10"
              />
            </label>

            <label className="block">
              <span className="mb-1 block text-xs text-white/50">유형</span>
              <select
                value={formType}
                onChange={(e) => setFormType(e.target.value as PartnerType)}
                className="w-full rounded-lg bg-black/50 px-3 py-2 text-sm outline-none ring-1 ring-white/10"
              >
                <option value="HOSPITAL">HOSPITAL · 동물병원</option>
                <option value="FUNERAL">FUNERAL · 장례식장</option>
              </select>
            </label>

            <label className="block">
              <span className="mb-1 block text-xs text-white/50">
                정산 비율 (0.15 = 15%)
              </span>
              <input
                value={formRate}
                onChange={(e) => setFormRate(e.target.value)}
                inputMode="decimal"
                placeholder="0.15"
                className="w-full rounded-lg bg-black/50 px-3 py-2 text-sm outline-none ring-1 ring-white/10"
              />
            </label>

            <label className="block">
              <span className="mb-1 block text-xs text-white/50">첫 QR 갈래</span>
              <select
                value={formTrack}
                onChange={(e) => setFormTrack(e.target.value as PartnerTrack | "")}
                className="w-full rounded-lg bg-black/50 px-3 py-2 text-sm outline-none ring-1 ring-white/10"
              >
                <option value="memorial">MEMORIAL · 떠난 아이</option>
                <option value="living">LIVING · 곁에 있는 아이</option>
                <option value="">발급하지 않음</option>
              </select>
            </label>
          </div>

          {formError ? (
            <p className="mt-3 text-xs text-red-300">{formError}</p>
          ) : null}

          <button
            type="button"
            onClick={() => void submitPartner()}
            disabled={busy === "create"}
            className="mt-4 rounded-lg bg-[#c9a227] px-4 py-2 text-sm font-semibold text-black disabled:opacity-50"
          >
            {busy === "create" ? "등록 중…" : "등록"}
          </button>
        </section>
      ) : null}

      {partners.length === 0 ? (
        <p className="text-sm text-white/40">등록된 제휴처가 없습니다.</p>
      ) : null}

      <div className="space-y-5">
        {partners.map((p) => (
          <section
            key={p.partnerId}
            className="rounded-xl border border-white/10 bg-black/30 p-4"
          >
            <div className="flex flex-wrap items-start justify-between gap-3">
              <div>
                <h2 className="text-base font-medium">{p.partnerName}</h2>
                <p className="mt-1 text-xs text-white/50">
                  {PARTNER_TYPE_LABEL[p.partnerType] ?? p.partnerType} ·{" "}
                  {p.partnerType} · 정산 {percentLabel(p.shareRate)}
                </p>
                <p className="mt-0.5 font-mono text-[11px] text-white/30">
                  {p.partnerId}
                </p>
              </div>

              <div className="flex items-center gap-2">
                <span
                  className={`rounded-full px-2.5 py-1 text-[11px] ${
                    p.active
                      ? "bg-emerald-500/15 text-emerald-300"
                      : "bg-white/10 text-white/45"
                  }`}
                >
                  {p.active ? "Active" : "Disabled"}
                </span>
                <button
                  type="button"
                  onClick={() => void doTogglePartner(p.partnerId, !p.active)}
                  disabled={busy === `partner:${p.partnerId}`}
                  className="rounded-lg border border-white/15 px-3 py-1.5 text-xs text-white/70 disabled:opacity-50"
                >
                  {p.active ? "제휴처 끄기" : "제휴처 켜기"}
                </button>
              </div>
            </div>

            {!p.active ? (
              <p className="mt-3 rounded-lg bg-amber-500/10 px-3 py-2 text-[11px] text-amber-200">
                제휴처가 꺼져 있어 모든 코드가 새 귀속을 만들지 않습니다. 이미
                귀속된 편지·주문은 그대로입니다.
              </p>
            ) : null}

            <div className="mt-4 space-y-3">
              {p.codes.length === 0 ? (
                <p className="text-xs text-white/35">발급된 QR 이 없습니다.</p>
              ) : null}

              {p.codes.map((c) => (
                <div
                  key={c.code}
                  className="rounded-lg border border-white/10 bg-black/40 p-3"
                >
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="rounded bg-white/10 px-2 py-0.5 text-[11px] tracking-wide">
                      {c.track ? TRACK_LABEL[c.track] : "갈래 없음 · 고객이 선택"}
                    </span>
                    <span
                      className={`rounded-full px-2 py-0.5 text-[11px] ${
                        c.active
                          ? "bg-emerald-500/15 text-emerald-300"
                          : "bg-white/10 text-white/45"
                      }`}
                    >
                      {c.active ? "Active" : "Disabled"}
                    </span>
                  </div>

                  <p className="mt-2 break-all font-mono text-[11px] text-white/60">
                    {partnerPublicUrl(c.code)}
                  </p>

                  {qrPreviews[c.code] ? (
                    <img
                      src={qrPreviews[c.code]}
                      alt={`QR ${c.code}`}
                      className="mt-3 h-32 w-32 rounded bg-white p-1.5"
                    />
                  ) : null}

                  <div className="mt-3 flex flex-wrap gap-2">
                    <button
                      type="button"
                      onClick={() => void doCopy(c.code)}
                      className="rounded-lg border border-white/15 px-3 py-1.5 text-xs text-white/70"
                    >
                      {copied === c.code ? "복사됨" : "링크 복사"}
                    </button>
                    <button
                      type="button"
                      onClick={() => void doPreview(c.code)}
                      disabled={busy === `qr:${c.code}`}
                      className="rounded-lg border border-white/15 px-3 py-1.5 text-xs text-white/70 disabled:opacity-50"
                    >
                      {busy === `qr:${c.code}` ? "…" : "QR 미리보기"}
                    </button>
                    <button
                      type="button"
                      onClick={() => void doDownload(c.code)}
                      disabled={busy === `dl:${c.code}`}
                      className="rounded-lg border border-white/15 px-3 py-1.5 text-xs text-white/70 disabled:opacity-50"
                    >
                      {busy === `dl:${c.code}` ? "…" : "QR 내려받기 (SVG)"}
                    </button>
                    <button
                      type="button"
                      onClick={() => void doToggleCode(c.code, !c.active)}
                      disabled={busy === `code:${c.code}`}
                      className="rounded-lg border border-white/15 px-3 py-1.5 text-xs text-white/70 disabled:opacity-50"
                    >
                      {c.active ? "코드 끄기" : "코드 켜기"}
                    </button>
                  </div>
                </div>
              ))}
            </div>

            <div className="mt-4 flex flex-wrap gap-2">
              <button
                type="button"
                onClick={() => void doIssue(p.partnerId, "memorial")}
                disabled={busy === `issue:${p.partnerId}`}
                className="rounded-lg border border-[#c9a227]/40 px-3 py-1.5 text-xs text-[#e8c85a] disabled:opacity-50"
              >
                + MEMORIAL QR 발급
              </button>
              <button
                type="button"
                onClick={() => void doIssue(p.partnerId, "living")}
                disabled={busy === `issue:${p.partnerId}`}
                className="rounded-lg border border-[#c9a227]/40 px-3 py-1.5 text-xs text-[#e8c85a] disabled:opacity-50"
              >
                + LIVING QR 발급
              </button>
            </div>
          </section>
        ))}
      </div>
    </div>
  );
}
