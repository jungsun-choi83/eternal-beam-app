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
 * ── 왜 sessionStorage 가 아니라 localStorage 인가 ───────────────────────────
 * 처음에는 sessionStorage 였다. 자격 증명이니 탭이 닫히면 사라지는 편이 안전하고,
 * billing-return-state.ts 도 같은 이유로 그것을 쓴다.
 *
 * 그런데 실제 흐름이 그 가정을 깼다: 신규 사용자는 가입 시 **이메일 확인**을
 * 거치고, 확인 링크는 거의 항상 **새 탭**에서 열린다. 새 탭은 sessionStorage 를
 * 공유하지 않으므로, 확인을 마치고 돌아온 그 순간 넘길 편지가 사라져 있었다.
 * 사용자 입장에서는 "로그인은 됐는데 편지가 없다"가 된다.
 *
 * 그래서 localStorage 로 옮기되, **만료를 직접 박는다.** 영구 보관이 아니라
 * "탭을 건너뛸 수 있는 짧은 수명"이 필요한 것이다.
 *
 * ── 수명은 서버 토큰보다 길지 않다 ──────────────────────────────────────────
 * 서버(Soul Trace)의 핸드오프 토큰은 15분·1회용이다. 브라우저가 그보다 오래
 * 들고 있어 봐야 이미 죽은 자격 증명을 방치하는 것뿐이다. 그래서 정확히 같은
 * 15분을 쓰고, 만료된 값은 **읽는 순간 지운다.**
 *
 * ── 클레임 직후 반드시 지운다 ───────────────────────────────────────────────
 * 토큰은 1회용이지만 그것은 서버 쪽 성질이다. 브라우저에 남겨 두면 쓸모없는
 * 자격 증명이 기기에 계속 굴러다닌다. 성공하든 확정적으로 실패하든 즉시 버린다.
 */

/** v2: sessionStorage → localStorage + 만료 도입으로 저장 모양이 바뀌었다. */
export const SOUL_TRACE_HANDOFF_KEY = "eternal_beam_soul_trace_handoff_v2";
/** v1(sessionStorage) 잔여물 — 읽지 않고 치우기만 한다. */
const LEGACY_KEY = "eternal_beam_soul_trace_handoff_v1";

/**
 * 클레임 성공 직후 한 번만 소비되는 표식.
 *
 * 편지를 가져왔으면 다음은 아이를 만드는 단계(기존 Upload Pet 흐름)다. 그 사실을
 * 앱 진입 지점에 전달할 값이 필요한데, **핸드오프 자체를 남겨 두면 안 된다**
 * (이미 소비된 자격 증명이다). 그래서 자격 증명이 아닌 이 표식만 따로 둔다.
 * 자격 증명이 아니므로 탭 수명이면 충분하다 → sessionStorage.
 */
const PENDING_UPLOAD_KEY = "eternal_beam_soul_trace_pending_upload";

/**
 * **방금 가져온 편지의 letter_id.** 클레임과 펫 생성 사이를 잇는 유일한 실.
 *
 * 왜 필요한가: 클레임이 돌려준 letter_id 는 지금까지 화면 state 에만 들어갔다가
 * `location.assign("/")` 과 함께 통째로 사라졌다. 그래서 펫이 만들어진 뒤
 * "어느 편지를 이 펫에 붙여야 하는가"를 아무도 몰랐고, 결제 화면은 목록의
 * 아무 편지나(실제로는 가장 오래된 것) 집었다.
 *
 * 자격 증명이 아니라 **식별자**다(그 자체로는 아무 권한도 없다). 그래서 핸드오프
 * 토큰과 달리 만료를 박지 않는다 — 대신 링크에 성공하면 즉시 지우는 1회용이다.
 * 사진 업로드는 다음 날 할 수도 있으므로 탭 수명(sessionStorage)으로는 부족하다.
 */
const ACTIVE_LETTER_KEY = "eternal_beam_soul_trace_active_letter_v1";

/** 서버 토큰과 같은 15분. 이보다 길게 잡지 않는다. */
export const HANDOFF_MAX_AGE_MS = 15 * 60 * 1000;

