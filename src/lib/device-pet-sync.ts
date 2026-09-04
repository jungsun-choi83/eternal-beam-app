import { shouldSyncThemeToDevice } from "@/lib/device-theme-sync";
import { triggerPetReadyOnDevice } from "@/lib/pi-sensor-bridge";
import { syncPetProfileToDevice } from "@/lib/pet-profile";

export type PetReadyPayload = {
  contentId: string;
  idleUrl?: string | null;
  cutoutUrl?: string | null;
  /** packed vstack URL (선택). 있으면 S23 이 단일 디코더 packed 모드로 재생. */
  packedUrl?: string | null;
};

/** idle 생성 완료 → S23 Unity(VFX) 로드 — Pi 배경은 변경하지 않음 */
export function schedulePetReadyToDevice(payload: PetReadyPayload): void {
  if (!shouldSyncThemeToDevice()) return;
  if (!payload.contentId.trim()) return;

  void syncPetProfileToDevice();
  void triggerPetReadyOnDevice(payload);
}

// ── Device D1 — Phase 7 BREATHING 1건을 기기(S23)로 보낸다 ──────────────────

export type Phase7DevicePush = {
  contentId: string;
  petId: string;
  /** 재생 리졸버(GET /generation-runs/{id}/playback 또는 하이드레이션)의 응답. */
  playback: {
    url: string;
    delivery_format?: string | null;
    qa_decision?: string;
    published?: boolean;
    device_test_only?: boolean;
  };
  cutoutUrl?: string | null;
};

export type Phase7DevicePushResult =
  | { sent: true; body: Record<string, string> }
  | { sent: false; reason: string };

/**
 * Phase 7 BREATHING packed 자산 → Pi(:8787) → UDP → S23.
 *
 * 테마와 완전히 분리된 메시지다 — 배경은 /demo/play 가 따로 나른다.
 * 미발행(REVIEW) 자산은 리졸버가 device_test_only 로 명시했을 때만 나간다:
 * 이 함수는 QA/발행 상태를 절대 바꾸지 않고, 프로덕션 홈 모션이라고
 * 주장하지도 않는다. URL 은 리졸버의 호출 시점 서명 그대로다.
 */
export async function sendPhase7BreathingToDevice(
  push: Phase7DevicePush
): Promise<Phase7DevicePushResult> {
  if (!shouldSyncThemeToDevice()) return { sent: false, reason: "device_sync_disabled" };
  const { playback } = push;
  // 미발행인데 명시 표식이 없으면 보내지 않는다 — 구서버/오조립 응답 방어.
  if (playback.published !== true && playback.device_test_only !== true) {
    return { sent: false, reason: "unpublished_without_device_test_marker" };
  }
  const { buildPhase7PetReadyBody } = await import("./pet-ready-payload.ts");
  const built = buildPhase7PetReadyBody({
    contentId: push.contentId,
    petId: push.petId,
    motionId: "BREATHING",
    packedUrl: playback.url,
    deliveryFormat: playback.delivery_format ?? null,
    cutoutUrl: push.cutoutUrl,
  });
  if (!built.ok) return { sent: false, reason: built.reason };
  const { postPetReadyBodyToDevice } = await import("./pi-sensor-bridge.ts");
  const ok = await postPetReadyBodyToDevice(built.body);
  return ok ? { sent: true, body: built.body } : { sent: false, reason: "pi_unreachable" };
}
