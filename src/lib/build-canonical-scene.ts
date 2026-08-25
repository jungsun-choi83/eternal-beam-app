/**
 * 승인 → 정본 장면 1건. **세 가지 배경을 한 경로로 처리한다.**
 *
 *   ORIGINAL  원본 사진 그대로 (펫이 이미 원래 자리에 있다)
 *   THEME     Eternal Beam 배경 + 승인된 배치
 *   CUSTOM    고객이 올린/생성한 배경 + 승인된 배치
 *
 * 배경 종류마다 생성 아키텍처를 따로 만들지 않는다 — 갈라지는 것은 **배경 이미지를
 * 어디서 가져오는가**뿐이고, 그 뒤(합성 → 업로드 → 장면 레코드)는 완전히 같다.
 */

import {
  deriveSceneId,
  saveCanonicalScene,
  type BackgroundType,
  type CanonicalScene,
} from "./canonical-scene.ts";
import { composeSceneImage, type ScenePlacement } from "./scene-export.ts";
import { SceneError } from "./scene-errors.ts";
import {
  getEffectiveBgVideo,
  getStoredCustomBgVideoUrl,
  isCustomPhotoBgTheme,
} from "./custom-background-store.ts";
import type { MemorialTheme } from "@/components/memorial/themes";  // 타입 전용 — 런타임에 지워진다
import { ORIGINAL_PHOTO_THEME_KEY } from "@/components/memorial/themes";

/** 원본 사진(업로드 원본)의 저장 키 — UploadScreen/EternalBeamApp 이 쓴다. */
const MAIN_PHOTO_KEY = "eternal_beam_main_photo";

export function readOriginalPhoto(): string | null {
  try {
    return localStorage.getItem(MAIN_PHOTO_KEY)?.trim() || null;
  } catch {
    return null;
  }
}

function apiBase(): string {
  try {
    const raw = (import.meta as { env?: Record<string, string> }).env
      ?.VITE_API_BASE_URL;
    return (raw || "").trim().replace(/\/$/, "");
  } catch {
    return "";
  }
}

async function blobToDataUrl(blob: Blob): Promise<string> {
  return await new Promise((resolve, reject) => {
    const fr = new FileReader();
    fr.onload = () => resolve(String(fr.result || ""));
    fr.onerror = () => reject(new Error("장면 이미지를 읽지 못했습니다."));
    fr.readAsDataURL(blob);
  });
}

async function uploadScene(params: {
  userId: string;
  contentId: string;
  sceneId: string;
  dataUrl: string;
}): Promise<string> {
  const res = await fetch(`${apiBase()}/api/assets/scene`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      user_id: params.userId,
      content_id: params.contentId,
      scene_id: params.sceneId,
      data_url: params.dataUrl,
    }),
  });
  if (!res.ok) {
    throw new SceneError(
      "SCENE_UPLOAD_FAILED",
      `장면 업로드 실패 (HTTP ${res.status})`
    );
  }
  const b = (await res.json()) as { scene_keyframe_url?: string };
  const url = (b.scene_keyframe_url || "").trim();
  if (!url) throw new SceneError("SCENE_UPLOAD_FAILED", "장면 주소가 비어 있습니다.");
  return url;
}

export interface SceneBackgroundChoice {
  type: BackgroundType;
  /** THEME 이면 테마, 아니면 무시. */
  theme?: MemorialTheme | null;
  /** CUSTOM 이면 배경 자산 주소. */
  customUrl?: string | null;
}

/**
 * 배경 선택 → (배경 주소, 배경 id).
 *
 * ORIGINAL 이 null 주소를 갖는 것은 실패가 아니다 — 원본 경로는 합성 자체를
 * 건너뛰기 때문이다(아래 buildCanonicalScene 참고).
 */
export function resolveBackgroundSource(choice: SceneBackgroundChoice): {
  url: string | null;
  backgroundId: string;
} {
  if (choice.type === "original") {
    return { url: readOriginalPhoto(), backgroundId: "original" };
  }
  if (choice.type === "custom") {
    const u = (choice.customUrl || "").trim();
    return { url: u || null, backgroundId: u || "custom" };
  }
  const theme = choice.theme || null;
  return {
    url: getEffectiveBgVideo(theme) || null,
    backgroundId: theme?.themeKey || "",
  };
}

/**
 * 원본 배경을 뜻하는 테마 키.
 *
 * themes.ts 의 ORIGINAL_PHOTO_THEME_KEY 를 **그대로 재수출한다** — 두 곳에
 * 문자열을 각각 적어 두면 한쪽만 바뀌는 날이 오고, 그때 원본 배경은 조용히
 * 테마 배경으로 처리된다.
 */
export const ORIGINAL_BG_THEME_KEY = ORIGINAL_PHOTO_THEME_KEY;

