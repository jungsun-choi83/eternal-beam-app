using System;
using UnityEngine;

namespace EternalBeam.Device
{
    public sealed class UdpJsonReceiver : MonoBehaviour
    {
        public int port = UdpJsonListener.DefaultPort;

        private UdpJsonListener _listener;
        [SerializeField] private DeviceDebugOverlay debugOverlay;

        public event Action<PetDeviceMessage> OnMessage;

        private void OnEnable()
        {
            try
            {
                _listener = new UdpJsonListener(port);
                Debug.Log($"[eb-udp] listening on 0.0.0.0:{port}");
                debugOverlay?.SetUdpReady();
            }
            catch (Exception e)
            {
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
                debugOverlay?.SetMessage(msg.Event, msg.MotionId, !string.IsNullOrEmpty(msg.PackedUrl) ? msg.PackedUrl : msg.VideoUrl);
            }
        }

        private void OnDisable()
        {
            _listener?.Dispose();
            _listener = null;
        }
    }
}
