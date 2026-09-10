using System;
using System.Collections.Generic;

namespace EternalBeam.Device
{
    /// <summary>
    /// Device M5-lite Phase 1 — motion_id 별 자산 저장소.
    ///
    /// 지금까지 PetVideoScreen 은 모든 재생 가능 데이터그램을 "단일 현재 영상"
    /// 으로 취급했다 — 새 URL 이 오면 이전 것을 덮었다. 이 클래스는 같은
    /// 데이터그램을 motion_id 별 사전에 저장해 서로 덮지 않게 하고, 재생
    /// 판단(지금 틀까?)을 호출자에게 돌려준다.
    ///
    /// 계약:
    ///   * 저장 대상은 기존 재생 규칙 그대로다 — event ∈ {nfc_match, idle},
    ///     video_url 필수 (packed_url 만 있는 본문은 저장하지 않는다: 송신자는
    ///     항상 video_url 을 함께 싣는다, buildPetReadyBody 참조).
    ///   * motion_id 가 없으면 BREATHING — 레거시 송신자(모션 필드가 없는
    ///     pet-ready)는 전부 "지금 트는 홈 루프"를 보냈으므로 의미가 같다.
    ///   * PlayNow 는 BREATHING(홈)만 true — 다른 모션은 저장만 한다. 센서
    ///     트리거 재생은 Phase 2 의 명시 작업이다.
    ///   * packed 판정은 기존 규칙 그대로: 명시 delivery_format="packed_alpha"
    ///     우선, 파일명(_packed.mp4)은 명시가 없을 때의 폴백 (Phase 7I 규칙).
    /// </summary>
    public sealed class MotionLibrary
    {
        public const string HomeMotionId = "BREATHING";

        private static readonly string[] PlayableEvents = { "nfc_match", "idle" };

        public sealed class MotionAsset
        {
            public string MotionId;
            public string Url;
            public bool Packed;
            public string DeliveryFormat;
        }

        public sealed class IngestResult
        {
            /// <summary>사전에 저장됐다 (재생 가능 이벤트 + URL 있음).</summary>
            public bool Stored;
            /// <summary>홈(BREATHING) 자산 — 지금 재생을 전환해야 한다.</summary>
            public bool PlayNow;
            /// <summary>같은 모션의 기존 자산을 **다른** URL 로 교체했다.</summary>
            public bool Replaced;
            public MotionAsset Asset;
        }

        private readonly Dictionary<string, MotionAsset> _assets =
            new Dictionary<string, MotionAsset>(StringComparer.OrdinalIgnoreCase);

        public int Count => _assets.Count;

        public IEnumerable<string> MotionIds => _assets.Keys;

        public bool TryGet(string motionId, out MotionAsset asset)
        {
            if (string.IsNullOrEmpty(motionId))
            {
                asset = null;
                return false;
            }
            return _assets.TryGetValue(motionId.Trim(), out asset);
        }

        /// <summary>
        /// Pi 센서 이벤트 → 재생할 모션 (Phase 2). 웹 sensorEventToRuntimeEvent
        /// (lib/pet-runtime-events)의 기기 거울이다 — 두 매핑이 갈라지면 같은
        /// 터치가 웹과 기기에서 다른 모션을 튼다. 모르는 이벤트는 null.
        /// </summary>
        public static string MotionForSensorEvent(string sensorEvent)
        {
            switch ((sensorEvent ?? "").Trim().ToLowerInvariant())
            {
                case "touch": return "PET_HEAD";
                case "voice": return "LOOK_UP";
                case "approach": return "COME_CLOSER";
                default: return null;
            }
        }

        /// <summary>packed 판정 — 명시 delivery_format 우선, 파일명은 폴백 (Phase 7I 규칙).</summary>
        public static bool IsPacked(string url, string deliveryFormat)
        {
            return deliveryFormat == "packed_alpha"
                   || (string.IsNullOrEmpty(deliveryFormat) && VideoLayer.IsPackedAlphaUrl(url));
        }

        /// <summary>
        /// 수신 데이터그램 하나를 저장한다. 저장하지 않는 경우에도 던지지 않고
        /// Stored=false 를 돌려준다 — 수신기는 모르는 이벤트를 조용히 통과시킨다.
        /// </summary>
        public IngestResult Ingest(PetDeviceMessage msg)
        {
            var result = new IngestResult();
            if (msg == null || !msg.Valid) return result;
            if (Array.IndexOf(PlayableEvents, msg.Event) < 0) return result;
            if (string.IsNullOrEmpty(msg.VideoUrl)) return result;

            // 재생 URL 선택 — packed 명시 우선, 없으면 구형 호환 키 (기존 규칙).
            string url = string.IsNullOrEmpty(msg.PackedUrl) ? msg.VideoUrl : msg.PackedUrl;
            string motionId = string.IsNullOrEmpty(msg.MotionId)
                ? HomeMotionId
                : msg.MotionId.Trim().ToUpperInvariant();

            var asset = new MotionAsset
            {
                MotionId = motionId,
                Url = url,
                Packed = IsPacked(url, msg.DeliveryFormat),
                DeliveryFormat = msg.DeliveryFormat,
            };
            result.Replaced = _assets.TryGetValue(motionId, out var prev) && prev.Url != url;
            _assets[motionId] = asset;
            result.Stored = true;
            result.PlayNow = string.Equals(motionId, HomeMotionId, StringComparison.Ordinal);
            result.Asset = asset;
            return result;
        }
    }
}
