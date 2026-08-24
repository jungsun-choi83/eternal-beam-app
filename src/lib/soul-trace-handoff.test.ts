import assert from "node:assert/strict";
import { describe, it, beforeEach } from "node:test";
import { readFileSync } from "node:fs";

import {
  HANDOFF_MAX_AGE_MS,
  SOUL_TRACE_HANDOFF_KEY,
  captureSoulTraceHandoff,
  clearSoulTraceHandoff,
  consumeSoulTracePendingUpload,
  hasPendingSoulTraceHandoff,
  isSoulTraceImportEntry,
  markSoulTracePendingUpload,
  readSoulTraceHandoff,
  readSoulTraceHandoffParams,
  saveSoulTraceHandoff,
  clearActiveSoulTraceLetter,
  peekSoulTraceHandoffState,
  readActiveSoulTraceLetter,
  saveActiveSoulTraceLetter,
} from "./soul-trace-handoff.ts";

const TRACE = "3f2504e0-4f89-41d3-9a0c-0305e82c3301";
const TOKEN = "A".repeat(43);
const T0 = 1_800_000_000_000;

/**
 * localStorage / sessionStorage / window 를 최소한으로 흉내 낸다.
 * 두 저장소를 **따로** 둬야 "탭을 건너뛴다"를 진짜로 검증할 수 있다.
 */
function installDom(search = "") {
  const local = new Map<string, string>();
  const session = new Map<string, string>();
  const mk = (m: Map<string, string>) => ({
    getItem: (k: string) => m.get(k) ?? null,
    setItem: (k: string, v: string) => void m.set(k, v),
    removeItem: (k: string) => void m.delete(k),
  });
  const g = globalThis as Record<string, unknown>;
  g.localStorage = mk(local);
  g.sessionStorage = mk(session);
  const replaced: string[] = [];
  g.window = {
    location: { href: `https://device.eternalbeam.com/soul-trace/import${search}` },
    history: { replaceState: (_s: unknown, _t: string, url: string) => replaced.push(url) },
  };
  return { local, session, replaced };
}

describe("진입 감지", () => {
  it("import 경로만 알아본다", () => {
    assert.equal(isSoulTraceImportEntry("/soul-trace/import"), true);
    assert.equal(isSoulTraceImportEntry("/soul-trace/import/"), true);
    assert.equal(isSoulTraceImportEntry("/soul-trace"), false);
    assert.equal(isSoulTraceImportEntry("/"), false);
    assert.equal(isSoulTraceImportEntry("/shaker"), false);
  });

  it("모양이 맞는 traceId + 토큰만 읽는다", () => {
    assert.deepEqual(readSoulTraceHandoffParams(`?traceId=${TRACE}&handoff=${TOKEN}`), {
      traceId: TRACE, handoff: TOKEN,
    });
    assert.equal(readSoulTraceHandoffParams(""), null);
    assert.equal(readSoulTraceHandoffParams(`?traceId=not-a-uuid&handoff=${TOKEN}`), null);
    assert.equal(readSoulTraceHandoffParams(`?traceId=${TRACE}&handoff=short`), null);
    assert.equal(readSoulTraceHandoffParams(`?handoff=${TOKEN}`), null);
  });
});

describe("이메일 확인 탭을 건너뛴다", () => {
  beforeEach(() => installDom());

  it("localStorage 에 저장된다 — 새 탭에서도 읽을 수 있어야 한다", () => {
    const { local, session } = installDom();
    saveSoulTraceHandoff({ traceId: TRACE, handoff: TOKEN }, T0);
    assert.ok(local.has(SOUL_TRACE_HANDOFF_KEY), "localStorage 에 없다 — 새 탭에서 사라진다");
    assert.equal(session.size, 0, "sessionStorage 에 자격 증명이 남아 있다");
  });

  it("**새 탭**(sessionStorage 비어 있음)에서도 복원된다", () => {
    const { local } = installDom();
    saveSoulTraceHandoff({ traceId: TRACE, handoff: TOKEN }, T0);
    const carried = local.get(SOUL_TRACE_HANDOFF_KEY)!;

    // 새 탭: session 은 비었지만 local 은 이어진다.
    const fresh = installDom();
    fresh.local.set(SOUL_TRACE_HANDOFF_KEY, carried);

    assert.deepEqual(readSoulTraceHandoff(T0 + 1000), { traceId: TRACE, handoff: TOKEN });
  });
});

