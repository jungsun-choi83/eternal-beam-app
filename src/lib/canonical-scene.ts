/**
 * 정본 장면(canonical scene) — **생성이 보는 단 하나의 그림.**
 *
 * ── 무엇이 바뀌었나 ─────────────────────────────────────────────────────────
 * 예전에는 배경이 **프론트 전용 표시 레이어**였다. 생성기는 누끼만 받았고,
 * 백엔드는 그것을 검정/흰색 단색 판에 눌러 붙여(luma_keyframe) 프로바이더에
 * 보냈다. 배경은 생성이 끝난 **뒤에** 영상 위에 다시 합성됐다(compose-video).
 *
 * 그래서 고객이 미리보기에서 승인한 그림과 실제로 생성된 영상이 같다는 보장이
 * 어디에도 없었다 — 조명도, 그림자도, 접지도 프로바이더는 본 적이 없다.
 *
 * 이제는 **승인된 그 장면 자체**를 프로바이더에 보낸다. 이 파일은 그 장면을
 * 식별하는 최소한의 사실만 정의한다.
 *
 * ── 왜 한 벌인가 ────────────────────────────────────────────────────────────
 * BREATHING·BLINKING·EAR_TWITCH·HEAD_TILT·TAIL_WAG·COME_CLOSER 는 전부 **같은
 * 장면**에서 출발하고 동작 프롬프트만 다르다. 행동마다 배경 처리를 따로 만들면
 * 여섯 벌이 서로 조금씩 어긋나고, 그 어긋남은 한 아이의 영상들 사이에서
 * 배경이 미묘하게 달라지는 형태로 나타난다.
 */

export type BackgroundType = "original" | "theme" | "custom";

export const BACKGROUND_TYPES: readonly BackgroundType[] = [
  "original",
  "theme",
  "custom",
];

export function isBackgroundType(v: unknown): v is BackgroundType {
  return v === "original" || v === "theme" || v === "custom";
}

export interface CanonicalScene {
  /** 이 장면의 식별자. 같은 장면이면 같은 값 — 생성 재사용의 키다. */
  sceneId: string;
  /** 이 장면이 속한 펫 콘텐츠. */
  contentId: string;
  backgroundType: BackgroundType;
  /**
   * 배경의 출처 식별자.
   *   theme    → themeKey ('fresh_forest')
   *   custom   → 커스텀 배경 자산 URL/ID
   *   original → 'original' (원본 사진 그 자체)
   */
  backgroundId: string;
  /** 승인 시점의 펫 배치. 미리보기와 **같은 수식**으로 쓰였다. */
  petScale: number;
  petX: number;
  petY: number;
  /** 테마 접지선(0=맨 위, 1=맨 아래). */
  floorY: number;
  /**
   * 접지 보정량(%). 미리보기가 실제로 적용한 값을 그대로 들고 온다 —
   * 여기서 다시 계산하면 미리보기와 1px 어긋날 수 있고, 그 1px 이 발이 땅에
   * 닿았는지를 가른다.
   */
  shiftPct: number;
  /** 합성된 장면 이미지의 주소. 프로바이더가 보는 바로 그 그림. */
  sceneKeyframeUrl: string;
  /** Independent, pet-free background used only by optional layered V2. */
  layeredBackgroundType?: "image" | "video" | null;
  layeredBackgroundUrl?: string | null;
  /**
   * 이 장면으로 만든 영상은 **배경이 이미 구워져 있다.**
   *
   * 항상 true 다 — 이 타입은 새 경로 전용이다. 레거시(투명/보이드) 자산은
   * 이 레코드 자체가 없고, 그 부재가 곧 `background_baked=false` 다.
   * 값을 갖고 다니는 이유는 저장·전송에서 명시적이어야 하기 때문이다.
   */
  backgroundBaked: true;
}

const SCENE_KEY = "eternal_beam_canonical_scene_v1";

/**
 * 장면 id — (contentId, 배경종류, 배경id, 배치) 에서 **결정적으로** 만든다.
 *
 * 무작위를 쓰면 같은 장면을 두 번 승인했을 때 서로 다른 id 가 나오고, 그러면
 * 생성 재사용 키가 갈라져 **같은 그림에 두 번 과금**된다. 배치까지 넣는 이유는
 * 위치를 바꿔 다시 승인하면 그것은 실제로 다른 장면이기 때문이다.
 */
