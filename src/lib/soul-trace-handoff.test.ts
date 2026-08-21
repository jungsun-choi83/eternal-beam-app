import assert from "node:assert/strict";
import { describe, it, beforeEach } from "node:test";

import {
  SOUL_TRACE_HANDOFF_KEY,
  captureSoulTraceHandoff,
  clearSoulTraceHandoff,
  isSoulTraceImportEntry,
  readSoulTraceHandoff,
  readSoulTraceHandoffParams,
  saveSoulTraceHandoff,
} from "./soul-trace-handoff.ts";

const TRACE = "3f2504e0-4f89-41d3-9a0c-0305e82c3301";
const TOKEN = "A".repeat(43);

/** sessionStorage / window 를 최소한으로 흉내 낸다 (node --test 에는 DOM 이 없다). */
function installDom(search = "") {
  const store = new Map<string, string>();
  const g = globalThis as Record<string, unknown>;
  g.sessionStorage = {
    getItem: (k: string) => store.get(k) ?? null,
    setItem: (k: string, v: string) => void store.set(k, v),
    removeItem: (k: string) => void store.delete(k),
  };
  const replaced: string[] = [];
  g.window = {
    location: { href: `https://device.eternalbeam.com/soul-trace/import${search}` },
    history: { replaceState: (_s: unknown, _t: string, url: string) => replaced.push(url) },
  };
  return { store, replaced };
}

describe("soul trace 핸드오프 진입", () => {
  it("import 경로만 알아본다", () => {
    assert.equal(isSoulTraceImportEntry("/soul-trace/import"), true);
    assert.equal(isSoulTraceImportEntry("/soul-trace/import/"), true);
    assert.equal(isSoulTraceImportEntry("/soul-trace"), false);
    assert.equal(isSoulTraceImportEntry("/"), false);
    assert.equal(isSoulTraceImportEntry("/shaker"), false);
  });

  it("모양이 맞는 traceId + 토큰만 읽는다", () => {
    const ok = readSoulTraceHandoffParams(`?traceId=${TRACE}&handoff=${TOKEN}`);
    assert.deepEqual(ok, { traceId: TRACE, handoff: TOKEN });
  });

  it("쓰레기 값을 받아들이지 않는다 — 로그인 뒤에야 실패하면 진단이 어렵다", () => {
    assert.equal(readSoulTraceHandoffParams(""), null);
    assert.equal(readSoulTraceHandoffParams(`?traceId=not-a-uuid&handoff=${TOKEN}`), null);
    assert.equal(readSoulTraceHandoffParams(`?traceId=${TRACE}&handoff=short`), null);
    // 토큰만 있고 편지가 없으면 무엇을 가져올지 알 수 없다.
    assert.equal(readSoulTraceHandoffParams(`?handoff=${TOKEN}`), null);
  });
});

describe("로그인 왕복 보존", () => {
  beforeEach(() => installDom());

  it("저장했다가 그대로 돌려준다", () => {
    saveSoulTraceHandoff({ traceId: TRACE, handoff: TOKEN });
    assert.deepEqual(readSoulTraceHandoff(), { traceId: TRACE, handoff: TOKEN });
  });

  it("손상된 저장값은 무시한다", () => {
    (globalThis as never as { sessionStorage: Storage }).sessionStorage.setItem(
      SOUL_TRACE_HANDOFF_KEY,
      "{ not json",
    );
    assert.equal(readSoulTraceHandoff(), null);
  });

  it("clear 하면 남지 않는다 — 쓸모없는 자격 증명을 방치하지 않는다", () => {
    saveSoulTraceHandoff({ traceId: TRACE, handoff: TOKEN });
    clearSoulTraceHandoff();
    assert.equal(readSoulTraceHandoff(), null);
  });
});

describe("capture", () => {
  it("URL 에서 집어 저장하고 **주소창에서 토큰을 지운다**", () => {
    const { replaced } = installDom(`?traceId=${TRACE}&handoff=${TOKEN}`);
    const got = captureSoulTraceHandoff(`?traceId=${TRACE}&handoff=${TOKEN}`);

    assert.deepEqual(got, { traceId: TRACE, handoff: TOKEN });
    // 세션에는 남는다 (로그인 왕복을 넘겨야 한다)
    assert.deepEqual(readSoulTraceHandoff(), { traceId: TRACE, handoff: TOKEN });
    // 주소창에는 남지 않는다 — 새로고침·공유·기록으로 새 나가면 안 된다
    assert.equal(replaced.length, 1);
    assert.ok(!replaced[0].includes(TOKEN));
    assert.ok(!replaced[0].includes(TRACE));
    assert.equal(replaced[0], "/soul-trace/import");
  });

  it("URL 이 비면 저장된 값을 쓴다 — 로그인에서 돌아온 경우", () => {
    installDom();
    saveSoulTraceHandoff({ traceId: TRACE, handoff: TOKEN });
    assert.deepEqual(captureSoulTraceHandoff(""), { traceId: TRACE, handoff: TOKEN });
  });

  it("아무것도 없으면 null", () => {
    installDom();
    assert.equal(captureSoulTraceHandoff(""), null);
  });
});

describe("편지 본문은 브라우저 저장소에 들어가지 않는다", () => {
  it("저장되는 것은 traceId 와 토큰뿐이다", () => {
    const { store } = installDom();
    saveSoulTraceHandoff({ traceId: TRACE, handoff: TOKEN });
    const raw = store.get(SOUL_TRACE_HANDOFF_KEY) ?? "";
    const parsed = JSON.parse(raw) as Record<string, unknown>;
    assert.deepEqual(Object.keys(parsed).sort(), ["handoff", "traceId"]);
  });
});
