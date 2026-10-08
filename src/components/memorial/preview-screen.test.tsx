/**
 * 미리보기(테마 선택 뒤) 화면의 생성 시작 흐름.
 *
 * 고객 흐름은 Upload/Cutout → Choose Theme → **CTA 하나** → Generation 이다.
 * CTA 를 누르면 곧바로 generation-run 이 만들어진다 — 사이에 "사진을 바꿀 수
 * 없다" 확인 패널이나 두 번째 버튼이 끼지 않는다. 내부 QA 컨트롤도 없다.
 * (사진 잠금 자체는 서버(PHASE1_LOCKED)와 업로드 화면이 그대로 지킨다.)
 */
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const runPhase7Generation = vi.fn();

vi.mock("@/lib/phase7-generation-flow", async () => {
  const actual = await vi.importActual<typeof import("@/lib/phase7-generation-flow")>(
    "@/lib/phase7-generation-flow",
  );
  return {
    ...actual,
    phase7GenerationEnabled: () => true,
    runPhase7Generation: (...args: unknown[]) => runPhase7Generation(...args),
  };
});
vi.mock("@/lib/pending-generation", async () => {
  const actual = await vi.importActual<typeof import("@/lib/pending-generation")>(
    "@/lib/pending-generation",
  );
  return {
    ...actual,
    getPendingCutoutMeta: () => ({
      contentId: "cid-preview-1",
      displayUrl: "data:image/png;base64,Y3V0b3V0",
    }),
  };
});
vi.mock("@/lib/pet-identity", () => ({ getEternalBeamPetId: () => "pet_preview_1" }));
vi.mock("@/lib/eternal-beam-user", () => ({ getEternalBeamUserId: () => "alice@test" }));
vi.mock("@/lib/pet-registry-api", () => ({ ensurePetRegistered: vi.fn(async () => {}) }));
vi.mock("@/lib/supabase-auth", () => ({ onAuthStateChange: () => () => {} }));
vi.mock("@/lib/device-connection-api", () => ({
  getDeviceConnectionState: vi.fn(async () => null),
}));
vi.mock("@/lib/use-processing-clock", () => ({ useProcessingClock: () => ({ seconds: 0 }) }));
vi.mock("@/components/memorial/theme-background-video", () => ({
  ThemeBackgroundVideo: () => null,
}));
vi.mock("@/components/memorial/pet-idle-display", () => ({ PetIdleDisplay: () => null }));
vi.mock("@/components/memorial/pet-photo", () => ({ PetPhoto: () => null }));
vi.mock("@/components/memorial/generation-progress-screen", () => ({
  GenerationProgressScreen: () => <div data-testid="generation-progress" />,
}));

import { PreviewScreen } from "./preview-screen";

const CTA = "Create video with this background";
const QA_LABELS = [
  /Does this feel like your pet\?/,
  /^Yes$/,
  /Issue: identity/,
  /Issue: anatomy/,
  /Issue: motion/,
];

function renderPreview() {
  return render(
    <PreviewScreen
      cutoutImage="data:image/png;base64,Y3V0b3V0"
      selectedTheme={1}
      language="en"
      settings={{ scale: 1, posX: 0, posY: 0 }}
      onSettingsChange={() => {}}
      onComplete={() => {}}
      onBack={() => {}}
    />,
  );
}

beforeEach(() => {
  sessionStorage.clear();
  localStorage.clear();
  runPhase7Generation.mockReset();
  // 실행은 시작만 하고 끝나지 않는다 — 화면은 진행 상태에 머문다.
  runPhase7Generation.mockImplementation(() => new Promise(() => {}));
});

afterEach(() => {
  cleanup();
});

describe("PreviewScreen generation start", () => {
  it("shows exactly one generation CTA and no lock-confirmation panel", () => {
    const { container } = renderPreview();
    expect(screen.getAllByRole("button", { name: CTA })).toHaveLength(1);
    expect(container.querySelectorAll(".preview-composer__cta")).toHaveLength(1);
    expect(screen.queryByRole("alertdialog")).toBeNull();
    expect(container.querySelector(".preview-composer__lock-confirm")).toBeNull();
    expect(screen.queryByText(/I understand/)).toBeNull();
    expect(screen.queryByText(/can't be added, removed or replaced/)).toBeNull();
  });

  it("starts generation directly from the single CTA — no second confirmation step", async () => {
    const { container } = renderPreview();
    fireEvent.click(screen.getByRole("button", { name: CTA }));
    await waitFor(() => expect(runPhase7Generation).toHaveBeenCalledTimes(1));
    expect(runPhase7Generation.mock.calls[0][0]).toMatchObject({
      petId: "pet_preview_1",
      contentId: "cid-preview-1",
    });
    expect(screen.queryByRole("alertdialog")).toBeNull();
    expect(container.querySelector(".preview-composer__lock-confirm")).toBeNull();
    expect(screen.queryByText(/I understand/)).toBeNull();
  });

  it("shows no internal QA controls in the customer flow", async () => {
    const { container } = renderPreview();
    const assertNoQA = () => {
      expect(container.querySelector("[data-business-qa-feedback]")).toBeNull();
      for (const label of QA_LABELS) expect(screen.queryByText(label)).toBeNull();
    };
    assertNoQA();
    fireEvent.click(screen.getByRole("button", { name: CTA }));
    await waitFor(() => expect(runPhase7Generation).toHaveBeenCalledTimes(1));
    assertNoQA();
  });
});