describe("만료 — 서버 토큰보다 오래 들고 있지 않는다", () => {
  beforeEach(() => installDom());

  it("수명이 서버 토큰(15분)과 같다", () => {
    assert.equal(HANDOFF_MAX_AGE_MS, 15 * 60 * 1000);
  });

  it("만료 직전에는 유효하다", () => {
    saveSoulTraceHandoff({ traceId: TRACE, handoff: TOKEN }, T0);
    assert.ok(readSoulTraceHandoff(T0 + HANDOFF_MAX_AGE_MS - 1));
  });

  it("만료되면 null 이고 **저장소에서 지워진다**", () => {
    const { local } = installDom();
    saveSoulTraceHandoff({ traceId: TRACE, handoff: TOKEN }, T0);
    assert.equal(readSoulTraceHandoff(T0 + HANDOFF_MAX_AGE_MS), null);
    assert.ok(!local.has(SOUL_TRACE_HANDOFF_KEY), "만료된 자격 증명이 남아 있다");
  });

  it("손상된 값은 무시하고 지운다", () => {
    const { local } = installDom();
    local.set(SOUL_TRACE_HANDOFF_KEY, "{ not json");
    assert.equal(readSoulTraceHandoff(T0), null);
    assert.ok(!local.has(SOUL_TRACE_HANDOFF_KEY));
  });

  it("만료 필드가 없는 값은 신뢰하지 않는다", () => {
    const { local } = installDom();
    local.set(SOUL_TRACE_HANDOFF_KEY, JSON.stringify({ traceId: TRACE, handoff: TOKEN }));
    assert.equal(readSoulTraceHandoff(T0), null);
  });

  it("hasPending 은 만료를 그대로 따른다", () => {
    saveSoulTraceHandoff({ traceId: TRACE, handoff: TOKEN }, T0);
    assert.equal(hasPendingSoulTraceHandoff(T0 + 1), true);
    assert.equal(hasPendingSoulTraceHandoff(T0 + HANDOFF_MAX_AGE_MS), false);
  });
});

describe("클레임 성공 후 정리", () => {
  it("clear 하면 아무 저장소에도 남지 않는다", () => {
    const { local, session } = installDom();
    saveSoulTraceHandoff({ traceId: TRACE, handoff: TOKEN }, T0);
    clearSoulTraceHandoff();
    assert.equal(readSoulTraceHandoff(T0), null);
    assert.ok(!local.has(SOUL_TRACE_HANDOFF_KEY));
    assert.equal(session.size, 0);
    assert.equal(hasPendingSoulTraceHandoff(T0), false);
  });
});

describe("Upload Pet 이어가기 표식", () => {
  beforeEach(() => installDom());

  it("표식이 없으면 기존 진입 흐름 그대로다", () => {
    assert.equal(consumeSoulTracePendingUpload(), false);
  });

  it("**한 번만** 소비된다 — 그 뒤로는 평소 화면으로 돌아간다", () => {
    markSoulTracePendingUpload();
    assert.equal(consumeSoulTracePendingUpload(), true);
    assert.equal(consumeSoulTracePendingUpload(), false);
  });

  it("표식은 자격 증명이 아니다 — 핸드오프 값을 담지 않는다", () => {
    const { session } = installDom();
    markSoulTracePendingUpload();
    const dumped = JSON.stringify([...session.entries()]);
    assert.ok(!dumped.includes(TOKEN), "표식에 토큰이 들어 있다");
    assert.ok(!dumped.includes(TRACE), "표식에 traceId 가 들어 있다");
  });
});

