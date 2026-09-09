/**
 * 미리보기 "확인" → idle 생성 요청 오케스트레이션.
 *
 * 컴포넌트에서 분리해 둔 이유는 두 가지다:
 *  1) 확인 1회당 생성이 정확히 1회인지 테스트로 못박기 위해.
 *  2) 백엔드로 나가는 인자가 **정본 장면 한 벌**로 고정되는지 테스트하기 위해.
 *
 * ── 계약이 뒤집혔다 (Phase 19) ──────────────────────────────────────────────
 * 예전 이 파일의 계약은 "테마 관련 키는 절대 넘기지 않는다"였다. 배경이 프론트
 * 전용 표시 레이어였기 때문이고, 그래서 생성기는 누끼만 보고 단색 판 위에서
 * 영상을 만들었다 — 고객이 승인한 그림을 프로바이더는 본 적이 없었다.
 *
 * 이제는 반대다. **승인된 장면(scene_keyframe)이 생성 입력의 정본**이고,
 * 개별 테마 id/키를 흩뿌리는 대신 장면 레코드 하나를 통째로 넘긴다.
 * 배경 종류(original/theme/custom)는 그 레코드 안에 들어 있으므로 호출부가
 * 배경마다 분기하지 않는다.
 *
 * 장면이 없으면 예전 동작 그대로다(누끼만 전송 → 백엔드 단색 판).
 */

// 값 import 라 상대 경로를 쓴다 — `@/` 별칭은 Vite 만 풀고 node --test 는 못 푼다.
import { sceneFormFields, type CanonicalScene } from "./canonical-scene.ts";

/** generatePetVideo 로 보낼 수 있는 옵션. */
export interface IdleGenerationOptions {
  userId?: string;
  contentId?: string;
  skipPreprocessing?: boolean;
  idleOnly?: boolean;
  /**
   * 정본 장면. 있으면 프로바이더는 이 그림에서 출발하고, 생성된 영상은 배경이
   * 구워진 채로 나온다. 없으면 레거시 경로(투명 누끼 + 단색 판).
   */
  scene?: Record<string, string>;
}

export type GeneratePetVideoFn = (
  file: File,
  options: IdleGenerationOptions
) => Promise<{
  content_id: string;
  dog_only_nobg_url: string;
  idle_video_url: string;
  action_video_url: string | null;
  [key: string]: unknown;
}>;

export interface IdleGenerationInput {
  cutFile: File;
  contentId: string;
  /**
   * 저장 경로에 쓰이는 신원. 넘기지 않으면 백엔드가 'anonymous' 로 저장해
   * 이후 COME_CLOSER 조회 신원과 영영 어긋난다 (videoProcessingApi 의
   * `user_id` 기본값).
   */
  userId?: string;
  /** 승인된 정본 장면. 없으면 레거시 경로. */
  scene?: CanonicalScene | null;
  /** 테스트에서 주입. 기본은 실제 API 클라이언트. */
  generate?: GeneratePetVideoFn;
}

/**
 * 확인 시점의 idle 생성 1회.
 *
 * 장면이 있으면 옵션에 장면 필드가 **한 벌로** 실린다 — 흩어진 테마 값이 아니라
 * scene_id/배경종류/배경id/배치/키프레임 주소가 함께 간다. 서버는 그 조합으로
 * 생성 레코드를 찾거나 만들고, 같은 장면 + 같은 행동이면 **다시 과금하지 않는다.**
 */
export async function requestIdleGeneration(input: IdleGenerationInput) {
  // 기본 구현은 지연 import 한다 — 테스트가 generate 를 주입하면 API 클라이언트를
  // (그리고 그 무거운 의존성 전체를) 아예 불러오지 않는다.
  const generate =
    input.generate ??
    ((await import("@/app/services/videoProcessingApi"))
      .generatePetVideo as unknown as GeneratePetVideoFn);
  const userId = input.userId?.trim();
  return generate(input.cutFile, {
    skipPreprocessing: true,
    contentId: input.contentId,
    idleOnly: true,
    // 값이 있을 때만 넣는다 — 빈 문자열을 보내면 백엔드 기본값('anonymous')을
    // 덮어써서 오히려 신원이 빈 채로 저장된다.
    ...(userId ? { userId } : {}),
    ...(input.scene ? { scene: sceneFormFields(input.scene) } : {}),
  });
}

/**
 * 이 호출이 **장면을 실었는가** (테스트·개발 가드용).
 *
 * 예전에는 반대 성질(optionsContainThemeData)을 검사했다. 배경이 프론트 전용이던
 * 시절의 가드였고, 지금 그것을 남겨 두면 새 계약과 정면으로 충돌한다.
 */
export function optionsCarryScene(options: IdleGenerationOptions): boolean {
  const s = options.scene;
  return Boolean(s && s.scene_keyframe_url && s.background_type);
}

/** 장면 필드가 기대한 이름으로 다 들어 있는가. */
export function sceneFieldsAreComplete(scene: Record<string, string>): boolean {
  return [
    "scene_id",
    "background_type",
    "background_id",
    "pet_scale",
    "pet_x",
    "pet_y",
    "scene_keyframe_url",
    "background_baked",
  ].every((k) => typeof scene[k] === "string" && scene[k].length > 0);
}
