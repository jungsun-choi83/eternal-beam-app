"use client";

import { useEffect, useState } from "react";

import { DEFAULT_FEET_BOTTOM_MARGIN } from "@/components/memorial/idle-loop-video";
import { getThemeFloorY, type MemorialTheme } from "@/components/memorial/themes";
import { computeSubjectShiftPct } from "@/lib/pet-grounding";

export interface PetGrounding {
  /** 테마 접지선 (0~1). 접지 그림자 위치에도 같은 값을 쓴다. */
  floorY: number;
  feetMargin: number;
  /** IdleLoopVideo 의 onFeetMarginChange 에 그대로 넘긴다. */
  setFeetMargin: (margin: number) => void;
  /** 피사체 레이어에 적용할 세로 보정(프레임 높이 대비 %). */
  subjectShiftPct: number;
}

/**
 * 테마 접지선 + 클립 실측 발 여백 → 피사체 세로 보정.
 *
 * 조정 화면(preview-screen)과 최종 재생 화면(memorial-device-play-screen)이
 * **이 훅 하나**를 공유한다. 예전에는 조정 화면에만 계산이 있어서 최종 재생에서
 * 펫이 테마 한가운데에 떠 있었다.
 *
 * @param theme    현재 테마. floorY 를 여기서 읽는다.
 * @param idleSrc  실제로 재생 중인 아이들 클립 URL. 클립이 바뀌면 이전 클립의
 *                 실측값이 남지 않도록 폴백으로 되돌린다 — 클립마다 하단 여백이
 *                 다르므로(Wan 0.175 · Luma 0.139) 남으면 발이 어긋난다.
 */
export function usePetGrounding(
  theme: MemorialTheme | null | undefined,
  idleSrc: string | null | undefined
): PetGrounding {
  const floorY = getThemeFloorY(theme);

  // 측정 전/실패 시에는 폴백 상수를 쓴다.
  const [feetMargin, setFeetMargin] = useState(DEFAULT_FEET_BOTTOM_MARGIN);
  useEffect(() => {
    setFeetMargin(DEFAULT_FEET_BOTTOM_MARGIN);
  }, [idleSrc]);

  return {
    floorY,
    feetMargin,
    setFeetMargin,
    subjectShiftPct: computeSubjectShiftPct({ floorY, feetMargin }),
  };
}
