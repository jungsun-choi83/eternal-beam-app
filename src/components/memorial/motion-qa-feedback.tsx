/**
 * 내부 모션 QA 피드백 컨트롤 ("Does this feel like your pet?" / Yes / Issue: …).
 *
 * 내부 평가용 UI 다 — 일반 고객 화면에는 **절대 나오지 않는다**. 백엔드가
 * business-QA 코호트로 등록(enrolled)했더라도, 개발 빌드에서
 * VITE_INTERNAL_MOTION_QA=1 로 명시적으로 켠 경우에만 렌더한다. 피드백 API 와
 * 백엔드 텔레메트리는 그대로 두고, 화면 노출만 막는다.
 */
export type MotionQAComplaint = "IDENTITY" | "ANATOMY" | "MOTION";
export type MotionQAFeedbackStatus = "idle" | "submitting" | "sent" | "error";

/** 호출 시점에 읽는다 — 프로덕션 빌드(PROD)에서는 항상 false, 개발 빌드도 기본 false. */
export function isInternalMotionQAEnabled(): boolean {
  if (!import.meta.env.DEV || import.meta.env.PROD) return false;
  return String(import.meta.env.VITE_INTERNAL_MOTION_QA ?? "").trim() === "1";
}

interface MotionQAFeedbackProps {
  enrolled: boolean;
  language: "en" | "ko" | string;
  status: MotionQAFeedbackStatus;
  onFeedback: (accepted: boolean, complaint?: MotionQAComplaint) => void;
}

export function MotionQAFeedback({ enrolled, language, status, onFeedback }: MotionQAFeedbackProps) {
  if (!enrolled || !isInternalMotionQAEnabled()) return null;

  return (
    <div className="rounded-2xl border border-white/10 bg-white/5 p-3" data-business-qa-feedback>
      <p className="mb-2 text-sm text-white/80">
        {language === "en" ? "Does this feel like your pet?" : "우리 아이답게 느껴지나요?"}
      </p>
      {status === "sent" ? (
        <p className="text-sm text-white/70" role="status">
          {language === "en" ? "Thank you for your feedback." : "의견을 남겨주셔서 감사합니다."}
        </p>
      ) : (
        <div className="flex flex-wrap gap-2">
          <button
            type="button"
            className="mem-btn-secondary"
            disabled={status === "submitting"}
            onClick={() => onFeedback(true)}
          >
            {language === "en" ? "Yes" : "네"}
          </button>
          {(["IDENTITY", "ANATOMY", "MOTION"] as const).map((complaint) => (
            <button
              key={complaint}
              type="button"
              className="mem-btn-secondary"
              disabled={status === "submitting"}
              onClick={() => onFeedback(false, complaint)}
            >
              {language === "en"
                ? `Issue: ${complaint.toLowerCase()}`
                : complaint === "IDENTITY"
                  ? "닮지 않음"
                  : complaint === "ANATOMY"
                    ? "신체 이상"
                    : "움직임 이상"}
            </button>
          ))}
        </div>
      )}
      {status === "error" ? (
        <p className="mt-2 text-sm text-red-300" role="alert">
          {language === "en"
            ? "Feedback could not be saved. Please try again."
            : "의견을 저장하지 못했습니다. 다시 시도해 주세요."}
        </p>
      ) : null}
    </div>
  );
}