export function deriveSceneId(input: {
  contentId: string;
  backgroundType: BackgroundType;
  backgroundId: string;
  petScale: number;
  petX: number;
  petY: number;
}): string {
  const round = (n: number) => Math.round(n * 1000) / 1000;
  const raw = [
    input.contentId.trim(),
    input.backgroundType,
    input.backgroundId.trim(),
    round(input.petScale),
    Math.round(input.petX),
    Math.round(input.petY),
  ].join("|");

  // 짧고 안정적인 해시. 암호학적 강도가 필요 없다 — 충돌을 피하고 결정적이면 된다.
  let h1 = 0x811c9dc5;
  let h2 = 0x01000193;
  for (let i = 0; i < raw.length; i++) {
    const c = raw.charCodeAt(i);
    h1 = Math.imul(h1 ^ c, 0x01000193) >>> 0;
    h2 = Math.imul(h2 + c + i, 0x85ebca6b) >>> 0;
  }
  return `scene_${h1.toString(36)}${h2.toString(36)}`;
}

export function readCanonicalScene(): CanonicalScene | null {
  try {
    const raw = localStorage.getItem(SCENE_KEY);
    if (!raw) return null;
    const s = JSON.parse(raw) as Partial<CanonicalScene>;
    if (!s.sceneId || !s.contentId || !s.sceneKeyframeUrl) return null;
    if (!isBackgroundType(s.backgroundType)) return null;
    return {
      sceneId: s.sceneId,
      contentId: s.contentId,
      backgroundType: s.backgroundType,
      backgroundId: String(s.backgroundId ?? ""),
      petScale: Number(s.petScale) || 1,
      petX: Number(s.petX) || 0,
      petY: Number(s.petY) || 0,
      floorY: Number(s.floorY) || 0.86,
      shiftPct: Number(s.shiftPct) || 0,
      sceneKeyframeUrl: s.sceneKeyframeUrl,
      layeredBackgroundType:
        s.layeredBackgroundType === "image" || s.layeredBackgroundType === "video"
          ? s.layeredBackgroundType
          : null,
      layeredBackgroundUrl:
        typeof s.layeredBackgroundUrl === "string" ? s.layeredBackgroundUrl : null,
      backgroundBaked: true,
    };
  } catch {
    return null;
  }
}

export function saveCanonicalScene(scene: CanonicalScene): void {
  try {
    localStorage.setItem(SCENE_KEY, JSON.stringify(scene));
  } catch {
    /* 용량 초과 — 생성은 장면을 인자로 직접 받으므로 이 저장은 편의다 */
  }
}

export function clearCanonicalScene(): void {
  try {
    localStorage.removeItem(SCENE_KEY);
  } catch {
    /* ignore */
  }
}

/**
 * 이 장면이 지금 콘텐츠의 것인가.
 *
 * 사진을 새로 올리면 content_id 가 바뀐다. 그때 옛 장면을 그대로 쓰면 **다른
 * 아이의 배경**으로 생성이 돌아간다 — pet-identity.ts 가 pet_id 에서 겪은 것과
 * 정확히 같은 오염이다.
 */
export function sceneMatchesContent(
  scene: CanonicalScene | null,
  contentId: string | null | undefined
): boolean {
  const cid = (contentId || "").trim();
  if (!scene || !cid) return false;
  return scene.contentId === cid;
}

/** 현재 콘텐츠에 묶인 장면만 돌려준다. 어긋나면 null. */
export function readSceneForContent(
  contentId: string | null | undefined
): CanonicalScene | null {
  const scene = readCanonicalScene();
  return sceneMatchesContent(scene, contentId) ? scene : null;
}

/** 백엔드로 보낼 폼 필드 — 이름은 서버 스키마와 1:1 이다. */
export function sceneFormFields(scene: CanonicalScene): Record<string, string> {
  const base = {
    scene_id: scene.sceneId,
    background_type: scene.backgroundType,
    background_id: scene.backgroundId,
    pet_scale: String(scene.petScale),
    pet_x: String(scene.petX),
    pet_y: String(scene.petY),
    scene_keyframe_url: scene.sceneKeyframeUrl,
    background_baked: "true",
  };
  if (scene.layeredBackgroundType && scene.layeredBackgroundUrl) {
    return {
      ...base,
      layered_background_type: scene.layeredBackgroundType,
      layered_background_url: scene.layeredBackgroundUrl,
    };
  }
  return base;
}
