/** Memorial 앱 테마 — Unity `light_rgb/snow_forest` 와 동일 소스 */
export interface MemorialTheme {
  id: number;
  name: string;
  nameKo: string;
  themeKey: string;
  gradient: string;
  accent: string;
  premium: boolean;
  price: string;
  thumb: string;
  /** public/ 기준 동영상 배경 (있으면 프리뷰에 재생) */
  bgVideo?: string;
  /**
   * true면 이 테마는 고정 에셋(bgVideo)이 없고, 사용자 사진마다 배경 인페인팅+Luma
   * 파이프라인(custom-background-store.ts)으로 생성된다 — 선택 시 바로 결제로 넘어가지
   * 않고 생성 화면(customBackground)을 먼저 거친다. 화면 쪽 분기는 이 플래그로 판단한다.
   */
  requiresGeneration?: boolean;
  /**
   * 이 배경에서 "땅"이 프레임 세로 어디쯤인지 (0=맨 위, 1=맨 아래).
   * 피사체의 발이 이 선에 닿도록 배치하고, 접지 그림자도 같은 선에 그린다.
   * 없으면 DEFAULT_FLOOR_Y. 프론트 전용 값이며 백엔드로 전달되지 않는다.
   */
  floorY?: number;
}

/**
 * 기본 접지선. 대부분의 테마 배경이 하단 15~20% 구간에 지면을 두고 있고,
 * 프레임 맨 아래(1.0)에 붙이면 피사체가 화면 밖으로 잘려 보인다.
 */
export const DEFAULT_FLOOR_Y = 0.86;

/** 테마의 접지선 (없으면 기본값). 0.5~1.0 범위로 클램프. */
export function getThemeFloorY(theme: MemorialTheme | null | undefined): number {
  const v = theme?.floorY;
  if (typeof v !== "number" || Number.isNaN(v)) return DEFAULT_FLOOR_Y;
  return Math.min(1, Math.max(0.5, v));
}

/** "내 사진으로 나만의 배경 만들기" 테마의 고정 id/key — 여러 파일에서 참조하므로 상수로 노출. */
export const CUSTOM_PHOTO_BG_THEME_ID = 9;
export const CUSTOM_PHOTO_BG_THEME_KEY = "custom_photo_bg";

export const memorialThemes: MemorialTheme[] = [
  {
    id: 8,
    name: "Forest",
    nameKo: "숲속",
    themeKey: "fresh_forest",
    gradient: "from-emerald-950 via-green-900 to-black",
    accent: "#34d399",
    premium: false,
    price: "",
    thumb: "/theme-thumbs/fresh_forest.jpg",
    bgVideo: "/demo/forest.mp4",
    // 숲 바닥이 하단 ~12%에 또렷하게 깔린다(영상 배경).
    floorY: 0.88,
  },
  {
    id: 9, 
    name: "Beach",
    nameKo: "해변",
    themeKey: "beach",
    gradient: "from-sky-500 via-cyan-700 to-blue-950",
    accent: "#67e8f9",
    premium: false,
    price: "",
  
    thumb: "/theme-thumbs/beach.jpg",
    bgVideo: "/backgrounds/snow_forest/beach.mp4",
  
    // Adjust after checking where the visible sand/ground begins.
    floorY: 0.88,
  },
  {
    id: 1,
    name: "Snow Forest",
    nameKo: "눈 숲",
    themeKey: "snow_forest",
    gradient: "from-slate-800 via-blue-950 to-black",
    accent: "#93c5fd",
    premium: false,
    price: "",
    // winter_forest_path (EternalBeam/Assets/Backgrounds) 를 웹용으로 트랜스코딩한 것.
    // 예전 snow_forest.jpg 는 눈이 전혀 없는 여름 숲이었고 celestial.jpg 와 바이트 동일했다.
    thumb: "/theme-thumbs/snow_forest_winter.jpg",
    bgVideo: "/backgrounds/snow_forest/winter_forest_path.mp4",
    // 서리 낀 나무터널 + 앞쪽으로 이어지는 길. 근경 노면이 ~90%.
    floorY: 0.90,
  },
  {
    id: 2,
    name: "Celestial",
    nameKo: "천상",
    themeKey: "celestial",
    gradient: "from-indigo-900 via-purple-900 to-black",
    accent: "#8b5cf6",
    premium: false,
    price: "",
    // ⚠ UNRESOLVED: 이 리포에 "천상(celestial)"에 맞는 에셋이 없다.
    // 예전에는 celestial.jpg(= snow_forest.jpg 와 바이트 동일한 숲 사진)를 써서
    // 두 테마가 같은 그림을 보여줬다. 진짜 에셋을 구할 때까지는 테마 자체의
    // gradient 색으로 만든 임시 자리표시자를 쓴다 — 사진인 척하지 않는다.
    // 진짜 배경으로 교체하면 floorY 를 새로 측정해야 한다(현재는 지면이 없어 기본값).
    thumb: "/theme-thumbs/celestial_placeholder.jpg",
  },
  {
    id: 3,
    name: "Golden Meadow",
    nameKo: "황금 들판",
    themeKey: "golden_meadow",
    gradient: "from-amber-900 via-yellow-900 to-black",
    accent: "#f59e0b",
    premium: false,
    price: "",
    thumb: "/theme-thumbs/golden_meadow.jpg",
    // 포장된 산책로가 하단에 뚜렷하다.
    floorY: 0.90,
  },
  {
    id: 4,
    name: "Starlight",
    nameKo: "별빛",
    themeKey: "starlight",
    gradient: "from-slate-900 via-zinc-800 to-black",
    accent: "#e4e4e7",
    premium: false,
    price: "",
    thumb: "/theme-thumbs/starlight.jpg",
  },
  {
    id: 5,
    name: "Aurora",
    nameKo: "오로라",
    themeKey: "aurora",
    gradient: "from-emerald-900 via-teal-900 to-black",
    accent: "#10b981",
    premium: true,
    price: "$2.99",
    thumb: "/theme-thumbs/aurora.jpg",
    // ⚠ aurora.jpg 는 실내 크리스마스 사진이다(이름과 불일치). 나무 마루가 매우 명확.
    floorY: 0.90,
  },
  {
    id: 6,
    name: "Sunset",
    nameKo: "일몰",
    themeKey: "sunset",
    gradient: "from-rose-900 via-orange-900 to-black",
    accent: "#f43f5e",
    premium: true,
    price: "$2.99",
    thumb: "/theme-thumbs/sunset.jpg",
  },
  {
    id: 7,
    name: "Ocean Deep",
    nameKo: "깊은 바다",
    themeKey: "ocean_deep",
    gradient: "from-blue-900 via-cyan-900 to-black",
    accent: "#06b6d4",
    premium: true,
    price: "$2.99",
    thumb: "/theme-thumbs/ocean_deep.jpg",
    // 파도선 아래 젖은 모래사장이 뚜렷하다.
    floorY: 0.93,
  },
  {
    id: CUSTOM_PHOTO_BG_THEME_ID,
    name: "My Photo, Animated",
    nameKo: "내 사진으로 나만의 배경",
    themeKey: CUSTOM_PHOTO_BG_THEME_KEY,
    gradient: "from-fuchsia-950 via-purple-900 to-black",
    accent: "#c084fc",
    premium: true,
    price: "$2.99",
    // 생성 전에는 고정 배경이 없다. 예전 경로(custom_photo_bg.jpg)는 파일 자체가
    // 없어서 카드가 깨진 이미지로 보였다 — 테마 gradient 자리표시자로 대체.
    thumb: "/theme-thumbs/custom_photo_bg_placeholder.jpg",
    // bgVideo 없음 — 고정 에셋이 아니라 사용자별로 생성됨(custom-background-store.ts 참고).
    requiresGeneration: true,
  },
];

