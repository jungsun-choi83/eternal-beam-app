/**
 * 배경이 구워진 자산인가 — **재생 쪽의 단 하나의 판정.**
 *
 * ── 왜 이 판정이 필요한가 ───────────────────────────────────────────────────
 * 두 세대의 자산이 동시에 존재한다.
 *
 *   레거시   투명/보이드 배경으로 생성됐다. 재생할 때 배경을 **덧붙여야** 완성된다
 *            (IdleLoopVideo 의 블랙키 제거 + 테마 배경 레이어, compose-video).
 *   구움     승인된 장면에서 생성됐다. 배경이 이미 화면 안에 있다.
 *
 * 구운 자산에 레거시 처리를 걸면 **배경이 두 번 적용된다** — 검정 키를 뽑아내려다
 * 실제 장면의 어두운 픽셀(그림자·나무 그늘)이 뚫리고, 그 구멍으로 테마 배경이
 * 비쳐 보인다. 반대로 레거시 자산에 구움 처리를 하면 검은 사각형이 그대로 남는다.
 *
 * 그래서 판정을 한 곳에 둔다. 화면마다 각자 추측하면 언젠가 한 화면만 틀린다.
 */

export interface BakedAssetLike {
  /** 생성 응답/저장 레코드가 실어 주는 값. 없으면 레거시다. */
  background_baked?: boolean | null;
  backgroundBaked?: boolean | null;
}

/**
 * 이 자산은 배경이 구워져 있는가.
 *
 * **없으면 false** 다 — 레거시가 기본값이어야 한다. 기존 자산에는 이 필드가
 * 아예 없고, 그것들이 지금까지처럼 계속 동작하는 것이 최우선이다.
 */
export function isBackgroundBaked(
  asset: BakedAssetLike | null | undefined
): boolean {
  if (!asset) return false;
  return asset.background_baked === true || asset.backgroundBaked === true;
}

/**
 * 재생 시 투명 합성(블랙키 제거)을 해야 하는가.
 *
 * 구운 자산은 **하면 안 된다.** 그것이 "배경을 두 번 적용하지 않는다"의 실제 구현이다.
 */
export function shouldTransparentComposite(
  asset: BakedAssetLike | null | undefined
): boolean {
  return !isBackgroundBaked(asset);
}

/**
 * 재생 시 테마 배경 레이어를 뒤에 깔아야 하는가.
 *
 * 구운 자산은 자기 배경을 갖고 있으므로 깔지 않는다. 깔면 두 배경이 겹치고,
 * 키잉이 완벽하지 않은 가장자리에서 두 장면이 동시에 보인다.
 */
export function shouldRenderThemeBackdrop(
  asset: BakedAssetLike | null | undefined
): boolean {
  return !isBackgroundBaked(asset);
}

/**
 * 생성 후 compose-video(배경 합성 후처리)를 돌려야 하는가.
 *
 * 레거시 전용이다. 구운 자산에 돌리면 승인된 장면 위에 테마 영상이 한 번 더
 * 얹혀 완전히 다른 그림이 나온다.
 */
export function shouldRunComposeVideo(
  asset: BakedAssetLike | null | undefined
): boolean {
  return !isBackgroundBaked(asset);
}
