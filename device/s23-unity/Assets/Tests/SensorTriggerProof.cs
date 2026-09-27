using NUnit.Framework;

namespace EternalBeam.Device.Tests
{
    /// <summary>
    /// M5-lite Phase 2 배선 증명 — 센서 트리거 재생 상태 기계.
    ///
    ///   touch → PET_HEAD 1회 → BREATHING 복귀
    ///   voice → LOOK_UP 1회 → BREATHING 복귀
    ///   approach → COME_CLOSER 1회 → BREATHING 복귀
    ///   없는 모션 → 무시, 홈 유지 / 홈은 계속 루프
    ///
    /// 데이터그램은 전부 **실제 수신 파서**(UdpJsonListener.Parse)를 지나고,
    /// 센서 본문은 Pi 가 실제로 쏘는 모양 그대로다 (pi_sensors_to_unity_udp /
    /// voice_to_unity 관측치). 재생 실행은 셸(PetVideoScreen)의 일이라 여기서는
    /// Command(무엇을, loop 여부)와 상태 전이를 검증한다.
    /// </summary>
    public class SensorTriggerProof
    {
        // Pi 가 :5005 로 실제 쏘는 센서 데이터그램.
        private const string Touch = "{\"event\":\"touch\",\"distance_mm\":85}";
        private const string Voice = "{\"event\":\"voice\",\"source\":\"inmp441\",\"rms\":1234}";
        private const string Approach = "{\"event\":\"approach\",\"distance_mm\":220}";

        private static string AssetDatagram(string motionId, string url)
        {
            return "{\"event\":\"idle\",\"content_id\":\"c1\",\"pet_id\":\"pet_c1\"," +
                   "\"motion_id\":\"" + motionId + "\",\"idle_url\":\"" + url + "\"," +
                   "\"video_url\":\"" + url + "\",\"packed_url\":\"" + url + "\"," +
                   "\"delivery_format\":\"packed_alpha\",\"source\":\"app_idle_ready\"}";
        }

        private static MotionPlaybackController.Command Feed(
            MotionPlaybackController c, string raw)
        {
            return c.OnMessage(UdpJsonListener.Parse(raw));
        }

        /// <summary>프리로드 완료 상태 — 상호작용 3종 먼저, BREATHING 마지막 (Phase 1 순서 계약).</summary>
        private static MotionPlaybackController Preloaded()
        {
            var c = new MotionPlaybackController(new MotionLibrary());
            Feed(c, AssetDatagram("PET_HEAD", "https://s/ph_packed.mp4"));
            Feed(c, AssetDatagram("LOOK_UP", "https://s/lu_packed.mp4"));
            Feed(c, AssetDatagram("COME_CLOSER", "https://s/cc_packed.mp4"));
            var home = Feed(c, AssetDatagram("BREATHING", "https://s/b_packed.mp4"));
            Assert.AreEqual(MotionPlaybackController.CommandKind.Play, home.Kind);
            Assert.IsTrue(home.Loop, "홈은 루프다");
            return c;
        }

        private static void AssertSensorRoundTrip(string sensorRaw, string motionId, string url)
        {
            var c = Preloaded();

            var play = Feed(c, sensorRaw);
            Assert.AreEqual(MotionPlaybackController.CommandKind.Play, play.Kind, sensorRaw);
            Assert.AreEqual(motionId, play.Asset.MotionId);
            Assert.AreEqual(url, play.Asset.Url);
            Assert.IsFalse(play.Loop, "상호작용은 1회 재생 — 루프 금지");
            Assert.AreEqual(motionId, c.CurrentMotionId);

            // 클립 끝 → 저장된 BREATHING 복귀.
            var back = c.OnClipEnded();
            Assert.AreEqual(MotionPlaybackController.CommandKind.Play, back.Kind);
            Assert.AreEqual("BREATHING", back.Asset.MotionId);
            Assert.AreEqual("https://s/b_packed.mp4", back.Asset.Url);
            Assert.IsTrue(back.Loop, "복귀한 홈은 다시 루프다");
            Assert.AreEqual("BREATHING", c.CurrentMotionId);
        }

        [Test]
        public void Touch_PlaysPetHeadOnce_ThenReturnsToBreathing()
        {
            AssertSensorRoundTrip(Touch, "PET_HEAD", "https://s/ph_packed.mp4");
        }

        [Test]
        public void Voice_PlaysLookUpOnce_ThenReturnsToBreathing()
        {
            AssertSensorRoundTrip(Voice, "LOOK_UP", "https://s/lu_packed.mp4");
        }

        [Test]
        public void Approach_PlaysComeCloserOnce_ThenReturnsToBreathing()
        {
            AssertSensorRoundTrip(Approach, "COME_CLOSER", "https://s/cc_packed.mp4");
        }

        [Test]
        public void MissingMotion_IsIgnored_HomeKeepsPlaying()
        {
            // LOOK_UP 만 프리로드에서 빠진 펫.
            var c = new MotionPlaybackController(new MotionLibrary());
            Feed(c, AssetDatagram("PET_HEAD", "https://s/ph_packed.mp4"));
            Feed(c, AssetDatagram("BREATHING", "https://s/b_packed.mp4"));

            var cmd = Feed(c, Voice);
            Assert.AreEqual(MotionPlaybackController.CommandKind.None, cmd.Kind);
            Assert.AreEqual("BREATHING", c.CurrentMotionId, "홈이 그대로 돌아야 한다");

            // 다른 저장된 상호작용은 여전히 동작한다 — 무시가 상태를 오염시키지 않는다.
            var touch = Feed(c, Touch);
            Assert.AreEqual(MotionPlaybackController.CommandKind.Play, touch.Kind);
            Assert.AreEqual("PET_HEAD", touch.Asset.MotionId);
        }

