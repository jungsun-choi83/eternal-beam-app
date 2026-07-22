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
}

/** "내 사진으로 나만의 배경 만들기" 테마의 고정 id/key — 여러 파일에서 참조하므로 상수로 노출. */
export const CUSTOM_PHOTO_BG_THEME_ID = 9;
export const CUSTOM_PHOTO_BG_THEME_KEY = "custom_photo_bg";

export const memorialThemes: MemorialTheme[] = [
  {
    id: 1,
    name: "Snow Forest",
    nameKo: "눈 숲",
    themeKey: "snow_forest",
    gradient: "from-slate-800 via-blue-950 to-black",
    accent: "#93c5fd",
    premium: false,
    price: "",
    thumb: "/theme-thumbs/snow_forest.jpg",
    bgVideo: "/backgrounds/snow_forest/foreground_light.mp4",
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
    thumb: "/theme-thumbs/celestial.jpg",
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
  },
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
    thumb: "/theme-thumbs/custom_photo_bg.jpg",
    // bgVideo 없음 — 고정 에셋이 아니라 사용자별로 생성됨(custom-background-store.ts 참고).
    requiresGeneration: true,
  },
];

export function getMemorialTheme(id: number | null): MemorialTheme | undefined {
  if (id == null) return undefined;
  return memorialThemes.find((t) => t.id === id);
}

export const freeMemorialThemes = memorialThemes.filter((t) => !t.premium);
export const premiumMemorialThemes = memorialThemes.filter((t) => t.premium);

export function isPremiumTheme(themeId: number | null | undefined): boolean {
  return getMemorialTheme(themeId ?? null)?.premium ?? false;
}

/** snow_forest 전용 mp4는 아직 public에 없음 — thumb 폴백으로 잘못된 배경 노출 방지 */
export function getThemePreviewBgVideo(theme: MemorialTheme): string | undefined {
  if (!theme.bgVideo) return undefined;
  if (theme.themeKey === "snow_forest") return undefined;
  return theme.bgVideo;
}
