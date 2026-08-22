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

  it("재개 분기는 대기 중인 핸드오프가 있을 때만 걸린다", () => {
    assert.ok(
      /if \(hasPendingSoulTraceHandoff\(\)\) return <SoulTraceImportScreen \/>/.test(app),
      "무조건 import 화면으로 보내고 있다",
    );
  });

  it("재개 분기는 Shaker·운영·결제 복귀 **뒤**에 온다", () => {
    const resume = app.indexOf("hasPendingSoulTraceHandoff()");
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
      app.indexOf("hasPendingSoulTraceHandoff()") < app.indexOf("return <EternalBeamApp />"),
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
