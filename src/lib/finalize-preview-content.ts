import { composeFinal } from "@/app/services/videoProcessingApi";
import {
  getMemorialTheme,
  type MemorialTheme,
} from "@/components/memorial/themes";
import { getEternalBeamUserId } from "@/lib/eternal-beam-user";
import { triggerThemeOnDevice } from "@/lib/pi-sensor-bridge";
import { syncPetProfileToDevice } from "@/lib/pet-profile";
import {
  getStoredContentId,
  persistDeviceContentFromPipeline,
  readPipeline,
} from "@/lib/persist-device-content";
import { getThemeBackgroundApiId } from "@/lib/theme-selection-store";

export type PreviewFinalizeSettings = {
  scale: number;
  posX: number;
  posY: number;
};

const SHIPPING_STORAGE_KEY = "eternal_beam_shipping_address";

export type ShippingAddress = {
  recipientName: string;
  phone: string;
  postalCode: string;
  addressLine1: string;
  addressLine2?: string;
};

export function saveShippingAddress(address: ShippingAddress): void {
  localStorage.setItem(SHIPPING_STORAGE_KEY, JSON.stringify(address));
}

export function readShippingAddress(): ShippingAddress | null {
  try {
    const raw = localStorage.getItem(SHIPPING_STORAGE_KEY);
    if (!raw) return null;
    return JSON.parse(raw) as ShippingAddress;
  } catch {
    return null;
  }
}

/** 미리보기 완료 — Content_ID 생성 + localStorage 동기화 */
export async function finalizePreviewContent(
  themeId: number,
  settings: PreviewFinalizeSettings
): Promise<string> {
  const theme = getMemorialTheme(themeId);
  if (!theme) throw new Error("Unknown theme");

  persistDeviceContentFromPipeline(themeId);

  const pipeline = readPipeline();
  const subjectId =
    pipeline?.content_id ||
    getStoredContentId() ||
    `subject_${Date.now()}`;

  try {
    const { content_id, nfc_payload } = await composeFinal({
      background_id: getThemeBackgroundApiId(theme),
      subject_id: subjectId,
      scale: settings.scale,
      position_x: settings.posX,
      position_y: settings.posY,
      user_id: getEternalBeamUserId(),
    });
    localStorage.setItem("eternal_beam_content_id", content_id);
    localStorage.setItem("eternal_beam_current_content_id", content_id);
    localStorage.setItem("eternal_beam_nfc_payload", JSON.stringify(nfc_payload));
    return content_id;
  } catch {
    const fallback = persistDeviceContentFromPipeline(themeId);
    return fallback;
  }
}

/** 무료 배경 — 연결된 기기(Pi/S23)로 즉시 송출 트리거 */
export async function broadcastFreeThemeToDevice(
  theme: MemorialTheme,
  contentId: string
): Promise<boolean> {
  void syncPetProfileToDevice();
  return triggerThemeOnDevice(theme.themeKey, contentId);
}
