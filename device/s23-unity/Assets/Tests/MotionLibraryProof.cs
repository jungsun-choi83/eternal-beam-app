using NUnit.Framework;

namespace EternalBeam.Device.Tests
{
    /// <summary>
    /// M5-lite Phase 1 배선 증명 — 4개 모션(BREATHING/PET_HEAD/LOOK_UP/COME_CLOSER)
    /// 이 서로 덮지 않고 독립 저장되는지, 홈만 즉시 재생인지, 레거시 본문이
    /// 그대로 홈으로 동작하는지를 **실제 수신 파서**(UdpJsonListener.Parse)를
    /// 통과한 데이터그램으로 검증한다. EditMode — 네트워크/재생 없음.
    /// </summary>
    public class MotionLibraryProof
    {
        private const string PetId = "pet_c1";

        private static string Datagram(string motionId, string url, string deliveryFormat = "packed_alpha")
        {
            // 웹 buildPhase7PetReadyBody → Pi build_pet_ready_base 가 만드는 실제 본문 모양.
            return "{\"event\":\"idle\",\"content_id\":\"c1\",\"pet_id\":\"" + PetId + "\"," +
                   "\"motion_id\":\"" + motionId + "\",\"idle_url\":\"" + url + "\"," +
                   "\"video_url\":\"" + url + "\",\"packed_url\":\"" + url + "\"," +
                   (deliveryFormat != null ? "\"delivery_format\":\"" + deliveryFormat + "\"," : "") +
                   "\"source\":\"app_idle_ready\"}";
        }

        private static MotionLibrary.IngestResult Ingest(MotionLibrary lib, string raw)
        {
            return lib.Ingest(UdpJsonListener.Parse(raw));
        }

        [Test]
        public void FourMotions_StoredIndependently_WithoutOverwriting()
        {
            var lib = new MotionLibrary();
            var motions = new[] { "PET_HEAD", "LOOK_UP", "COME_CLOSER", "BREATHING" };
            foreach (var id in motions)
            {
                var r = Ingest(lib, Datagram(id, $"https://s/{id.ToLowerInvariant()}_packed.mp4?t=1"));
                Assert.IsTrue(r.Stored, id);
                Assert.IsFalse(r.Replaced, id);
            }

            Assert.AreEqual(4, lib.Count);
            foreach (var id in motions)
            {
                Assert.IsTrue(lib.TryGet(id, out var asset), id);
                Assert.AreEqual($"https://s/{id.ToLowerInvariant()}_packed.mp4?t=1", asset.Url, id);
                Assert.IsTrue(asset.Packed, id);
            }
        }

        [Test]
        public void OnlyHome_Breathing_RequestsImmediatePlay()
        {
            var lib = new MotionLibrary();
            Assert.IsFalse(Ingest(lib, Datagram("PET_HEAD", "https://s/ph_packed.mp4")).PlayNow);
            Assert.IsFalse(Ingest(lib, Datagram("LOOK_UP", "https://s/lu_packed.mp4")).PlayNow);
            Assert.IsFalse(Ingest(lib, Datagram("COME_CLOSER", "https://s/cc_packed.mp4")).PlayNow);
            Assert.IsTrue(Ingest(lib, Datagram("BREATHING", "https://s/b_packed.mp4")).PlayNow);
        }

        [Test]
        public void LegacyBody_WithoutMotionId_IsStoredAsHome_AndPlays()
        {
            // 레거시 pet-ready(모션 필드 없음) — 기존 단일 슬롯 의미와 동일해야 한다.
            const string legacy = "{\"event\":\"idle\",\"content_id\":\"c1\"," +
                                  "\"idle_url\":\"https://s/legacy.mp4\",\"video_url\":\"https://s/legacy.mp4\"," +
                                  "\"source\":\"app_idle_ready\"}";
            var lib = new MotionLibrary();
            var r = Ingest(lib, legacy);
            Assert.IsTrue(r.Stored);
            Assert.IsTrue(r.PlayNow, "모션 필드 없는 본문은 홈 루프다");
            Assert.IsTrue(lib.TryGet("BREATHING", out var asset));
            Assert.AreEqual("https://s/legacy.mp4", asset.Url);
            Assert.IsFalse(asset.Packed, "packed 명시도 _packed.mp4 파일명도 없다");
        }