describe("capture", () => {
  it("URL 에서 집어 저장하고 **주소창에서 토큰을 지운다**", () => {
    const { replaced } = installDom(`?traceId=${TRACE}&handoff=${TOKEN}`);
    const got = captureSoulTraceHandoff(`?traceId=${TRACE}&handoff=${TOKEN}`, T0);

    assert.deepEqual(got, { traceId: TRACE, handoff: TOKEN });
    assert.deepEqual(readSoulTraceHandoff(T0), { traceId: TRACE, handoff: TOKEN });
    assert.equal(replaced.length, 1);
    assert.ok(!replaced[0].includes(TOKEN));
    assert.ok(!replaced[0].includes(TRACE));
    assert.equal(replaced[0], "/soul-trace/import");
  });

  it("URL 이 비면 저장된 값을 쓴다 — 확인 왕복에서 돌아온 경우", () => {
    installDom();
    saveSoulTraceHandoff({ traceId: TRACE, handoff: TOKEN }, T0);
    assert.deepEqual(captureSoulTraceHandoff("", T0 + 1000), { traceId: TRACE, handoff: TOKEN });
  });

  it("저장된 값이 만료됐으면 null", () => {
    installDom();
    saveSoulTraceHandoff({ traceId: TRACE, handoff: TOKEN }, T0);
    assert.equal(captureSoulTraceHandoff("", T0 + HANDOFF_MAX_AGE_MS), null);
  });

  it("아무것도 없으면 null", () => {
    installDom();
    assert.equal(captureSoulTraceHandoff("", T0), null);
  });
});

describe("편지 본문은 브라우저 저장소에 들어가지 않는다", () => {
  it("저장되는 것은 traceId·토큰·만료뿐이다", () => {
    const { local } = installDom();
    saveSoulTraceHandoff({ traceId: TRACE, handoff: TOKEN }, T0);
    const parsed = JSON.parse(local.get(SOUL_TRACE_HANDOFF_KEY)!) as Record<string, unknown>;
    assert.deepEqual(Object.keys(parsed).sort(), ["expiresAt", "handoff", "traceId"]);
  });
});

/**
 * 구조 고정 — **직접 진입 흐름을 건드리지 않는다.**
 *
 * 재개 분기와 Upload Pet 분기는 둘 다 "대기 중인 것이 있을 때만" 동작해야 한다.
 * 조건 없이 걸리면 device.eternalbeam.com 으로 그냥 들어온 사용자까지
 * import 화면이나 사진 업로드로 끌려간다.
 */
describe("구조 고정 — 기존 진입 흐름 보존", () => {
  const app = readFileSync("src/app/App.tsx", "utf8");
  const eb = readFileSync("src/app/EternalBeamApp.tsx", "utf8");

  it("재개 분기는 핸드오프의 흔적이 있을 때만 걸린다", () => {
    assert.ok(
      /if \(peekSoulTraceHandoffState\(\) !== 'none'\) return <SoulTraceImportScreen \/>/.test(app),
      "무조건 import 화면으로 보내고 있다",
    );
  });

  it("재개 분기가 만료를 삼키지 않는다 — 조용한 qrConnection 낙하 금지", () => {
    // hasPendingSoulTraceHandoff() 는 만료를 false 로 뭉갠다. 그것으로 분기하면
    // 편지를 기다리던 사용자가 설명 없이 평소 온보딩으로 떨어진다.
    assert.ok(
      !app.includes("hasPendingSoulTraceHandoff()"),
      "만료를 '없음'으로 뭉개는 판정으로 되돌아갔다",
    );
  });

  it("재개 분기는 Shaker·운영·결제 복귀 **뒤**에 온다", () => {
    const resume = app.indexOf("peekSoulTraceHandoffState()");
    for (const earlier of [
      "orderReturnEntry()",
      "themeReturnEntry()",
      "isOpsProductionEntry()",
      "isOpsShakerEntry()",
      "isShakerEntry()",
    ]) {
      assert.ok(
        app.indexOf(earlier) < resume,
        `${earlier} 보다 먼저 재개 분기가 걸린다 — 공개/운영 경로를 가로챈다`,
      );
    }
  });

  it("재개 분기는 EternalBeamApp 폴백 **앞**에 온다", () => {
    assert.ok(
      app.indexOf("peekSoulTraceHandoffState()") < app.indexOf("return <EternalBeamApp />"),
      "폴백 뒤에 있어 절대 실행되지 않는다",
    );
  });

  it("Upload Pet 우회는 1회성 표식을 소비할 때만 걸린다", () => {
    assert.ok(
      /if \(consumeSoulTracePendingUpload\(\)\) return 'photoUpload'/.test(eb),
      "조건 없이 사진 업로드로 보내고 있다 — 기존 진입(qrConnection)이 깨진다",
    );
  });

  it("기본 진입 화면은 그대로 qrConnection 이다", () => {
    const fn = eb.slice(eb.indexOf("function resolveInitialScreen"));
    assert.ok(fn.slice(0, 900).includes("return 'qrConnection'"), "기본 진입이 바뀌었다");
  });

  it("Upload Pet 흐름을 새로 만들지 않는다 — 기존 화면 이름을 그대로 쓴다", () => {
    assert.ok(eb.includes("screen === 'photoUpload'"), "기존 photoUpload 화면이 없다");
  });
});

