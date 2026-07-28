import { shouldSyncThemeToDevice } from "@/lib/device-theme-sync";
import { triggerPetReadyOnDevice } from "@/lib/pi-sensor-bridge";
import { syncPetProfileToDevice } from "@/lib/pet-profile";

export type PetReadyPayload = {
  contentId: string;
  idleUrl?: string | null;
  cutoutUrl?: string | null;
};

/** idle 생성 완료 → S23 Unity(VFX) 로드 — Pi 배경은 변경하지 않음 */
export function schedulePetReadyToDevice(payload: PetReadyPayload): void {
  if (!shouldSyncThemeToDevice()) return;
  if (!payload.contentId.trim()) return;

  void syncPetProfileToDevice();
  void triggerPetReadyOnDevice(payload);
}
