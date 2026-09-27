"use client";

import { Check } from "lucide-react";
import { BackButton } from "@/components/ui/screen-header";
import { StatusBadge } from "@/components/ui/status-badge";
import { PrimaryButton } from "@/components/ui/buttons";
import { memorialT } from "@/components/memorial/memorial-i18n";
import {
  type GenerationStageKey,
  type GenerationProgressView,
} from "@/lib/generation-progress";

/**
 * The run contract intentionally keeps its more granular seven render keys.
 * This list only groups those keys into the five phases a customer needs to
 * understand; it never feeds back into polling or the generation state machine.
 */
const DISPLAY_PHASES: readonly {
  key: string;
  labelStage: GenerationStageKey;
}[] = [
  { key: "preparing", labelStage: "preparing_pet" },
  { key: "appearance", labelStage: "creating_appearance" },
  { key: "motion", labelStage: "generating_motion" },
  { key: "checking", labelStage: "checking_result" },
  { key: "rendering", labelStage: "rendering" },
];

const DISPLAY_PHASE_INDEX: Record<GenerationStageKey, number> = {
  preparing_pet: 0,
  creating_appearance: 1,
  waiting_capacity: 2,
  generating_motion: 2,
  checking_result: 3,
  rendering: 4,
  complete: DISPLAY_PHASES.length,
};

function formatElapsed(totalSeconds: number): string {
  const s = Math.max(0, Math.floor(totalSeconds));
  const m = Math.floor(s / 60);
  const r = s % 60;
  return `${m}:${String(r).padStart(2, "0")}`;
}

interface GenerationProgressScreenProps {
  view: GenerationProgressView;
  elapsedSec: number;
  language?: string;
  /** 정적 누끼 썸네일 — 생성 중에도 "무엇을 만들고 있는지" 눈으로 확인시켜 준다. */
  previewImageUrl?: string | null;
  onBack?: () => void;
  onRetry?: () => void;
  retrying?: boolean;
}

/**
 * Phase 4 — generation-run 진행 화면.
 *
 * 모바일: 미리보기와 상태가 한 표면으로 이어진 세로 구성. 데스크톱(1024px+):
 * 펫 미리보기 왼쪽, 상태 오른쪽의 넓은 2열 구성(app-shell wide 스테이지).
 * 반응형 전환은 전부 CSS(generation-progress.css)가 맡는다 — JS 브레이크포인트
 * 분기를 두지 않는다.
 */
export function GenerationProgressScreen({
  view,
  elapsedSec,
  language = "ko",
  previewImageUrl = null,
  onBack,
  onRetry,
  retrying = false,
}: GenerationProgressScreenProps) {
  const t = memorialT(language).generationProgress;

  const header = (
    <header className="gen-progress__header">
      <div className="gen-progress__back-row">
        {onBack ? <BackButton onClick={onBack} label={t.back} /> : <span />}
      </div>
      <div className="gen-progress__intro">
        <h1 className="gen-progress__title">{t.title}</h1>
        <p className="gen-progress__supporting-copy">{t.supportingCopy}</p>
      </div>
    </header>
  );

  if (view.kind === "error") {
    const title = view.recoverable ? t.errorRecoverableTitle : t.errorTitle;
    const hint = view.recoverable ? t.errorRecoverableHint : null;
    return (
      <div className="gen-progress">
        {header}
        <div className="gen-progress__scroll">
          <div className={`gen-progress__panel${previewImageUrl ? "" : " gen-progress__panel--without-preview"}`}>
            {previewImageUrl ? (
              <div className="gen-progress__preview">
                <img src={previewImageUrl} alt={t.previewAlt} decoding="async" />
              </div>
            ) : null}
            <div className="gen-progress__error-card" role="alert">
              <p className="gen-progress__error-title">{title}</p>
              {view.message ? <p className="gen-progress__error-message">{view.message}</p> : null}
              {hint ? <p className="gen-progress__error-message">{hint}</p> : null}
              {onRetry ? (
                <PrimaryButton onClick={onRetry} state={retrying ? "loading" : "default"}>
                  {t.retry}
                </PrimaryButton>
              ) : null}
            </div>
          </div>
        </div>
      </div>
    );
  }

  const phaseIndex = DISPLAY_PHASE_INDEX[view.stage];
  const stageCopy = t.stages[view.stage];
  // 일부 단계만 "대기 중" 전용 문구를 갖는다(memorial-i18n.ts) — 없으면 기본
  // active 문구로 떨어진다. 유니온 좁히기 대신 느슨한 캐스트로 안전하게 읽는다.
  const waitingCopy = (stageCopy as { waiting?: string }).waiting;
  const headline = (view.waiting && waitingCopy) || stageCopy.active;

  return (
    <div className="gen-progress">
      {header}
      <div className="gen-progress__scroll">
        <div className={`gen-progress__panel eb-fade-up${previewImageUrl ? "" : " gen-progress__panel--without-preview"}`}>
          <div className={`gen-progress__preview ${view.stage !== "complete" ? "gen-progress__preview--active" : ""}`}>
            {previewImageUrl ? <img src={previewImageUrl} alt={t.previewAlt} decoding="async" /> : null}
          </div>

          <div className="gen-progress__status" aria-live="polite">
            <div className="gen-progress__headline-row">
              <div>
                <p className="gen-progress__eyebrow">{t.eyebrow}</p>
                <p className="gen-progress__headline">{headline}</p>
              </div>
              <p className="gen-progress__timer">
                <span>{t.elapsedLabel}</span>
                <strong>{formatElapsed(elapsedSec)}</strong>
              </p>
            </div>

            <StatusBadge tone={view.stage === "complete" ? "success" : view.waiting ? "waiting" : "generating"}>
              {view.stage === "complete" ? t.stages.complete.label : view.waiting ? t.waitingBadge : t.activeBadge}
            </StatusBadge>

            <ol className="gen-progress__steps">
              {DISPLAY_PHASES.map((phase, index) => {
                const state = index < phaseIndex ? "done" : index === phaseIndex ? "active" : "pending";
                return (
                  <li
                    key={phase.key}
                    className={`gen-progress__step gen-progress__step--${state}`}
                    aria-current={state === "active" ? "step" : undefined}
                  >
                    <span className="gen-progress__step-marker" aria-hidden>
                      {state === "done" ? <Check className="w-3.5 h-3.5" strokeWidth={3} /> : index + 1}
                    </span>
                    <span className="gen-progress__step-label">{t.stages[phase.labelStage].label}</span>
                  </li>
                );
              })}
            </ol>

            {view.stage !== "complete" ? <p className="gen-progress__hint">{t.elapsedHint}</p> : null}
          </div>
        </div>
      </div>
    </div>
  );
}