// ── 만료를 "없음"과 구별한다 ────────────────────────────────────────────────
//
// 회귀 배경: 진입 분기는 hasPendingSoulTraceHandoff() 만 봤다. 그 함수는 만료된
// 값을 읽으면서 지우고 false 를 돌려주므로, 만료와 "처음부터 없음"이 같은 답이
// 됐다. 그래서 편지를 기다리던 신규 사용자가 아무 설명 없이 평소 온보딩
// (qrConnection)으로 떨어졌고, 편지가 어디로 갔는지 화면 어디에도 없었다.
describe("핸드오프 상태 peek — 만료를 삼키지 않는다", () => {
  beforeEach(() => installDom());

  it("없으면 none", () => {
    assert.equal(peekSoulTraceHandoffState(T0), "none");
  });

  it("유효하면 valid", () => {
    saveSoulTraceHandoff({ traceId: TRACE, handoff: TOKEN }, T0);
    assert.equal(peekSoulTraceHandoffState(T0 + 1000), "valid");
  });

  it("만료되면 expired — none 이 아니다", () => {
    saveSoulTraceHandoff({ traceId: TRACE, handoff: TOKEN }, T0);
    assert.equal(peekSoulTraceHandoffState(T0 + HANDOFF_MAX_AGE_MS), "expired");
  });

  it("peek 은 **지우지 않는다** — 복구 화면이 이유를 말할 수 있어야 한다", () => {
    const { local } = installDom();
    saveSoulTraceHandoff({ traceId: TRACE, handoff: TOKEN }, T0);
    peekSoulTraceHandoffState(T0 + HANDOFF_MAX_AGE_MS);
    assert.ok(local.has(SOUL_TRACE_HANDOFF_KEY), "peek 이 값을 지웠다");
    // 청소는 import 화면이 capture 로 진입하면서 한다.
    assert.equal(captureSoulTraceHandoff("", T0 + HANDOFF_MAX_AGE_MS), null);
    assert.ok(!local.has(SOUL_TRACE_HANDOFF_KEY), "capture 가 만료값을 남겼다");
  });

  it("쓰레기 값은 none — 복구 화면을 띄울 근거가 못 된다", () => {
    const { local } = installDom();
    local.set(SOUL_TRACE_HANDOFF_KEY, "{ not json");
    assert.equal(peekSoulTraceHandoffState(T0), "none");
    local.set(SOUL_TRACE_HANDOFF_KEY, JSON.stringify({ traceId: "nope", handoff: TOKEN }));
    assert.equal(peekSoulTraceHandoffState(T0), "none");
  });
});