        [Test]
        public void Reingest_SameMotion_ReplacesUrl_LeavesOthersUntouched()
        {
            var lib = new MotionLibrary();
            Ingest(lib, Datagram("PET_HEAD", "https://s/ph_packed.mp4?t=1"));
            Ingest(lib, Datagram("BREATHING", "https://s/b_packed.mp4?t=1"));

            // 발견 재서명으로 URL 이 갱신되는 경로 — 같은 모션만 교체된다.
            var r = Ingest(lib, Datagram("PET_HEAD", "https://s/ph_packed.mp4?t=2"));
            Assert.IsTrue(r.Stored);
            Assert.IsTrue(r.Replaced);
            Assert.AreEqual(2, lib.Count);
            Assert.IsTrue(lib.TryGet("PET_HEAD", out var petHead));
            Assert.AreEqual("https://s/ph_packed.mp4?t=2", petHead.Url);
            Assert.IsTrue(lib.TryGet("BREATHING", out var home));
            Assert.AreEqual("https://s/b_packed.mp4?t=1", home.Url, "다른 모션이 덮이면 안 된다");
        }

        [Test]
        public void NonPlayableOrUrlLess_Datagrams_AreNotStored()
        {
            var lib = new MotionLibrary();
            // 센서 이벤트 — 자산이 아니다.
            Assert.IsFalse(Ingest(lib, "{\"event\":\"touch\",\"distance_mm\":85}").Stored);
            // pi_reset — URL 없는 idle.
            Assert.IsFalse(Ingest(lib, "{\"event\":\"idle\",\"source\":\"pi_reset\"}").Stored);
            // video_url 없는 본문 — 송신 계약 위반 (구형 빌드가 못 읽는다).
            Assert.IsFalse(
                Ingest(lib, "{\"event\":\"idle\",\"motion_id\":\"PET_HEAD\",\"packed_url\":\"https://s/p_packed.mp4\"}").Stored);
            // JSON 아님.
            Assert.IsFalse(Ingest(lib, "not json").Stored);
            Assert.AreEqual(0, lib.Count);
        }

        [Test]
        public void PackedDetermination_ExplicitFormatFirst_FilenameFallback()
        {
            var lib = new MotionLibrary();
            // 명시 packed_alpha — 파일명과 무관하게 packed.
            Ingest(lib, Datagram("PET_HEAD", "https://s/no_suffix.mp4", "packed_alpha"));
            Assert.IsTrue(lib.TryGet("PET_HEAD", out var explicitPacked));
            Assert.IsTrue(explicitPacked.Packed);
            // 명시가 다른 값이면 파일명이 _packed 여도 packed 가 아니다.
            Ingest(lib, Datagram("LOOK_UP", "https://s/lu_packed.mp4", "baked"));
            Assert.IsTrue(lib.TryGet("LOOK_UP", out var explicitOther));
            Assert.IsFalse(explicitOther.Packed);
            // 명시 없음 — 파일명 폴백.
            Ingest(lib, Datagram("COME_CLOSER", "https://s/cc_packed.mp4", null));
            Assert.IsTrue(lib.TryGet("COME_CLOSER", out var fallback));
            Assert.IsTrue(fallback.Packed);
        }

        [Test]
        public void MotionId_Lookup_IsCaseInsensitive_AndNormalized()
        {
            var lib = new MotionLibrary();
            Ingest(lib, "{\"event\":\"idle\",\"motion_id\":\" pet_head \"," +
                        "\"video_url\":\"https://s/ph_packed.mp4\"}");
            Assert.IsTrue(lib.TryGet("PET_HEAD", out var asset));
            Assert.AreEqual("PET_HEAD", asset.MotionId);
        }
    }
}
