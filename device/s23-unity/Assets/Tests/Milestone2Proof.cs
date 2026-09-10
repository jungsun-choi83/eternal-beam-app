using System;
using System.Collections;
using NUnit.Framework;
using UnityEngine;
using UnityEngine.Networking;
using UnityEngine.TestTools;

namespace EternalBeam.Device.Tests
{
    /// <summary>
    /// Milestone 2 배치 검증 — **실제 체인** 재생 증명 (목업 없음).
    ///
    ///   이 테스트(Unity PlayMode)
    ///     → POST http://127.0.0.1:8787/demo/pet-ready  (실행 중인 브리지)
    ///     → 브리지가 UDP :5005 로 전달
    ///     → DeviceBootstrap 의 수신기 → PetVideoScreen
    ///     → VideoPlayer 가 서명 URL 을 prepare/play
    ///     → RenderTexture 픽셀을 읽어 "실제 프레임이 그려졌다"를 단언
    ///
    /// 요구 환경: 브리지 실행 중(UDP_HOST=127.0.0.1), 환경변수 M2_VIDEO_URL 에
    /// 신선한 서명 URL. 없으면 Inconclusive — 실패로 위장하지 않는다.
    /// </summary>
    public class Milestone2Proof
    {
        private const string Bridge = "http://127.0.0.1:8787/demo/pet-ready";

        [UnityTest]
        public IEnumerator RemoteUrl_Plays_Through_The_Real_Bridge_Chain()
        {
            string url = Environment.GetEnvironmentVariable("M2_VIDEO_URL");
            if (string.IsNullOrEmpty(url))
            {
                Assert.Inconclusive("M2_VIDEO_URL 미설정 — 서명 URL 없이는 원격 재생을 증명할 수 없다.");
                yield break;
            }

            // DeviceBootstrap 이 Play 진입 시 수신기+스크린을 세웠다.
            var screen = UnityEngine.Object.FindFirstObjectByType<PetVideoScreen>();
            Assert.IsNotNull(screen, "PetVideoScreen 이 부트스트랩되지 않았다");

            // 실제 pet-ready POST — Milestone 1 과 동일한 실계약 본문.
            string body = "{\"content_id\":\"m2_proof\",\"pet_id\":\"pet_m2_proof\"," +
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

            // UDP → 수신 → prepare → play. 원격 mp4 첫 준비는 수 초 걸릴 수 있다.
            float deadline = Time.realtimeSinceStartup + 60f;
            while (Time.realtimeSinceStartup < deadline && !screen.IsPlaying)
            {
                StringAssert.DoesNotStartWith("error", screen.LastState, "VideoPlayer 오류");
                yield return null;
            }
            Assert.IsTrue(screen.IsPlaying, $"60초 안에 재생이 시작되지 않았다 (state={screen.LastState})");

            // 시간이 실제로 전진하는가 (정지 프레임/멈춤 방어).
            double t0 = screen.PlaybackTime;
            yield return new WaitForSecondsRealtime(1.5f);
            Assert.Greater(screen.PlaybackTime, t0 + 0.5, "재생 시간이 전진하지 않는다");

            // RenderTexture 에 진짜 프레임이 그려졌는가 — 균일하지 않은 픽셀이어야 한다.
            var rt = screen.Output;
            var tex = new Texture2D(rt.width, rt.height, TextureFormat.RGBA32, false);
            var prev = RenderTexture.active;
            RenderTexture.active = rt;
            tex.ReadPixels(new Rect(0, 0, rt.width, rt.height), 0, 0);
            tex.Apply();
            RenderTexture.active = prev;
            Color32[] px = tex.GetPixels32();
            byte min = 255, max = 0;
            for (int i = 0; i < px.Length; i += 97)
            {
                byte v = (byte)((px[i].r + px[i].g + px[i].b) / 3);
                if (v < min) min = v;
                if (v > max) max = v;
            }
            Debug.Log($"[m2] frame pixels: min={min} max={max} spread={max - min}");
            Assert.Greater(max - min, 20, "프레임이 균일하다 — 비디오가 그려지지 않았다");

            // 루프: 5초 클립이므로 조금 더 기다리면 loopPointReached 가 와야 한다.
            deadline = Time.realtimeSinceStartup + 12f;
            while (Time.realtimeSinceStartup < deadline && screen.LoopCount < 1)
                yield return null;
            Assert.GreaterOrEqual(screen.LoopCount, 1, "루프 지점에 도달하지 못했다 (isLooping 검증 실패)");

            Debug.Log($"[m2] PROOF OK — playing, frames non-uniform, loops={screen.LoopCount}");
        }
    }
}
