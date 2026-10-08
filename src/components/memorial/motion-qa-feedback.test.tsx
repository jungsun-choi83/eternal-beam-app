/**
 * 내부 모션 QA 컨트롤은 고객 화면에 절대 나타나지 않는다 (백엔드가 business-QA
 * 코호트로 등록했더라도). 개발 빌드에서 VITE_INTERNAL_MOTION_QA=1 로 명시적으로
 * 켠 경우에만 보인다.
 */
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { MotionQAFeedback } from "@/components/memorial/motion-qa-feedback";

const QA_LABELS = [
  /Does this feel like your pet\?/,
  /^Yes$/,
  /Issue: identity/,
  /Issue: anatomy/,
  /Issue: motion/,
];

function renderQA() {
  return render(
    <MotionQAFeedback enrolled language="en" status="idle" onFeedback={() => {}} />,
  );
}

afterEach(() => {
  cleanup();
  vi.unstubAllEnvs();
});

describe("MotionQAFeedback", () => {
  it("renders no QA controls in production mode, even when enrolled", () => {
    vi.stubEnv("DEV", false);
    vi.stubEnv("PROD", true);
    vi.stubEnv("VITE_INTERNAL_MOTION_QA", "1");
    const { container } = renderQA();
    expect(container.querySelector("[data-business-qa-feedback]")).toBeNull();
    for (const label of QA_LABELS) {
      expect(screen.queryByText(label)).toBeNull();
    }
    expect(screen.queryAllByRole("button")).toHaveLength(0);
  });

  it("renders no QA controls in a dev build unless explicitly opted in", () => {
    vi.stubEnv("DEV", true);
    vi.stubEnv("PROD", false);
    vi.stubEnv("VITE_INTERNAL_MOTION_QA", "");
    const { container } = renderQA();
    expect(container.firstChild).toBeNull();
    expect(screen.queryAllByRole("button")).toHaveLength(0);
  });

  it("renders nothing when not enrolled", () => {
    vi.stubEnv("DEV", true);
    vi.stubEnv("PROD", false);
    vi.stubEnv("VITE_INTERNAL_MOTION_QA", "1");
    const { container } = render(
      <MotionQAFeedback enrolled={false} language="en" status="idle" onFeedback={() => {}} />,
    );
    expect(container.firstChild).toBeNull();
  });

  it("still renders for internal evaluation in an opted-in dev build", () => {
    vi.stubEnv("DEV", true);
    vi.stubEnv("PROD", false);
    vi.stubEnv("VITE_INTERNAL_MOTION_QA", "1");
    renderQA();
    for (const label of QA_LABELS) {
      expect(screen.getByText(label)).toBeTruthy();
    }
  });
});
