import assert from "node:assert/strict";
import { describe, it } from "node:test";
import { readdirSync, readFileSync, statSync } from "node:fs";
import { join } from "node:path";

/**
 * 구조로 고정 — **공유 비밀이 브라우저 번들에 들어갈 수 없다.**
 *
 * Vite 는 `import.meta.env.VITE_*` 만 클라이언트로 인라인한다. 그래서 누군가
 * 편의로 `VITE_SOUL_TRACE_SERVICE_TOKEN` 을 만들면, 그 순간 Soul Trace 내부
 * API 자격 증명이 모든 방문자의 JS 파일 안에 평문으로 실려 나간다 — 그리고
 * 그 사실은 코드 리뷰에서 잘 보이지 않는다(한 글자 접두사 차이다).
 *
 * 이 테스트가 그것을 잡는다. 편지 본문은 서버 대 서버로만 이동해야 하고,
 * 그 전제가 이 비밀의 기밀성이다.
 */

const SRC = join(process.cwd(), "src");

function walk(dir: string): string[] {
  const out: string[] = [];
  for (const name of readdirSync(dir)) {
    const p = join(dir, name);
    if (statSync(p).isDirectory()) out.push(...walk(p));
    else if (/\.(ts|tsx|js|jsx)$/.test(name)) out.push(p);
  }
  return out;
}

describe("Soul Trace 공유 비밀은 클라이언트로 새지 않는다", () => {
  const files = walk(SRC).filter((f) => !f.endsWith("soul-trace-secret-never-bundled.test.ts"));

  it("VITE_ 로 노출되는 서비스 토큰이 없다", () => {
    const offenders = files.filter((f) =>
      /VITE_[A-Z_]*SOUL_TRACE[A-Z_]*(TOKEN|SECRET|KEY)/.test(readFileSync(f, "utf8")),
    );
    assert.deepEqual(offenders, [], `클라이언트 코드에 서비스 토큰 노출: ${offenders}`);
  });

  it("클라이언트 코드가 SOUL_TRACE_SERVICE_TOKEN 을 읽지 않는다", () => {
    const offenders = files.filter((f) =>
      readFileSync(f, "utf8").includes("SOUL_TRACE_SERVICE_TOKEN"),
    );
    assert.deepEqual(offenders, [], `클라이언트가 서버 전용 비밀을 참조: ${offenders}`);
  });

  it("클라이언트가 Soul Trace 내부 API 를 직접 부르지 않는다", () => {
    // 브라우저가 /api/internal/letter 를 부를 수 있다면 그것은 곧 비밀을
    // 들고 있다는 뜻이다. 교환은 **EB 백엔드만** 한다.
    const offenders = files.filter((f) =>
      readFileSync(f, "utf8").includes("/api/internal/letter"),
    );
    assert.deepEqual(offenders, [], `클라이언트가 내부 API 를 호출: ${offenders}`);
  });
});
