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

// ── Device D1/M5-lite — Phase 7 모션을 기기(S23)로 보낸다 ───────────────────

export type Phase7DevicePush = {
  contentId: string;
  petId: string;
  /** DEVICE_PRELOAD_MOTIONS 중 하나. 없으면 BREATHING (D1 하위호환). */
  motionId?: string;
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
 * Phase 7 packed 자산 1건 → Pi(:8787) → UDP → S23.
 *
 * 테마와 완전히 분리된 메시지다 — 배경은 /demo/play 가 따로 나른다.
 * 미발행(REVIEW) 자산은 리졸버가 device_test_only 로 명시했을 때만 나간다:
 * 이 함수는 QA/발행 상태를 절대 바꾸지 않고, 프로덕션 홈 모션이라고
 * 주장하지도 않는다. URL 은 리졸버의 호출 시점 서명 그대로다.
 */
export async function sendPhase7MotionToDevice(
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
    motionId: push.motionId ?? "BREATHING",
    packedUrl: playback.url,
    deliveryFormat: playback.delivery_format ?? null,
    cutoutUrl: push.cutoutUrl,
  });
  if (!built.ok) return { sent: false, reason: built.reason };
  const { postPetReadyBodyToDevice } = await import("./pi-sensor-bridge.ts");
  const ok = await postPetReadyBodyToDevice(built.body);
  return ok ? { sent: true, body: built.body } : { sent: false, reason: "pi_unreachable" };
}

/** D1 이름 유지 — 기존 호출부/절차 문서와의 호환용 별칭. */
export async function sendPhase7BreathingToDevice(
  push: Phase7DevicePush
): Promise<Phase7DevicePushResult> {
  return sendPhase7MotionToDevice({ ...push, motionId: "BREATHING" });
}

// ── Device M5-lite Phase 1 — READY 모션 프리로드 ────────────────────────────

export type DeviceMotionPreloadAsset = {
  motionId: string;
  /** GET /premium/assets ready_assets 의 재서명 URL 또는 발행 BREATHING 하이드레이션 URL. */
  url: string | null | undefined;
  deliveryFormat?: string | null;
};

export type DeviceMotionPreloadReport = {
  /** POST 성공한 motion_id (전송 순서 그대로 — BREATHING 이 항상 마지막). */
  sent: string[];
  /** Pi POST 실패 (도달 불가 등). */
  failed: string[];
};

/**
 * READY 모션 세트를 기기(S23)에 **저장용으로** 실어 둔다 — 재생 지시가 아니다.
 *
 * 입력 URL 은 발행 자산만 주는 두 발견 계약에서 와야 한다:
 * GET /premium/assets 의 ready_assets(발행된 프리미엄 모션만 나온다)와
 * 발행 BREATHING 하이드레이션(pets.breathing_* 포인터). REVIEW/미발행 자산은
 * 그 계약들에 아예 나타나지 않으므로 여기서 QA 상태를 다시 묻지 않는다 —
 * 리졸버 playback 응답을 실어 보내는 쪽은 sendPhase7MotionToDevice 를 쓸 것.
 *
 * 전송은 **순차**다: buildDeviceMotionPreloadBodies 의 순서 계약(BREATHING
 * 마지막)이 UDP 도착 순서로도 유지되어야 단일 슬롯 구형 빌드의 홈 루프가
 * BREATHING 으로 남는다.
 */
export async function preloadDeviceMotions(args: {
  contentId: string;
  petId: string;
  cutoutUrl?: string | null;
  motions: DeviceMotionPreloadAsset[];
}): Promise<DeviceMotionPreloadReport> {
  const report: DeviceMotionPreloadReport = { sent: [], failed: [] };
  if (!shouldSyncThemeToDevice()) return report;
  const { buildDeviceMotionPreloadBodies } = await import("./pet-ready-payload.ts");
  const bodies = buildDeviceMotionPreloadBodies({
    contentId: args.contentId,
    petId: args.petId,
    cutoutUrl: args.cutoutUrl,
    motions: args.motions.map((m) => ({
      motionId: m.motionId,
      packedUrl: m.url,
      deliveryFormat: m.deliveryFormat,
    })),
  });
  if (!bodies.length) return report;
  const { postPetReadyBodyToDevice } = await import("./pi-sensor-bridge.ts");
  for (const { motionId, body } of bodies) {
    const ok = await postPetReadyBodyToDevice(body);
    (ok ? report.sent : report.failed).push(motionId);
  }
  return report;
}
