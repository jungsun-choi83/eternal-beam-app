using System;
using UnityEngine;

namespace EternalBeam.Device
{
    /// <summary>
    /// Milestone 1 — 기기 JSON 수신 + 로그.
    ///
    /// 백그라운드 스레드는 큐에 넣기만 하고, Unity 작업(로그 포함 이후의 모든
    /// 디스패치)은 Update() 즉 메인 스레드에서만 한다. VideoPlayer / packed
    /// 렌더링은 다음 마일스톤 — 여기서는 절대 하지 않는다.
    /// </summary>
    public sealed class UdpJsonReceiver : MonoBehaviour
    {
        [Tooltip("Pi/브리지가 쏘는 기기 포트 (계약: 5005)")]
        public int port = UdpJsonListener.DefaultPort;

        private UdpJsonListener _listener;

        /// <summary>다음 마일스톤이 붙을 자리 — 파싱된 메시지의 메인 스레드 훅.</summary>
        public event Action<PetDeviceMessage> OnMessage;

        private void OnEnable()
        {
            try
            {
                _listener = new UdpJsonListener(port);
                Debug.Log($"[eb-udp] listening on 0.0.0.0:{port}");
            }
            catch (Exception e)
            {
                // 5005 를 다른 리스너가 물고 있다(설치된 APK 대상 브리지와 병행 등).
                Debug.LogError($"[eb-udp] bind failed on :{port} — {e.Message}");
            }
        }

        private void Update()
        {
            if (_listener == null) return;
            while (_listener.TryDequeue(out var raw))
            {
                var msg = UdpJsonListener.Parse(raw);
                Debug.Log(msg.Summary());
                OnMessage?.Invoke(msg);
            }
        }

        private void OnDisable()
        {
            _listener?.Dispose();
            _listener = null;
        }
    }
}
