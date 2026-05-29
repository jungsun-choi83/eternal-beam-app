import {
  ETERNAL_BEAM_PIPELINE_KEY,
  type StoredPipeline,
} from "@/components/memorial/ai-processing-screen";
import { loadCreditSession } from "@/lib/credit-session";

function isVideoUrl(url: string): boolean {
  const u = url.toLowerCase();
  return (
    u.startsWith("http") &&
    (u.endsWith(".mp4") || u.endsWith(".webm") || u.endsWith(".mov"))
  );
}

export function readPipeline(): StoredPipeline | null {
  try {
    const raw = sessionStorage.getItem(ETERNAL_BEAM_PIPELINE_KEY);
    if (!raw) return null;
    return JSON.parse(raw) as StoredPipeline;
  } catch {
    return null;
  }
}

/** 미리보기 완료 / 기기 전송 전 — NFC·슬롯 API용 ID 저장 */
export function persistDeviceContentFromPipeline(
  selectedTheme: number | null
): string {
  const pipeline = readPipeline();
  const credit = loadCreditSession();
  const contentId =
    pipeline?.content_id ||
    credit?.session_id ||
    `local_${Date.now()}`;

  localStorage.setItem("eternal_beam_content_id", contentId);
  localStorage.setItem("eternal_beam_current_content_id", contentId);

  const videoCandidate =
    pipeline?.idle_video_url && isVideoUrl(pipeline.idle_video_url)
      ? pipeline.idle_video_url
      : pipeline?.action_video_url && isVideoUrl(pipeline.action_video_url)
        ? pipeline.action_video_url
        : null;

  if (videoCandidate) {
    localStorage.setItem("eternal_beam_hologram_video_id", videoCandidate);
    localStorage.setItem("eternal_beam_current_video_id", videoCandidate);
  } else {
    localStorage.setItem("eternal_beam_hologram_video_id", contentId);
  }

  if (selectedTheme != null) {
    localStorage.setItem("eternal_beam_selected_theme_id", String(selectedTheme));
  }

  if (credit?.pet_id) {
    localStorage.setItem("eternal_beam_pet_id", credit.pet_id);
  }
  if (credit?.place_id) {
    localStorage.setItem("eternal_beam_place_id", credit.place_id);
  }

  return contentId;
}

export function getStoredContentId(): string | null {
  return (
    localStorage.getItem("eternal_beam_content_id") ||
    localStorage.getItem("eternal_beam_current_content_id") ||
    readPipeline()?.content_id ||
    null
  );
}