export interface SoulTraceHandoff {
  traceId: string;
  handoff: string;
}

interface StoredHandoff extends SoulTraceHandoff {
  /** epoch ms. 이 시각을 넘기면 읽는 쪽이 지운다. */
  expiresAt: number;
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

export function saveSoulTraceHandoff(value: SoulTraceHandoff, now: number = Date.now()): void {
  const row: StoredHandoff = { ...value, expiresAt: now + HANDOFF_MAX_AGE_MS };
  try {
    localStorage.setItem(SOUL_TRACE_HANDOFF_KEY, JSON.stringify(row));
    // v1 이 sessionStorage 에 남아 있을 수 있다 — 같은 목적의 값이 두 곳에
    // 살아 있으면 어느 쪽이 진짜인지 알 수 없게 된다.
    sessionStorage.removeItem(LEGACY_KEY);
  } catch {
    /* 용량 초과·프라이빗 모드 — 이번 왕복이 끊길 뿐이다 */
  }
}

/**
 * 유효한 핸드오프. 없거나 **만료됐으면 null 이고, 만료된 값은 지운다.**
 *
 * 만료를 읽는 쪽에서 처리하는 이유: 타이머로 지우면 탭이 닫혀 있는 동안은
 * 아무도 지우지 않아 죽은 자격 증명이 남는다. 읽을 때 판정하면 항상 정확하다.
 */
export function readSoulTraceHandoff(now: number = Date.now()): SoulTraceHandoff | null {
  try {
    const raw = localStorage.getItem(SOUL_TRACE_HANDOFF_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as Partial<StoredHandoff>;
    const traceId = (parsed.traceId ?? "").trim();
    const handoff = (parsed.handoff ?? "").trim();
    const expiresAt = Number(parsed.expiresAt ?? 0);

    if (!UUID_RE.test(traceId) || !TOKEN_RE.test(handoff) || !Number.isFinite(expiresAt)) {
      clearSoulTraceHandoff();
      return null;
    }
    if (now >= expiresAt) {
      // 서버에서도 이미 죽었다. 들고 있을 이유가 없다.
      clearSoulTraceHandoff();
      return null;
    }
    return { traceId, handoff };
  } catch {
    clearSoulTraceHandoff();
    return null;
  }
}

/** 유효한 핸드오프가 대기 중인가 — 진입 분기가 쓴다. */
export function hasPendingSoulTraceHandoff(now: number = Date.now()): boolean {
  return readSoulTraceHandoff(now) !== null;
}

/** 없음 / 아직 유효 / 만료됨. */
export type SoulTraceHandoffState = "none" | "valid" | "expired";

/**
 * 핸드오프의 상태를 **지우지 않고** 본다.
 *
 * readSoulTraceHandoff 는 만료된 값을 읽는 순간 지운다(그게 맞다 — 죽은 자격
 * 증명을 남길 이유가 없다). 그런데 진입 분기가 그것만 쓰면 만료를 "없음"과
 * 구별하지 못하고, 편지를 기다리던 사용자가 **말없이 평소 온보딩(qrConnection)
 * 으로 떨어진다.** 편지가 사라진 이유를 화면 어디에서도 알 수 없게 되는 것이다.
 *
 * 그래서 판정과 청소를 나눈다: 여기서는 보기만 하고, 청소는 import 화면이
 * captureSoulTraceHandoff 로 진입하면서 한다.
 */
export function peekSoulTraceHandoffState(now: number = Date.now()): SoulTraceHandoffState {
  try {
    const raw = localStorage.getItem(SOUL_TRACE_HANDOFF_KEY);
    if (!raw) return "none";
    const parsed = JSON.parse(raw) as Partial<StoredHandoff>;
    const traceId = (parsed.traceId ?? "").trim();
    const handoff = (parsed.handoff ?? "").trim();
    const expiresAt = Number(parsed.expiresAt ?? 0);
    if (!UUID_RE.test(traceId) || !TOKEN_RE.test(handoff) || !Number.isFinite(expiresAt)) {
      // 쓰레기 값은 "없음"이다 — 복구 화면을 띄울 근거가 못 된다.
      return "none";
    }
    return now >= expiresAt ? "expired" : "valid";
  } catch {
    return "none";
  }
}

export function clearSoulTraceHandoff(): void {
  try {
    localStorage.removeItem(SOUL_TRACE_HANDOFF_KEY);
    sessionStorage.removeItem(LEGACY_KEY);
  } catch {
    /* ignore */
  }
}

/** 클레임 성공 — 다음은 기존 Upload Pet 흐름이다. */
export function markSoulTracePendingUpload(): void {
  try {
    sessionStorage.setItem(PENDING_UPLOAD_KEY, "1");
  } catch {
    /* 표식이 없으면 평소 진입 화면으로 갈 뿐, 편지는 이미 안전하다 */
  }
}

/** 방금 가져온 편지 + 그 시점의 content_id. */
export interface ActiveSoulTraceLetter {
  letterId: string;
  /**
   * 클레임 당시의 content_id (없으면 ""). **"이 편지는 아직 어느 펫에도 속하지
   * 않는다"의 기준선**이다.
   *
   * 이것이 없으면: 편지 B 를 가져온 뒤 예전 펫 A 의 미리보기를 다시 열기만 해도
   * B 가 A 에 붙는다. 편지는 새 업로드에 붙어야 하므로, content_id 가 이 값과
   * **달라졌을 때만** 링크한다(= 클레임 이후에 만들어진 펫이다).
   */
  contentIdAtClaim: string;
}

/** 클레임이 성공했다 — 다음에 만들어지는 펫에 붙일 편지를 적어 둔다. */
export function saveActiveSoulTraceLetter(letterId: string, contentIdAtClaim: string): void {
  const id = (letterId || "").trim();
  if (!id) return;
  try {
    localStorage.setItem(
      ACTIVE_LETTER_KEY,
      JSON.stringify({ letterId: id, contentIdAtClaim: (contentIdAtClaim || "").trim() })
    );
  } catch {
    /* 저장 실패 — 결제 화면이 pet_id 로 고르는 경로는 그대로 살아 있다 */
  }
}

export function readActiveSoulTraceLetter(): ActiveSoulTraceLetter | null {
  try {
    const raw = localStorage.getItem(ACTIVE_LETTER_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as Partial<ActiveSoulTraceLetter>;
    const letterId = (parsed.letterId ?? "").trim();
    if (!letterId) return null;
    return { letterId, contentIdAtClaim: (parsed.contentIdAtClaim ?? "").trim() };
  } catch {
    return null;
  }
}

/** 링크에 성공했다(또는 편지가 죽었다) — 1회용이므로 즉시 버린다. */
export function clearActiveSoulTraceLetter(): void {
  try {
    localStorage.removeItem(ACTIVE_LETTER_KEY);
  } catch {
    /* ignore */
  }
}

/**
 * 표식을 **한 번만** 돌려준다. 읽으면서 지운다 —
 * 남겨 두면 그 뒤로 앱을 열 때마다 업로드 화면으로 끌려간다.
 */
export function consumeSoulTracePendingUpload(): boolean {
  try {
    const v = sessionStorage.getItem(PENDING_UPLOAD_KEY);
    if (!v) return false;
    sessionStorage.removeItem(PENDING_UPLOAD_KEY);
    return true;
  } catch {
    return false;
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
 * 유효한 핸드오프를 돌려준다.
 *
 * URL 에 없으면 저장된 것을 쓴다 — 로그인·이메일 확인 왕복에서 돌아온 경우다.
 */
export function captureSoulTraceHandoff(
  search: string,
  now: number = Date.now(),
): SoulTraceHandoff | null {
  const fromUrl = readSoulTraceHandoffParams(search);
  if (fromUrl) {
    saveSoulTraceHandoff(fromUrl, now);
    stripHandoffFromUrl();
    return fromUrl;
  }
  return readSoulTraceHandoff(now);
}
