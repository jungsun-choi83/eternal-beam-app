import {
  cutoutImage,
  generateWithCredit,
  type GenerateWithCreditResult,
} from "@/app/services/videoProcessingApi";
import {
  ETERNAL_BEAM_PIPELINE_KEY,
  type StoredPipeline,
} from "@/components/memorial/ai-processing-screen";
import { getMemorialTheme } from "@/components/memorial/themes";

export function isInsufficientCreditsError(err: unknown): boolean {
  const msg = err instanceof Error ? err.message : String(err);
  return msg.includes("크레딧") || msg.toLowerCase().includes("insufficient credit");
}

export function isCreditSkippableError(err: unknown): boolean {
  const msg = err instanceof Error ? err.message : String(err);
  return (
    msg.includes("CREDIT_SKIP") ||
    msg.includes("502") ||
    msg.includes("503") ||
    msg.includes("504") ||
    msg.includes("시간") ||
    msg.includes("timeout") ||
    msg.includes("abort") ||
    msg.includes("Failed to fetch") ||
    msg.includes("누끼 서버")
  );
}

/** Luma용 공개 HTTPS URL (data URL이면 Storage 업로드 — 실패 시 재누끼 호출 안 함) */
export async function ensureCutoutPublicUrl(
  cutoutDisplay: string,
  userId: string,
  contentId?: string
): Promise<string> {
  const trimmed = cutoutDisplay.trim();
  if (trimmed.startsWith("http://") || trimmed.startsWith("https://")) {
    return trimmed;
  }

  try {
    const raw = sessionStorage.getItem(ETERNAL_BEAM_PIPELINE_KEY);
    if (raw) {
      const pipeline = JSON.parse(raw) as StoredPipeline;
      for (const u of [pipeline.dog_only_nobg_url, pipeline.cutout_display_url]) {
        if (u.startsWith("http://") || u.startsWith("https://")) return u;
      }
    }
  } catch {
    /* ignore */
  }

  if (trimmed.startsWith("data:")) {
    throw new Error(
      "CREDIT_SKIP: 서버 URL 없음 — 미리보기만 진행합니다."
    );
  }

  const res = await fetch(trimmed);
  if (!res.ok) throw new Error("누끼 이미지를 읽을 수 없습니다.");
  const blob = await res.blob();
  const file = new File([blob], "cutout.png", {
    type: blob.type || "image/png",
  });
  const cut = await cutoutImage(file, {
    userId,
    contentId,
    saveToStorage: true,
    model: "isnet-general-use",
    timeoutMs: 45_000,
  });
  if (cut.error) throw new Error(cut.error);
  if (cut.cutout_url) return cut.cutout_url;
  throw new Error(
    "누끼 URL을 만들 수 없습니다. Render에 SUPABASE_URL·Storage 버킷을 설정해 주세요."
  );
}

/**
 * 테마 1곳 × 4행동 Luma 제출 (크레딧 4개 차감).
 * LUMA_API_KEY 없으면 서버에서 제출 실패·환불될 수 있음.
 */
export async function runCreditMotionGeneration(params: {
  userId: string;
  cutoutDisplay: string;
  themeId: number;
  contentId?: string;
}): Promise<{ result: GenerateWithCreditResult; petImageUrl: string }> {
  const theme = getMemorialTheme(params.themeId);
  const placeId = theme?.themeKey ?? "snow_forest";

  const petImageUrl = await ensureCutoutPublicUrl(
    params.cutoutDisplay,
    params.userId,
    params.contentId
  );

  const result = await generateWithCredit({
    user_id: params.userId,
    pet_image_url: petImageUrl,
    selected_place_id: placeId,
    pet_id: localStorage.getItem("eternal_beam_pet_id") || undefined,
  });

  if (result.status === "failed" && result.submitted === 0) {
    const errText =
      result.submit_errors?.[0]?.error ||
      "Luma 영상 요청에 실패했습니다. LUMA_API_KEY와 Supabase를 확인하세요.";
    throw new Error(errText);
  }

  return { result, petImageUrl };
}