// ── 클레임 → 펫 생성을 잇는 실 ──────────────────────────────────────────────
//
// 회귀 배경: 클레임이 돌려준 letter_id 는 화면 state 에만 들어갔다가
// location.assign("/") 과 함께 사라졌다. 그래서 펫이 만들어진 뒤 "이 펫에 붙일
// 편지"를 아무도 알지 못했다.
describe("활성 편지 — 클레임과 펫 생성 사이", () => {
  beforeEach(() => installDom());

  it("없으면 null", () => {
    assert.equal(readActiveSoulTraceLetter(), null);
  });

  it("letter_id 와 클레임 시점 content_id 를 함께 남긴다", () => {
    saveActiveSoulTraceLetter("stl_4784", "content_old");
    assert.deepEqual(readActiveSoulTraceLetter(), {
      letterId: "stl_4784",
      contentIdAtClaim: "content_old",
    });
  });

  it("**localStorage** 에 남는다 — 사진 업로드는 다음 날일 수도 있다", () => {
    const { local, session } = installDom();
    saveActiveSoulTraceLetter("stl_4784", "");
    assert.equal(session.size, 0, "탭 수명 저장소로는 부족하다");
    assert.ok([...local.keys()].some((k) => k.includes("active_letter")));
  });

  it("자격 증명을 담지 않는다 — 식별자뿐이다", () => {
    const { local } = installDom();
    saveSoulTraceHandoff({ traceId: TRACE, handoff: TOKEN }, T0);
    saveActiveSoulTraceLetter("stl_4784", "content_old");
    const activeKey = [...local.keys()].find((k) => k.includes("active_letter"))!;
    const dumped = local.get(activeKey)!;
    assert.ok(!dumped.includes(TOKEN), "활성 편지 표식에 토큰이 들어 있다");
    assert.ok(!dumped.includes(TRACE), "활성 편지 표식에 traceId 가 들어 있다");
  });

  it("링크 성공 후 지워진다 — 1회용이다", () => {
    saveActiveSoulTraceLetter("stl_4784", "");
    clearActiveSoulTraceLetter();
    assert.equal(readActiveSoulTraceLetter(), null);
  });

  it("빈 letter_id 는 저장하지 않는다", () => {
    saveActiveSoulTraceLetter("   ", "content_old");
    assert.equal(readActiveSoulTraceLetter(), null);
  });
});

/**
 * 구조 고정 — **로그인/가입 후 이어가기가 다시 끊기지 않게 한다.**
 *
 * 회귀 배경: 재개는 AuthScreen 의 onAuthComplete 콜백 **하나에만** 기대고 있었다.
 * 그 콜백은 이메일 확인이 필요한 가입에서는 아예 호출되지 않고(auth-screen 이
 * 안내 문구를 띄우고 return 한다), 확인이 새 탭에서 끝나면 원래 탭은 영영
 * needsAuth 에 멈춘다.
 */
describe("구조 고정 — 인증 후 재개", () => {
  const screen = readFileSync("src/components/memorial/soul-trace-import-screen.tsx", "utf8");
  const auth = readFileSync("src/lib/supabase-auth.ts", "utf8");

  it("세션 변화를 구독해 재개한다 — 콜백 하나에만 기대지 않는다", () => {
    assert.ok(screen.includes("onAuthStateChange"), "재개 경로가 콜백 하나로 되돌아갔다");
  });

  it("교환은 정확히 한 번만 나간다 — 토큰은 1회용이다", () => {
    // 재개 경로가 둘이므로 동시 호출 가드가 없으면 두 번째 교환이 반드시 실패하고
    // 성공한 사용자에게 "이미 사용됨"을 보여 준다.
    assert.ok(screen.includes("inFlightRef"), "동시 호출 가드가 없다");
    assert.ok(screen.includes("claimedRef"), "1회용 가드가 없다");
  });

  it("확인 메일이 **이 origin 의 import 경로**로 돌아온다", () => {
    // 프로젝트 Site URL 로 떨어지면 세션과 핸드오프가 다른 origin 에 갇힌다.
    assert.ok(
      screen.includes("emailRedirectTo"),
      "확인 메일 착지점을 앱이 정하지 않는다",
    );
    assert.ok(
      /emailRedirectTo=\{`\$\{window\.location\.origin\}\/soul-trace\/import`\}/.test(screen),
      "import 화면이 자기 경로를 착지점으로 지정하지 않는다",
    );
    assert.ok(auth.includes("emailRedirectTo"), "signUp 이 착지점을 넘기지 않는다");
  });

  it("이미 로그인한 사용자는 AuthScreen 을 보지 않는다", () => {
    // needsAuth 는 토큰이 **없을 때만** 세워진다.
    assert.ok(
      /if \(!auth\.token\) \{\s*setPhase\(\{ kind: "needsAuth" \}\);/.test(screen),
      "세션이 있어도 로그인 화면을 띄울 수 있는 모양이다",
    );
  });

  it("만료된 핸드오프는 전용 복구 상태를 갖는다", () => {
    assert.ok(screen.includes('kind: "expired"'), "만료 복구 상태가 없다");
    assert.ok(
      screen.includes("peekSoulTraceHandoffState"),
      "만료와 '없음'을 구별하지 않는다",
    );
  });
});
