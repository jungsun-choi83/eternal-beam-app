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
// 값 import 라 상대 경로 — `@/` 별칭은 Vite 만 푼다(node --test 는 못 푼다).
import { ORIGINAL_PHOTO_THEME_KEY } from "../components/memorial/themes.ts";
import { readMainPhoto } from "./main-media-store.ts";

/**
 * 저장된 원본 사진. **키 정의는 main-media-store.ts 한 곳에만 있다.**
 *
 * 예전에는 이 파일이 키 문자열을 따로 들고 있었다. 쓰는 쪽(업로드 경로)과
 * 읽는 쪽(여기)이 각자 문자열을 적어 두면, 한쪽만 바뀌는 날 원본 배경이
 * 조용히 빈다.
 *
 * ⚠️ 화면이 **지금 들고 있는** 업로드 이미지가 있으면 그쪽이 우선이다. 그
 * 우선순위는 resolveOriginalPhoto 가 쥐고 있고, 화면은 그 결과를 장면에
 * 직접 넘긴다(SceneBackgroundChoice.originalUrl).
 */
export const readOriginalPhoto = readMainPhoto;

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
  /**
   * ORIGINAL 이면 **화면이 실제로 보여 준 그 사진.**
   *
   * 없으면 저장된 값으로 떨어진다. 주입을 받는 이유는 하나다 — 화면이 보여 준
   * 그림과 생성에 들어가는 그림이 같아야 하는데, 저장이 늦거나 실패하면 둘이
   * 갈라지기 때문이다. 실제로 그 갈라짐이 이번에 고친 결함이다.
   */
  originalUrl?: string | null;
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
    // 주입된 값이 정본이다. 화면이 그린 그림과 같아야 하므로 여기서 다시
    // localStorage 를 읽지 않는다 — 읽으면 두 그림이 갈라질 수 있다.
    const injected = (choice.originalUrl || "").trim();
    return { url: injected || readOriginalPhoto(), backgroundId: "original" };
  }
  if (choice.type === "custom") {
    const u = (choice.customUrl || "").trim();
    return { url: u || null, backgroundId: u || "custom" };
  }
  const theme = choice.theme || null;
  return {
    // 영상이 있으면 영상, 없으면 **썸네일**이다 (Phase 28).
    //
    // ── 왜 폴백이 필요한가 ────────────────────────────────────────────────
    // 12개 테마 중 배경 영상을 가진 것은 셋뿐이다(fresh_forest·beach·
    // snow_forest). 나머지 여섯(celestial·golden_meadow·starlight·aurora·
    // sunset·ocean_deep)은 bgVideo 가 없어서 여기서 null 이 나왔고, 그러면
    // composeSceneImage 가 BACKGROUND_LOAD_FAILED 로 던져 **생성 자체가
    // 거절**됐다.
    //
    // 그런데 미리보기는 그 여섯 테마에도 배경을 보여 준다 — 정확히 이
    // thumb 을 bg-cover 로 깐다(preview-screen.tsx, memorial-device-play-
    // screen.tsx). 즉 고객은 배경이 있는 그림을 보고 승인한 뒤 "배경을
    // 불러오지 못했습니다"를 받았다.
    //
    // 그래서 같은 이미지로 합성한다. 승인한 그림과 만들어지는 그림이 같아야
    // 한다는 규칙이 이 폴백의 근거이고, 화질은 그 thumb 해상도가 상한이다.
    url: getEffectiveBgVideo(theme) || theme?.thumb?.trim() || null,
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
  theme: MemorialTheme | null | undefined,
  /** 화면이 실제로 보여 준 원본 사진. 원본 갈래에서만 쓰인다. */
  originalUrl?: string | null
): SceneBackgroundChoice {
  const key = theme?.themeKey || "";
  if (key === ORIGINAL_BG_THEME_KEY) {
    return { type: "original", originalUrl: (originalUrl || "").trim() || null };
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
  // Original photos already contain the pet and must remain V1.  Other
  // independently addressable backgrounds can be copied into immutable V2
  // storage after V1 succeeds. data:/blob: values are browser-local and cannot
  // be safely fetched by the backend.
  let layeredBackgroundType: "image" | "video" | null = null;
  let layeredBackgroundUrl: string | null = null;
  if (!isOriginal && backgroundUrl && !/^(data|blob):/i.test(backgroundUrl)) {
    layeredBackgroundType = /\.(mp4|webm|mov)(?:[?#]|$)/i.test(backgroundUrl)
      ? "video"
      : "image";
    try {
      const origin = typeof window !== "undefined" ? window.location.origin : "http://localhost";
      layeredBackgroundUrl = new URL(backgroundUrl, origin).toString();
    } catch {
      layeredBackgroundType = null;
    }
  }
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
    layeredBackgroundType,
    layeredBackgroundUrl,
    backgroundBaked: true,
  };
  saveCanonicalScene(scene);
  return scene;
}
