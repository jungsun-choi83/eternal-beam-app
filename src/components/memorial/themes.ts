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
}

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
];

export function getMemorialTheme(id: number | null): MemorialTheme | undefined {
  if (id == null) return undefined;
  return memorialThemes.find((t) => t.id === id);
}
