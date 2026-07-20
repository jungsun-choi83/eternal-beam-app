/** 테스트 앱 플래그 — .env / Vercel 환경 변수 */

/** IAP: mock 영수증으로 verify-and-charge (기본 켜짐, 끄려면 VITE_IAP_MOCK=0) */
export const IAP_MOCK_ENABLED = import.meta.env.VITE_IAP_MOCK !== "0";

/** 구독 웹훅 목업 (기본 켜짐, 끄려면 VITE_SUBSCRIPTION_MOCK=0) */
export const SUBSCRIPTION_MOCK_ENABLED =
  import.meta.env.VITE_SUBSCRIPTION_MOCK !== "0";

/** 누끼: 서버/AI 없이 원본 리사이즈만 (빠른 플로우 테스트) */
export const MOCK_CUTOUT_ENABLED = import.meta.env.VITE_MOCK_CUTOUT === "1";

/** 테스트 앱 UX (오류 문구 완화 등) */
export const TEST_APP_MODE =
  import.meta.env.VITE_TEST_APP === "1" || IAP_MOCK_ENABLED || MOCK_CUTOUT_ENABLED;

/** 아이들(Idle) 5종 세트 테스트 패널 — Luma가 켜져 있을 때만 노출 (설정 화면) */
// .trim() — Vercel 등 대시보드에서 값에 공백/개행이 섞여 들어가도 안전하게 비교
export const IDLE_TEST_PANEL_ENABLED =
  String(import.meta.env.VITE_ENABLE_LUMA ?? "").trim() === "1";
