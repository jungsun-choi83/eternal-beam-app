/**
 * 장면 준비 실패 — **생성 실패와 구분한다.**
 *
 * ── 왜 구분이 중요한가 ──────────────────────────────────────────────────────
 * 두 실패는 사용자에게 요구하는 행동이 정반대다.
 *
 *   생성 실패    프로바이더가 이미 돈을 받고 실패했다. "다시 시도"는 **또 과금**이다.
 *   장면 실패    아직 아무것도 제출하지 않았다. 배경을 다시 고르면 공짜로 복구된다.
 *
 * 예전에는 장면 준비가 실패하면 조용히 검정 판으로 떨어져 그대로 생성했다.
 * 고객은 자기가 고른 숲 대신 검정 배경의 영상을 받았고, **돈은 나갔다.**
 * 이제는 멈춘다 — 배경을 골랐는데 그 배경으로 만들 수 없다면, 만들지 않는 것이 맞다.
 */

export type SceneErrorCode =
  /** 장면 합성 자체가 실패했다 (캔버스·인코딩). */
  | "SCENE_PREPARATION_FAILED"
  /** 배경 이미지를 불러오지 못했다 (404·네트워크·디코드). */
  | "BACKGROUND_LOAD_FAILED"
  /** 커스텀 배경이 CORS 로 막혀 캔버스가 오염됐다. */
  | "CUSTOM_BACKGROUND_CORS_FAILED"
  /** 장면을 저장하지 못했다 (업로드). */
  | "SCENE_UPLOAD_FAILED"
  /** 원본 사진을 찾지 못했다 (원본 배경 선택 시). */
  | "ORIGINAL_PHOTO_MISSING";

export class SceneError extends Error {
  readonly code: SceneErrorCode;
  /**
   * 고객이 **무언가 해서** 벗어날 수 있는가.
   *
   * 전부 true 다 — 장면 실패는 정의상 제출 전이고, 배경을 다시 고르거나 다시
   * 시도하면 된다. 필드를 남겨 두는 이유는 화면이 이 값을 보고 "다시 시도"
   * 버튼을 그릴지 정하기 때문이고, 나중에 복구 불가 사유가 생기면 여기서 갈린다.
   */
  readonly recoverable: boolean;

  constructor(code: SceneErrorCode, message: string, recoverable = true) {
    super(message);
    this.name = "SceneError";
    this.code = code;
    this.recoverable = recoverable;
  }
}

export function isSceneError(e: unknown): e is SceneError {
  return e instanceof SceneError;
}

/** 캔버스 오염(CORS)인가 — 브라우저는 SecurityError 로 알린다. */
export function looksLikeCanvasTaint(e: unknown): boolean {
  const name = (e as { name?: string })?.name || "";
  const msg = String((e as { message?: string })?.message || "");
  return (
    name === "SecurityError" ||
    /tainted|cross-origin|crossorigin/i.test(msg)
  );
}

/**
 * 고객에게 보여 줄 한 줄.
 *
 * **"생성 실패"라고 말하지 않는다.** 그렇게 말하면 고객은 다시 생성하려 하고,
 * 그 재시도가 유료 제출이 된다. 여기서 유도해야 하는 행동은 "배경을 다시".
 */
export function sceneErrorMessage(code: SceneErrorCode, lang = "ko"): string {
  const ko: Record<SceneErrorCode, string> = {
    SCENE_PREPARATION_FAILED:
      "장면을 준비하지 못했습니다. 배경을 다시 선택해 주세요.",
    BACKGROUND_LOAD_FAILED:
      "배경을 불러오지 못했습니다. 배경을 다시 선택해 주세요.",
    CUSTOM_BACKGROUND_CORS_FAILED:
      "이 배경 이미지를 사용할 수 없습니다. 다른 배경을 선택해 주세요.",
    SCENE_UPLOAD_FAILED:
      "장면을 저장하지 못했습니다. 잠시 후 다시 시도해 주세요.",
    ORIGINAL_PHOTO_MISSING:
      "원본 사진을 찾을 수 없습니다. 사진을 다시 올리거나 다른 배경을 선택해 주세요.",
  };
  const en: Record<SceneErrorCode, string> = {
    SCENE_PREPARATION_FAILED:
      "We couldn't prepare this scene. Please try the background again.",
    BACKGROUND_LOAD_FAILED:
      "We couldn't load that background. Please choose it again.",
    CUSTOM_BACKGROUND_CORS_FAILED:
      "That background image can't be used. Please choose another background.",
    SCENE_UPLOAD_FAILED: "We couldn't save the scene. Please try again shortly.",
    ORIGINAL_PHOTO_MISSING:
      "We couldn't find the original photo. Re-upload it or pick another background.",
  };
  return (lang === "en" ? en : ko)[code];
}

/** 서버가 돌려주는 생성 관련 코드 중 **제출 전** 실패들. */
export const PRE_SUBMISSION_SERVER_CODES = [
  "GENERATION_IDEMPOTENCY_UNAVAILABLE",
  "GENERATION_IN_PROGRESS",
  /**
   * 서버가 장면을 준비하지 못했다 (Phase 26).
   *
   * 예전에는 이 상황에서 서버가 조용히 레거시 단색 판으로 떨어져 **생성에
   * 성공**했다. 응답은 200 이고 background_baked 만 false 였다 — 화면은 그
   * false 를 기록만 하고 아무 말도 하지 않았으므로, 고객은 자기가 고른 적 없는
   * 배경의 영상을 받고서야 무언가 잘못됐음을 알았다.
   */
  "SCENE_UNAVAILABLE",
] as const;

export function isPreSubmissionServerCode(code: string): boolean {
  return (PRE_SUBMISSION_SERVER_CODES as readonly string[]).includes(code);
}

/**
 * 서버 코드 → 고객 문구.
 *
 * 이 둘도 **과금되지 않았다.** 그러니 "생성 실패"가 아니라 "잠시 후 다시"로
 * 안내해야 한다.
 */
export function serverGenerationMessage(code: string, lang = "ko"): string | null {
  if (code === "GENERATION_IDEMPOTENCY_UNAVAILABLE") {
    return lang === "en"
      ? "We couldn't start generation safely. Nothing was charged — please try again shortly."
      : "안전하게 생성을 시작하지 못했습니다. 과금되지 않았으니 잠시 후 다시 시도해 주세요.";
  }
  if (code === "SCENE_UNAVAILABLE") {
    // "생성 실패"라고 하지 않는다 — 제출 전이라 돈이 나가지 않았고, 다시
    // 시도하면 그대로 복구된다.
    return lang === "en"
      ? "We couldn't prepare the background you chose. Nothing was charged — please try again shortly."
      : "선택한 배경으로 장면을 준비하지 못했습니다. 과금되지 않았으니 잠시 후 다시 시도해 주세요.";
  }
  if (code === "GENERATION_IN_PROGRESS") {
    return lang === "en"
      ? "This scene is already being generated. Please wait a moment."
      : "이 장면은 이미 생성 중입니다. 잠시만 기다려 주세요.";
  }
  return null;
}
