/**
 * Soul Trace → Eternal Beam 핸드오프 — **브라우저 쪽 보관 규칙.**
 *
 * Soul Trace 가 보내는 것은 두 값뿐이다:
 *
 *     /soul-trace/import?traceId=<uuid>&handoff=<불투명 토큰>
 *
 * 편지 본문·설문·이메일·펫 이미지는 URL 에도, 이 모듈에도 들어오지 않는다.
 * 본문은 EB 백엔드가 Soul Trace 에서 **서버 대 서버로** 가져간다.
 *
 * ── 왜 잠깐이라도 저장해야 하는가 ───────────────────────────────────────────
 * 링크를 연 사람은 대개 로그인 상태가 아니다. 로그인/가입은 페이지를 갈아 치우고
 * (OAuth 왕복이면 문서가 아예 바뀐다) React state 는 그 순간 사라진다. 그러면
 * 인증을 마치고 돌아왔을 때 넘길 편지가 무엇이었는지 알 수 없다.
 *
 * ── 왜 sessionStorage 이고 localStorage 가 아닌가 ───────────────────────────
 * 이 값은 **자격 증명**이다. 탭이 살아 있는 동안만 필요하고, 탭을 닫으면 사라져야
 * 한다. localStorage 는 기기에 영구히 남아 공용 컴퓨터에서 다음 사람에게 넘어간다.
 * (billing-return-state.ts 가 같은 이유로 sessionStorage 를 쓴다.)
 *
 * ── 클레임 직후 반드시 지운다 ───────────────────────────────────────────────
 * 토큰은 1회용이지만, 그것은 서버 쪽 성질이다. 브라우저에 남겨 두면 이미 쓸모없는
 * 자격 증명이 계속 굴러다닌다. 성공하든 확정적으로 실패하든 즉시 버린다.
 */

export const SOUL_TRACE_HANDOFF_KEY = "eternal_beam_soul_trace_handoff_v1";

export interface SoulTraceHandoff {
  traceId: string;
  handoff: string;
}

const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
/** Soul Trace 가 발급하는 모양 — base64url 43자(256비트). */
const TOKEN_RE = /^[A-Za-z0-9_-]{43}$/;

/** `/soul-trace/import` 로 들어왔는가. */
export function isSoulTraceImportEntry(pathname: string): boolean {
  const path = pathname.replace(/\/+$/, "") || "/";
  return path === "/soul-trace/import";
}

/**
 * 쿼리에서 핸드오프를 읽는다. 모양이 틀리면 null —
 * 쓰레기 값을 저장했다가 로그인 뒤에야 실패하면 진단이 어렵다.
 */
export function readSoulTraceHandoffParams(search: string): SoulTraceHandoff | null {
  const params = new URLSearchParams(search);
  const traceId = (params.get("traceId") ?? "").trim();
  const handoff = (params.get("handoff") ?? "").trim();
  if (!UUID_RE.test(traceId) || !TOKEN_RE.test(handoff)) return null;
  return { traceId, handoff };
}

export function saveSoulTraceHandoff(value: SoulTraceHandoff): void {
  try {
    sessionStorage.setItem(SOUL_TRACE_HANDOFF_KEY, JSON.stringify(value));
  } catch {
    /* 용량 초과·프라이빗 모드 — 이번 탭에서 로그인 왕복이 끊길 뿐이다 */
  }
}

export function readSoulTraceHandoff(): SoulTraceHandoff | null {
  try {
    const raw = sessionStorage.getItem(SOUL_TRACE_HANDOFF_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as Partial<SoulTraceHandoff>;
    const traceId = (parsed.traceId ?? "").trim();
    const handoff = (parsed.handoff ?? "").trim();
    if (!UUID_RE.test(traceId) || !TOKEN_RE.test(handoff)) return null;
    return { traceId, handoff };
  } catch {
    return null;
  }
}

export function clearSoulTraceHandoff(): void {
  try {
    sessionStorage.removeItem(SOUL_TRACE_HANDOFF_KEY);
  } catch {
    /* ignore */
  }
}

/**
 * 주소창에서 핸드오프를 **지운다.**
 *
 * 토큰이 URL 에 남아 있으면 새로고침·공유·스크린샷·브라우저 기록으로 계속
 * 새 나간다. 저장한 직후 흔적을 지우고 경로만 남긴다.
 */
export function stripHandoffFromUrl(): void {
  try {
    const url = new URL(window.location.href);
    url.searchParams.delete("traceId");
    url.searchParams.delete("handoff");
    window.history.replaceState(null, "", url.pathname + url.search + url.hash);
  } catch {
    /* ignore */
  }
}

/**
 * 진입 시 한 번 부른다: URL 에 있으면 저장하고 주소창을 청소한 뒤,
 * 이번 탭에서 유효한 핸드오프를 돌려준다.
 *
 * URL 에 없으면 저장된 것을 쓴다 — 로그인 왕복에서 돌아온 경우다.
 */
export function captureSoulTraceHandoff(search: string): SoulTraceHandoff | null {
  const fromUrl = readSoulTraceHandoffParams(search);
  if (fromUrl) {
    saveSoulTraceHandoff(fromUrl);
    stripHandoffFromUrl();
    return fromUrl;
  }
  return readSoulTraceHandoff();
}
