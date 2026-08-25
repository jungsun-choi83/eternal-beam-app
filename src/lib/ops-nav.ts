/**
 * Ops 워크스페이스의 경로와 내비게이션 — **한 곳에서 정한다.**
 *
 * 예전에는 경로 판정이 shaker-ops-entry.ts 에 흩어져 있었고, `/ops` 와
 * `/ops/search` 가 "생산 콘솔"이라는 같은 화면으로 접혀 있었다. 스태프에게는
 * 그 둘이 서로 다른 개념처럼 보였지만 실제로는 하나였다.
 *
 * 이제 `/ops` 는 **대시보드**이고 검색은 주문 화면 안에 있다 — 검색은 화면이
 * 아니라 주문 목록의 기능이기 때문이다.
 *
 * ⚠️ 경로 분기는 **보안 경계가 아니다.** 인가는 서버가 한다
 * (JWT + SHAKER_OPS_USER_IDS). 여기서는 어느 화면을 그릴지만 정한다.
 */

export type OpsRoute = "dashboard" | "orders" | "partners" | "shaker";

export const OPS_ROOT = "/ops";
export const OPS_ORDERS_PATH = "/ops/production";
export const OPS_PARTNERS_PATH = "/ops/partners";
export const OPS_SHAKER_PATH = "/ops/shaker";

export interface OpsNavItem {
  route: OpsRoute;
  path: string;
  label: string;
}

/** 사이드바 순서 — 스태프가 실제로 쓰는 빈도 순이다. */
export const OPS_NAV: readonly OpsNavItem[] = [
  { route: "dashboard", path: OPS_ROOT, label: "Dashboard" },
  { route: "orders", path: OPS_ORDERS_PATH, label: "Orders" },
  { route: "partners", path: OPS_PARTNERS_PATH, label: "Partners" },
  { route: "shaker", path: OPS_SHAKER_PATH, label: "Shaker" },
];

function normalize(pathname: string): string {
  return (pathname || "").replace(/\/+$/, "") || "/";
}

/**
 * 경로 → 화면. Ops 경로가 아니면 null.
 *
 * `/ops/search` 는 **여전히 인식한다** — 예전 링크·북마크가 죽지 않아야 한다.
 * 다만 별도 개념으로 노출하지 않고 주문 화면으로 접는다.
 */
export function opsRouteFor(pathname: string): OpsRoute | null {
  const p = normalize(pathname);
  if (p === OPS_ROOT) return "dashboard";
  if (p === OPS_ORDERS_PATH || p === "/ops/search") return "orders";
  if (p === OPS_PARTNERS_PATH) return "partners";
  if (p === OPS_SHAKER_PATH) return "shaker";
  return null;
}

export function isOpsPath(pathname: string): boolean {
  return opsRouteFor(pathname) !== null;
}

export function currentOpsRoute(): OpsRoute | null {
  if (typeof window === "undefined") return null;
  return opsRouteFor(window.location.pathname);
}

export function pathForRoute(route: OpsRoute): string {
  return OPS_NAV.find((n) => n.route === route)?.path ?? OPS_ROOT;
}