/**
 * 선택된 테마 → 배경 갈래. **세 갈래가 한 함수에서 갈린다.**
 *
 * 여기가 유일한 분기점이라 생성·재생·저장 어디에도 배경 종류별 코드가 없다.
 */
export function resolveSceneBackground(
  theme: MemorialTheme | null | undefined
): SceneBackgroundChoice {
  const key = theme?.themeKey || "";
  if (key === ORIGINAL_BG_THEME_KEY) {
    return { type: "original" };
  }
  if (isCustomPhotoBgTheme(theme)) {
    return { type: "custom", customUrl: getStoredCustomBgVideoUrl() };
  }
  return { type: "theme", theme: theme ?? null };
}

export interface BuildSceneInput {
  userId: string;
  contentId: string;
  petCutoutUrl: string;
  placement: ScenePlacement;
  floorY: number;
  background: SceneBackgroundChoice;
  previewFrameHeight?: number;
  /**
   * 고객이 배경을 명시적으로 골랐는가. 기본 true.
   *
   * false 는 **레거시 흐름 전용**이다 — 배경 선택 단계 자체가 없던 경로에서만
   * 단색 폴백이 허용된다. 고른 배경으로 만들지 못하면 돈을 쓰지 않는다.
   */
  requireBackground?: boolean;
  /** 테스트 주입. 기본은 실제 업로더. */
  upload?: (p: {
    userId: string;
    contentId: string;
    sceneId: string;
    dataUrl: string;
  }) => Promise<string>;
  /** 테스트 주입. 기본은 캔버스 합성. */
  compose?: typeof composeSceneImage;
}

/**
 * 정본 장면을 만들어 저장하고 돌려준다.
 *
 * ── 원본 배경은 왜 합성하지 않는가 ─────────────────────────────────────────
 * 원본 사진에는 **펫이 이미 원래 자리에, 원래 크기로** 들어 있다. 그것이 가장
 * 정확한 "원래 장면"이고, 누끼를 다시 얹으면 오히려 원본과 미세하게 어긋난다
 * (누끼 경계, 그림자 손실). 요구사항이 말한 "고객이 위치를 다시 만들 필요가
 * 없어야 한다"는 이 경로에서 **자동으로** 충족된다 — 재배치 자체가 없다.
 *
 * 그래서 원본 경로의 장면 이미지는 원본 사진 그 자체다.
 */
export async function buildCanonicalScene(
  input: BuildSceneInput
): Promise<CanonicalScene> {
  const compose = input.compose ?? composeSceneImage;
  const upload = input.upload ?? uploadScene;

  const { url: backgroundUrl, backgroundId } = resolveBackgroundSource(
    input.background
  );

  const isOriginal = input.background.type === "original";
  // 원본은 배치가 곧 원본 그대로다 — 배치 값을 중립으로 기록해야 이후 화면이
  // 원본 위에 펫을 한 번 더 얹지 않는다.
  const placement: ScenePlacement = isOriginal
    ? { scale: 1, posX: 0, posY: 0, shiftPct: 0 }
    : input.placement;

  const sceneId = deriveSceneId({
    contentId: input.contentId,
    backgroundType: input.background.type,
    backgroundId,
    petScale: placement.scale,
    petX: placement.posX,
    petY: placement.posY,
  });

  const required = input.requireBackground !== false;

  let dataUrl: string;
  if (isOriginal) {
    // 원본 사진이 곧 장면이다. 다시 그리지 않는다 — 펫이 이미 원래 자리에 있다.
    if (!backgroundUrl) {
      throw new SceneError(
        "ORIGINAL_PHOTO_MISSING",
        "원본 사진을 찾을 수 없습니다."
      );
    }
    try {
      dataUrl = backgroundUrl.startsWith("data:")
        ? backgroundUrl
        : await blobToDataUrl(await (await fetch(backgroundUrl)).blob());
    } catch {
      throw new SceneError(
        "BACKGROUND_LOAD_FAILED",
        "원본 사진을 불러오지 못했습니다."
      );
    }
  } else {
    const blob = await compose({
      backgroundUrl,
      petCutoutUrl: input.petCutoutUrl,
      placement,
      previewFrameHeight: input.previewFrameHeight,
      requireBackground: required,
    });
    dataUrl = await blobToDataUrl(blob);
  }

  const sceneKeyframeUrl = await upload({
    userId: input.userId,
    contentId: input.contentId,
    sceneId,
    dataUrl,
  });

  const scene: CanonicalScene = {
    sceneId,
    contentId: input.contentId,
    backgroundType: input.background.type,
    backgroundId,
    petScale: placement.scale,
    petX: placement.posX,
    petY: placement.posY,
    floorY: input.floorY,
    shiftPct: placement.shiftPct,
    sceneKeyframeUrl,
    backgroundBaked: true,
  };
  saveCanonicalScene(scene);
  return scene;
}
