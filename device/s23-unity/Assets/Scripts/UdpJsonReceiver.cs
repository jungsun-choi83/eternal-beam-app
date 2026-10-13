using System;
using UnityEngine;

namespace EternalBeam.Device
{
    public sealed class UdpJsonReceiver : MonoBehaviour
    {
        public event Action<PetDeviceMessage> OnMessage;

        private UdpJsonListener _listener;

        public NetworkSettings networkSettings;
        public DeviceDebugOverlay debugOverlay;

        private void OnEnable()
        {
            try
            {
                _listener = new UdpJsonListener(networkSettings.udpPort);
                Debug.Log($"[eb-udp] listening on 0.0.0.0:{networkSettings.udpPort}");
                debugOverlay?.SetUdpReady();
            }
            catch (Exception e)
            {
                Debug.LogError($"[eb-udp] bind failed on :{networkSettings.udpPort} — {e.Message}");
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
