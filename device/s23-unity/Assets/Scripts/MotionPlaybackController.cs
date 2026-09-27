using System;

namespace EternalBeam.Device
{
    /// <summary>
    /// Device M5-lite Phase 2 — 센서 트리거 재생 상태 기계 (순수 C#).
    ///
    ///   BREATHING = 홈 루프 (loop)
    ///   touch → PET_HEAD, voice → LOOK_UP, approach → COME_CLOSER
    ///     → 1회 재생 (no loop) → 끝나면 저장된 BREATHING 으로 자동 복귀
    ///
    /// 판정만 한다 — VideoPlayer 는 만지지 않는다. PetVideoScreen(셸)이
    /// 데이터그램·플레이어 이벤트를 넣고 Command 를 실행한다. 기존 APK 에
    /// 병합할 때도 이 클래스와 MotionLibrary 만 가져가면 규칙이 그대로 온다.
    ///
    /// 규칙 (웹 런타임과 같은 정신, 기기 최소판):
    ///   * 상호작용은 비중단 — 재생 중 새 센서 이벤트는 무시한다
    ///     (웹 decideTrigger 의 ACTION non-interruptible 거울).
    ///   * 요청 모션이 저장 안 됐으면 무시 — 홈이 계속 돈다.
    ///   * 홈(BREATHING)이 저장되기 전에는 상호작용을 틀지 않는다 —
    ///     복귀 목적지 없이 홈을 떠나지 않는다 (프리로드는 BREATHING 을
    ///     마지막에 보내므로 실제 공백은 데이터그램 간격뿐이다).
    ///   * 상호작용 재생 중 도착한 홈 자산은 저장만 하고(교체 지연),
    ///     복귀 시점에 라이브러리의 최신 URL 로 튼다.
    ///   * 재생 오류: 상호작용이면 홈 복귀 시도(만료 URL 페일세이프),
    ///     홈이면 아무것도 안 한다 (기존과 동일 — 다음 데이터그램이 고친다).
    /// </summary>
    public sealed class MotionPlaybackController
    {
        public enum CommandKind { None, Play }

        public sealed class Command
        {
            public CommandKind Kind = CommandKind.None;
            public MotionLibrary.MotionAsset Asset;
            /// <summary>true = 홈 루프, false = 상호작용 1회 재생.</summary>
            public bool Loop;
            /// <summary>로그용 — 왜 이 판정인지 (None 일 때도 채워질 수 있다).</summary>
            public string Reason;

            public static readonly Command None = new Command();

            public static Command Ignore(string reason) =>
                new Command { Kind = CommandKind.None, Reason = reason };

            public static Command Play(MotionLibrary.MotionAsset asset, bool loop, string reason) =>
                new Command { Kind = CommandKind.Play, Asset = asset, Loop = loop, Reason = reason };
        }

        private readonly MotionLibrary _library;
        private string _currentMotionId = MotionLibrary.HomeMotionId;

        public MotionPlaybackController(MotionLibrary library)
        {
            _library = library ?? throw new ArgumentNullException(nameof(library));
        }

        public MotionLibrary Library => _library;

        /// <summary>지금 재생 의도가 걸린 모션 — 홈이면 BREATHING.</summary>
        public string CurrentMotionId => _currentMotionId;

        public bool IsHome => string.Equals(
            _currentMotionId, MotionLibrary.HomeMotionId, StringComparison.Ordinal);

        /// <summary>
        /// 수신 데이터그램 하나 → 실행할 명령. 자산(nfc_match/idle)과 센서
        /// (touch/voice/approach) 를 모두 여기로 넣는다 — 모르는 이벤트는 None.
        /// </summary>
        public Command OnMessage(PetDeviceMessage msg)
        {
            if (msg == null || !msg.Valid) return Command.None;

            string trigger = MotionLibrary.MotionForSensorEvent(msg.Event);
            if (trigger != null) return OnSensorTrigger(trigger);

            var ingest = _library.Ingest(msg);
            if (!ingest.Stored) return Command.None;
            if (!ingest.PlayNow)
            {
                return Command.Ignore(
                    $"motion stored ({ingest.Asset.MotionId}" +
                    $"{(ingest.Replaced ? ", url replaced" : "")})");
            }
            // 홈 자산 도착. 상호작용 재생 중이면 교체를 미룬다 — 복귀가
            // 라이브러리를 다시 읽으므로 새 URL 은 그때 적용된다.
            if (!IsHome)
            {
                return Command.Ignore("home updated during interaction — applied on return");
            }
            return Command.Play(ingest.Asset, loop: true, "home asset");
        }

        private Command OnSensorTrigger(string motionId)
        {
            if (!IsHome)
            {
                return Command.Ignore($"sensor ignored — {_currentMotionId} still playing");
            }
            if (!_library.TryGet(MotionLibrary.HomeMotionId, out _))
            {
                return Command.Ignore($"{motionId} ignored — no home to return to");
            }
            if (!_library.TryGet(motionId, out var asset))
            {
                return Command.Ignore($"{motionId} not stored — keeping home");
            }
            _currentMotionId = asset.MotionId;
            return Command.Play(asset, loop: false, $"sensor → {asset.MotionId} (play once)");
        }

        /// <summary>
        /// 플레이어가 클립 끝(loopPointReached)에 닿았다. 홈은 loop 플래그가
        /// 알아서 이어 돌므로 None — 상호작용이면 저장된 홈으로 복귀한다.
        /// </summary>
        public Command OnClipEnded()
        {
            if (IsHome) return Command.None;
            string finished = _currentMotionId;
            _currentMotionId = MotionLibrary.HomeMotionId;
            if (!_library.TryGet(MotionLibrary.HomeMotionId, out var home))
            {
                // 방어 — OnSensorTrigger 의 홈 선행 조건 때문에 정상 경로에선 없다.
                return Command.Ignore($"{finished} finished — home missing, keeping last frame");
            }
            return Command.Play(home, loop: true, $"{finished} finished → home");
        }

        /// <summary>재생 오류 — 상호작용이면 홈 복귀를 시도한다 (만료 URL 페일세이프).</summary>
        public Command OnPlaybackError()
        {
            if (IsHome) return Command.None;
            string failed = _currentMotionId;
            _currentMotionId = MotionLibrary.HomeMotionId;
            if (!_library.TryGet(MotionLibrary.HomeMotionId, out var home))
            {
                return Command.Ignore($"{failed} errored — home missing");
            }
            return Command.Play(home, loop: true, $"{failed} errored → home");
        }
    }
}
