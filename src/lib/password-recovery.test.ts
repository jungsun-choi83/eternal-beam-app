import { strict as assert } from "node:assert";
import { describe, it, beforeEach } from "node:test";

import {
  clearPasswordRecoveryEntry,
  passwordRecoveryEntry,
} from "./password-recovery.ts";

/**
 * window.location(.hash) / sessionStorage 를 최소한으로 흉내 낸다.
 * (soul-trace-handoff.test.ts 의 installDom 과 같은 패턴.)
 */
function installDom(href = "https://eternalbeam.com/") {
  const session = new Map<string, string>();
  const g = globalThis as Record<string, unknown>;
  g.sessionStorage = {
    getItem: (k: string) => session.get(k) ?? null,
    setItem: (k: string, v: string) => void session.set(k, v),
    removeItem: (k: string) => void session.delete(k),
  };
  const state = { href, replaced: [] as string[] };
  g.window = {
    get location() {
      const url = new URL(state.href);
      return {
        href: state.href,
        get hash() {
          return url.hash;
        },
      };
    },
    history: {
      state: null,
      replaceState: (_s: unknown, _t: string, url: string) => {
        state.replaced.push(url);
        state.href = url;
      },
    },
  };
  return { session, state };
}

const RECOVERY_HASH =
  "#access_token=abc.def.ghi&expires_in=3600&refresh_token=zzz&token_type=bearer&type=recovery";
const EXPIRED_HASH = "#error=access_denied&error_code=otp_expired&error_description=Email+link+is+invalid+or+has+expired";

describe("비밀번호 재설정 진입 감지", () => {
  beforeEach(() => installDom());

  it("성공한 재설정 링크(해시의 type=recovery + access_token)를 알아본다", () => {
    installDom(`https://eternalbeam.com/${RECOVERY_HASH}`);
    assert.deepEqual(passwordRecoveryEntry(), { kind: "recovery" });
  });

  it("만료·무효 링크(type 없이 error 만)를 알아본다", () => {
    installDom(`https://eternalbeam.com/${EXPIRED_HASH}`);
    const entry = passwordRecoveryEntry();
    assert.equal(entry?.kind, "recovery_error");
    assert.match((entry as { description: string }).description, /expired/i);
  });

  it("아무 해시도, 표식도 없으면 null — 평소 진입에 관여하지 않는다", () => {
    installDom("https://eternalbeam.com/");
    assert.equal(passwordRecoveryEntry(), null);
  });

  it("type 이 recovery 가 아니면(access_token 없이) 재설정으로 보지 않는다", () => {
    installDom("https://eternalbeam.com/#type=signup");
    assert.equal(passwordRecoveryEntry(), null);
  });

  it("access_token 없이 type=recovery 만 있으면 아직 재설정으로 확정하지 않는다", () => {
    installDom("https://eternalbeam.com/#type=recovery");
    assert.equal(passwordRecoveryEntry(), null);
  });

  it("성공 링크를 읽으면 sessionStorage 에 표식을 남긴다", () => {
    const { session } = installDom(`https://eternalbeam.com/${RECOVERY_HASH}`);
    passwordRecoveryEntry();
    assert.equal(session.size, 1);
  });

  it("새로고침(해시는 이미 지워짐)해도 표식이 있으면 재설정으로 계속 본다", () => {
    const { session } = installDom(`https://eternalbeam.com/${RECOVERY_HASH}`);
    passwordRecoveryEntry(); // 첫 진입 — 표식을 남긴다
    const carried = session.get("eternal_beam_password_recovery_v1")!;

    // 같은 탭, 해시 없는 새로고침을 흉내낸다.
    const fresh = installDom("https://eternalbeam.com/");
    fresh.session.set("eternal_beam_password_recovery_v1", carried);
    assert.deepEqual(passwordRecoveryEntry(), { kind: "recovery" });
  });

  it("만료 오류는 표식을 남기지 않는다 — 재시도할 것이 없다", () => {
    const { session } = installDom(`https://eternalbeam.com/${EXPIRED_HASH}`);
    passwordRecoveryEntry();
    assert.equal(session.size, 0);
  });

  it("clearPasswordRecoveryEntry 는 표식과 해시를 함께 지운다", () => {
    const { session, state } = installDom(`https://eternalbeam.com/${RECOVERY_HASH}`);
    passwordRecoveryEntry();
    assert.equal(session.size, 1);

    clearPasswordRecoveryEntry();
    assert.equal(session.size, 0);
    assert.ok(state.replaced.length > 0, "URL 해시를 지우지 않았다");
    assert.doesNotMatch(state.replaced[state.replaced.length - 1], /type=recovery/);
  });

  it("지운 뒤에는 다시 물어도 null 이다 — 뒤로가기가 재설정 화면으로 돌아가지 않는다", () => {
    installDom(`https://eternalbeam.com/${RECOVERY_HASH}`);
    passwordRecoveryEntry();
    clearPasswordRecoveryEntry();
    assert.equal(passwordRecoveryEntry(), null);
  });
});
