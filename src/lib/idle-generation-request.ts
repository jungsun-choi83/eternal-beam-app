/**
 * 미리보기 "확인" → idle 생성 요청 오케스트레이션.
 *
 * 컴포넌트에서 분리해 둔 이유는 두 가지다:
 *  1) 확인 1회당 생성이 정확히 1회인지 테스트로 못박기 위해.
 *  2) 백엔드로 나가는 인자에 테마 정보가 절대 섞이지 않는지 테스트하기 위해
 *     (테마는 현재 프론트 전용이다).
 *
 * 백엔드 API 는 바뀌지 않는다 — 기존 generatePetVideo 를 그대로 호출한다.
 */

/** generatePetVideo 로 보낼 수 있는 옵션 — 테마 관련 키는 의도적으로 없다. */
export interface IdleGenerationOptions {
  userId?: string;
  contentId?: string;
  skipPreprocessing?: boolean;
  idleOnly?: boolean;
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
   * `user_id` 기본값). 테마와 달리 이건 생성 결과를 바꾸지 않는다 — 어디에
   * 저장할지만 정한다.
   */
  userId?: string;
  /** 테스트에서 주입. 기본은 실제 API 클라이언트. */
  generate?: GeneratePetVideoFn;
}

/**
 * 확인 시점의 idle 생성 1회.
 *
 * 옵션은 항상 아래 세 개뿐이다 — 테마 id/키는 넘기지 않는다.
 *   { skipPreprocessing: true, contentId, idleOnly: true }
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
  });
}

/** 이 호출에 테마 정보가 섞이지 않았는지 검사 (테스트·개발 가드용). */
export function optionsContainThemeData(options: Record<string, unknown>): boolean {
  return Object.keys(options).some((k) => /theme|background|bg_/i.test(k));
}