        [Test]
        public void InteractionCommand_NeverLoops_HomeAlwaysLoops()
        {
            var c = Preloaded();
            foreach (var sensor in new[] { Touch, Voice, Approach })
            {
                var play = Feed(c, sensor);
                Assert.IsFalse(play.Loop, sensor);
                var back = c.OnClipEnded();
                Assert.IsTrue(back.Loop, sensor);
            }
        }

        [Test]
        public void Breathing_ContinuesLooping_ClipEndIsNoOp()
        {
            var c = Preloaded();
            // 홈 루프 중 loopPointReached 가 여러 번 와도 아무 명령도 없다 —
            // 루프 유지는 isLooping 플래그의 일이고, 컨트롤러는 끼어들지 않는다.
            for (int i = 0; i < 3; i++)
            {
                Assert.AreEqual(MotionPlaybackController.CommandKind.None, c.OnClipEnded().Kind, $"loop {i}");
                Assert.AreEqual("BREATHING", c.CurrentMotionId);
            }
        }

        [Test]
        public void SensorDuringInteraction_IsIgnored_NonInterruptible()
        {
            var c = Preloaded();
            Feed(c, Touch); // PET_HEAD 재생 중
            var during = Feed(c, Approach);
            Assert.AreEqual(MotionPlaybackController.CommandKind.None, during.Kind);
            Assert.AreEqual("PET_HEAD", c.CurrentMotionId, "재생 중 모션이 바뀌면 안 된다");
            // 끝나면 정상 복귀 — 무시된 이벤트는 큐잉되지 않는다 (웹 no-queue 와 동일).
            Assert.AreEqual("BREATHING", c.OnClipEnded().Asset.MotionId);
        }

        [Test]
        public void HomeUpdate_DuringInteraction_IsDeferred_AppliedOnReturn()
        {
            var c = Preloaded();
            Feed(c, Touch); // PET_HEAD 재생 중

            // 프리로드 재전송(재서명 URL) 이 상호작용 중에 도착 — 즉시 전환 금지.
            var update = Feed(c, AssetDatagram("BREATHING", "https://s/b_packed.mp4?t=fresh"));
            Assert.AreEqual(MotionPlaybackController.CommandKind.None, update.Kind);
            Assert.AreEqual("PET_HEAD", c.CurrentMotionId, "홈 갱신이 상호작용을 끊으면 안 된다");

            // 복귀는 라이브러리의 **최신** 홈 URL 로 튼다.
            var back = c.OnClipEnded();
            Assert.AreEqual("https://s/b_packed.mp4?t=fresh", back.Asset.Url);
            Assert.IsTrue(back.Loop);
        }

        [Test]
        public void Sensor_BeforeHomeStored_IsIgnored()
        {
            // 복귀 목적지 없이 홈을 떠나지 않는다 — 프리로드는 BREATHING 을
            // 마지막에 보내므로 이 창은 데이터그램 간격만큼만 존재한다.
            var c = new MotionPlaybackController(new MotionLibrary());
            Feed(c, AssetDatagram("PET_HEAD", "https://s/ph_packed.mp4"));
            var cmd = Feed(c, Touch);
            Assert.AreEqual(MotionPlaybackController.CommandKind.None, cmd.Kind);
            Assert.AreEqual("BREATHING", c.CurrentMotionId);
        }

        [Test]
        public void InteractionError_FailsSafeToHome()
        {
            var c = Preloaded();
            Feed(c, Touch); // PET_HEAD 재생 시도 (만료 URL 가정)
            var cmd = c.OnPlaybackError();
            Assert.AreEqual(MotionPlaybackController.CommandKind.Play, cmd.Kind);
            Assert.AreEqual("BREATHING", cmd.Asset.MotionId);
            Assert.IsTrue(cmd.Loop);
            Assert.AreEqual("BREATHING", c.CurrentMotionId);
        }

        [Test]
        public void HomeError_IsNoOp_SameAsBefore()
        {
            var c = Preloaded();
            Assert.AreEqual(MotionPlaybackController.CommandKind.None, c.OnPlaybackError().Kind);
        }

        [Test]
        public void UnknownOrLegacyEvents_DoNotTriggerPlayback()
        {
            var c = Preloaded();
            // pi_reset idle (URL 없음), 구 RUN 목업의 action, 미지 이벤트 — 전부 무시.
            Assert.AreEqual(MotionPlaybackController.CommandKind.None,
                Feed(c, "{\"event\":\"idle\",\"source\":\"pi_reset\"}").Kind);
            Assert.AreEqual(MotionPlaybackController.CommandKind.None,
                Feed(c, "{\"event\":\"action\",\"action_id\":\"RUN\"}").Kind);
            Assert.AreEqual(MotionPlaybackController.CommandKind.None,
                Feed(c, "{\"event\":\"happy\"}").Kind);
            Assert.AreEqual("BREATHING", c.CurrentMotionId);
        }
    }
}
