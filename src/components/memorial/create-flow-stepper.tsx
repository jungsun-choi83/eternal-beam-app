"use client";

import { Check } from "lucide-react";
import type { CreateFlowStepStatus } from "@/lib/create-flow-steps";

export interface CreateFlowStepView {
  id: string;
  label: string;
  status: CreateFlowStepStatus;
  statusLabel: string;
}

interface CreateFlowStepperProps {
  steps: CreateFlowStepView[];
  /** 접근성 레이블 — 화면마다 다른 흐름을 가리킬 수 있어 주입받는다. */
  ariaLabel: string;
  /** 좁은 화면(모바일)의 압축 바에 쓸 "3/5 · 테마 선택" 형태 문구. */
  compactSummary: string;
  className?: string;
}

/**
 * Create 흐름 진행 표시 — 데스크톱은 세로 목록, 모바일은 압축 바.
 *
 * 두 마크업 모두 항상 DOM에 있고 CSS 컨테이너 쿼리(create-flow-stepper.css 아님,
 * theme-selection.css 의 기존 관례 — 화면 자신의 폭 기준)가 어느 쪽을 보일지
 * 정한다. 이 화면(Theme Selection)이 My Library 안의 좁은 상자에서도 재사용되기
 * 때문에 뷰포트 media query 로는 그 임베딩에서 올바르게 좁아지지 않는다.
 */
export function CreateFlowStepper({
  steps,
  ariaLabel,
  compactSummary,
  className = "",
}: CreateFlowStepperProps) {
  const currentIndex = Math.max(
    0,
    steps.findIndex((s) => s.status === "current")
  );

  return (
    <nav aria-label={ariaLabel} className={`create-flow-stepper ${className}`}>
      <ol className="create-flow-stepper__full">
        {steps.map((step, index) => (
          <li
            key={step.id}
            className={`create-flow-stepper__item create-flow-stepper__item--${step.status}`}
            aria-current={step.status === "current" ? "step" : undefined}
          >
            <span className="create-flow-stepper__marker" aria-hidden>
              {step.status === "complete" ? (
                <Check className="w-3.5 h-3.5" strokeWidth={3} />
              ) : (
                index + 1
              )}
            </span>
            <span className="create-flow-stepper__copy">
              <span className="create-flow-stepper__label">{step.label}</span>
              <span className="create-flow-stepper__status">{step.statusLabel}</span>
            </span>
          </li>
        ))}
      </ol>

      <div className="create-flow-stepper__compact">
        <ol className="create-flow-stepper__dots" aria-hidden>
          {steps.map((step) => (
            <li
              key={step.id}
              className={`create-flow-stepper__dot create-flow-stepper__dot--${step.status}`}
            />
          ))}
        </ol>
        <span className="create-flow-stepper__compact-label">
          {compactSummary} · {steps[currentIndex]?.label}
        </span>
      </div>
    </nav>
  );
}
