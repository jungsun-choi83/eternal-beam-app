/**
 * Phase 8 — My Beam (device-screen.tsx) 회귀 가드.
 *
 * 예전 목업은 배터리·용량·펌웨어·Wi-Fi를 지어냈고, 연결 상태는 useState(true)
 * 로 항상 "Connected"였다. Remove Device 버튼은 아무 것도 하지 않았고,
 * "Reconnect"는 실제로는 스플래시 로고 화면(qrConnection)을 보여주고 자동으로
 * "성공"을 선언했을 뿐이다. 이 테스트는 그 셋이 모두 사라졌는지 지킨다.
 */
import { strict as assert } from "node:assert";
import { readFileSync } from "node:fs";
import { describe, it } from "node:test";

const deviceScreen = readFileSync("src/components/memorial/device-screen.tsx", "utf8");
const app = readFileSync("src/app/EternalBeamApp.tsx", "utf8");

describe("My Beam — 지어낸 상태를 걷어낸다", () => {
  it("배터리·용량·펌웨어·Wi-Fi 네트워크 이름을 지어내지 않는다", () => {
    assert.doesNotMatch(deviceScreen, /battery/i);
    assert.doesNotMatch(deviceScreen, /firmware/i);
    assert.doesNotMatch(deviceScreen, /storage/i);
    // Wifi/WifiOff 는 lucide 아이콘 이름이라 허용한다 — 지어낸 SSID/네트워크 이름만 금지.
    assert.doesNotMatch(deviceScreen, /Home_WiFi|deviceInfo\.wifi|wifi:\s*["']/i);
  });

  it("연결 상태를 useState(true) 로 지어내지 않는다 — 실제 게이트웨이 상태를 쓴다", () => {
    assert.doesNotMatch(deviceScreen, /useState\(true\)/);
    assert.match(deviceScreen, /useDeviceConnection\(deviceId\)/);
  });

  it("아무 것도 하지 않는 'Remove Device' 버튼이 없다", () => {
    assert.doesNotMatch(deviceScreen, /Remove Device/);
  });

  it("Retry/Reconnect 는 실제 재조회(retry())를 부른다 — 가짜 QR 성공 화면으로 새지 않는다", () => {
    assert.match(deviceScreen, /onClick=\{retry\}/);
    assert.doesNotMatch(deviceScreen, /qrConnection/);
  });

  it("기기 식별자는 실제 device_id(demoDeviceId) 다 — 지어낸 기기 이름/모델이 아니다", () => {
    assert.match(deviceScreen, /demoDeviceId\(\)/);
  });

  it("최근 전송 내역은 이 브라우저가 실제로 보낸 명령에서만 온다", () => {
    assert.match(deviceScreen, /getRecentBeamCommands|reconcileAckedCommand/);
  });

  it("EternalBeamApp 은 더 이상 onReconnect 를 qrConnection 스플래시로 라우팅하지 않는다", () => {
    const deviceScreenBlock = app.slice(
      app.indexOf("{screen === 'device' && ("),
      app.indexOf("{screen === 'settings' && (")
    );
    assert.doesNotMatch(deviceScreenBlock, /onReconnect/);
    assert.doesNotMatch(deviceScreenBlock, /qrConnection/);
    assert.match(deviceScreenBlock, /onBack=\{\(\) => navigateTo\('settings', 'back'\)\}/);
  });
});
