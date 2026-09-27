using System;
using System.Collections;
using NUnit.Framework;
using UnityEngine;
using UnityEngine.Networking;
using UnityEngine.TestTools;

namespace EternalBeam.Device.Tests
{
    /// <summary>
    /// Milestone 4 — **실제 Phase 7 packed 자산** 이 실체인으로 투명하게 그려진다.
    ///
    ///   이 테스트(Unity PlayMode)
    ///     → POST http://127.0.0.1:8787/demo/pet-ready  (실행 중인 브리지, 실계약 본문)
    ///     → 브리지가 UDP :5005 로 nfc_match + idle 전달
    ///     → PetVideoScreen: delivery_format=packed_alpha → packed 모드 prepare/play
    ///     → 전체 프레임 프로브: 모서리=배경(투명 OK) + 펫 픽셀 존재(그려짐 OK)
    ///
    /// 요구 환경: 브리지 실행 중(UDP_HOST=127.0.0.1), M4_PACKED_URL 에 실제
    /// packed 자산의 신선한 서명 URL. 없으면 Inconclusive — 실패로 위장하지 않는다.
    /// </summary>
    public class Milestone4Proof
    {
        private const string Bridge = "http://127.0.0.1:8787/demo/pet-ready";

        [UnityTest]
        public IEnumerator RealPackedAsset_Renders_Transparent_Pet()
        {
            string url = Environment.GetEnvironmentVariable("M4_PACKED_URL");
            if (string.IsNullOrEmpty(url))
            {
                Assert.Inconclusive("M4_PACKED_URL 미설정 — 실제 packed 서명 URL 없이는 증명할 수 없다.");
                yield break;
            }

            var screen = UnityEngine.Object.FindFirstObjectByType<PetVideoScreen>();
            Assert.IsNotNull(screen, "PetVideoScreen 이 부트스트랩되지 않았다");

            // 실계약 본문 — buildPhase7PetReadyBody 와 같은 필드 구성.
            string body = "{\"content_id\":\"upload_1788440064381_flsuewbc0a\"," +
                          "\"pet_id\":\"pet_upload_1788440064381_flsuewbc0a\"," +
                          "\"motion_id\":\"BREATHING\",\"idle_url\":\"" + url + "\"," +
                          "\"video_url\":\"" + url + "\",\"packed_url\":\"" + url + "\"," +
                          "\"delivery_format\":\"packed_alpha\"}";
            using (var req = new UnityWebRequest(Bridge, "POST"))
            {
                req.uploadHandler = new UploadHandlerRaw(System.Text.Encoding.UTF8.GetBytes(body));
                req.downloadHandler = new DownloadHandlerBuffer();
                req.SetRequestHeader("Content-Type", "application/json");
                yield return req.SendWebRequest();
                if (req.result != UnityWebRequest.Result.Success)
                {
                    Assert.Inconclusive($"브리지에 닿지 못했다({req.error}) — UDP_HOST=127.0.0.1 브리지를 먼저 띄울 것.");
                    yield break;
                }
            }

            float deadline = Time.realtimeSinceStartup + 60f;
            while (Time.realtimeSinceStartup < deadline && !screen.IsPlaying)
            {
                StringAssert.DoesNotStartWith("error", screen.LastState, "VideoPlayer 오류");
                yield return null;
            }
            Assert.IsTrue(screen.IsPlaying, $"60초 안에 재생이 시작되지 않았다 (state={screen.LastState})");
            Assert.IsTrue(screen.PackedMode, "delivery_format=packed_alpha 인데 packed 모드가 아니다");

            // 프로브는 재생 3초 뒤에 돈다.
            deadline = Time.realtimeSinceStartup + 15f;
            while (Time.realtimeSinceStartup < deadline && !screen.ProbeCompleted)
                yield return null;
            Assert.IsTrue(screen.ProbeCompleted, "투명 프로브가 완료되지 않았다");
            Assert.IsTrue(screen.ProbeCornersBackground, "모서리가 배경색이 아니다 — 매트 검정이 투명 처리되지 않았다");
            Assert.Greater(screen.ProbePetPixels, 300, "펫 픽셀이 없다 — 전부 클리핑되었거나 그려지지 않았다");
            Assert.IsFalse(screen.ProbeBottomMatteGhost, "하단 절반에 흰 매트 이중상이 보인다");

            // BREATHING 은 반드시 루프.
            deadline = Time.realtimeSinceStartup + 12f;
            while (Time.realtimeSinceStartup < deadline && screen.LoopCount < 1)
                yield return null;
            Assert.GreaterOrEqual(screen.LoopCount, 1, "루프 지점에 도달하지 못했다");

            Debug.Log($"[m4] PROOF OK — packed, transparent, petPixels={screen.ProbePetPixels}, loops={screen.LoopCount}");
        }
    }
}
