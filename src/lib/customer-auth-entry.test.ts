import { strict as assert } from "node:assert";
import { readFileSync } from "node:fs";
import { describe, it } from "node:test";

const app = readFileSync("src/app/EternalBeamApp.tsx", "utf8");
const auth = readFileSync("src/components/memorial/auth-screen.tsx", "utf8");

function screenBlock(name: "signup" | "login"): string {
  const start = app.indexOf(`screen === '${name}'`);
  assert.ok(start >= 0, `${name} 화면이 없다`);
  return app.slice(start, start + 1_500);
}

describe("고객 인증 진입", () => {
  it("첫 인증 화면에서 로그인과 회원가입을 모두 선택할 수 있다", () => {
    const signup = screenBlock("signup");
    assert.match(signup, /initialMode="signup"/);
    assert.doesNotMatch(
      signup,
      /lockMode="signup"/,
      "회원가입으로 잠겨 있어 기존 사용자가 로그인할 수 없다",
    );
  });

  it("AuthScreen 이 완료된 인증 모드를 호출자에게 돌려준다", () => {
    assert.match(auth, /onAuthComplete\(label \|\| undefined, mode\)/);
  });

  it("로그인은 홈으로, 회원가입은 사진 업로드로 이어진다", () => {
    const signup = screenBlock("signup");
    assert.match(signup, /completedMode === 'login' \? 'home' : 'photoUpload'/);

    const login = screenBlock("login");
    assert.match(login, /completedMode === 'signup' \? 'photoUpload' : 'home'/);
  });
});