export function getMemorialTheme(id: number | null): MemorialTheme | undefined {
  if (id == null) return undefined;
  return memorialThemes.find((t) => t.id === id);
}

/**
 * 앱 전체의 **기본 테마** 단일 출처.
 *
 * 왜 fresh_forest(id 8)인가 — 배열 첫 번째라서가 아니라 실제 동작이 그렇다:
 *   * python/pi_sse_server.py 의 /demo/play 가 theme_id 기본값으로 "fresh_forest"
 *     를 세 곳에서 쓴다 (123, 132, 221행)
 *   * pi-sensor-bridge.triggerForestMachineDemo() → triggerThemeOnDevice("fresh_forest")
 *   * 기기 데모(forest-demo-config)의 FOREST_THEME_ID 가 8
 *   * freeMemorialThemes[0] 도 fresh_forest
 * 반면 id 1(snow_forest)은 과거 번호 체계의 잔재이고, themes.ts 주석대로 한동안
 * mp4 가 없어 목록에서 제외되기까지 했다. 그런데도 preview-screen 과
 * credit-pipeline 은 snow_forest 로 폴백해 두 기본값이 서로 달랐다.
 *
 * ⚠️ 이 값은 **폴백 전용**이다. 사용자가 명시적으로 고른 테마가 항상 이긴다.
 */
export const DEFAULT_THEME_ID = 8;

/** DEFAULT_THEME_ID 의 themeKey (백엔드 place_id / 기기 theme_id 로 나가는 값). */
export const DEFAULT_THEME_KEY =
  memorialThemes.find((t) => t.id === DEFAULT_THEME_ID)?.themeKey ?? "fresh_forest";

export const freeMemorialThemes = memorialThemes.filter((t) => !t.premium);
export const premiumMemorialThemes = memorialThemes.filter((t) => t.premium);

export function isPremiumTheme(themeId: number | null | undefined): boolean {
  return getMemorialTheme(themeId ?? null)?.premium ?? false;
}

/**
 * 프리뷰에 재생할 고정 배경 영상.
 *
 * 예전에는 snow_forest 를 무조건 제외했다 — 그 테마의 mp4 가 public 에 없었기 때문이다.
 * 이제 winter_forest_path.mp4 를 웹용으로 넣었으므로 그 예외는 필요 없다.
 */
export function getThemePreviewBgVideo(theme: MemorialTheme): string | undefined {
  return theme.bgVideo || undefined;
}
