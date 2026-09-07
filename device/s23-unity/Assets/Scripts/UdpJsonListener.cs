using System;
using System.Collections.Concurrent;
using System.Net;
using System.Net.Sockets;
using System.Text;
using System.Threading;
using Newtonsoft.Json.Linq;

namespace EternalBeam.Device
{
    /// <summary>
    /// 순수 UDP JSON 수신기 — Unity API 무의존 (Editor 배치 검증/테스트에서도 그대로 쓴다).
    ///
    /// 계약 (python/pi_sse_server.py + eternal_beam_pi.py 관측치):
    ///   * UDP :5005, 데이터그램 하나 = 컴팩트 JSON 오브젝트 하나.
    ///   * /demo/pet-ready 1회 = "nfc_match" + "idle" 두 데이터그램 (같은 본문).
    ///   * 같은 포트로 센서 이벤트(approach/touch/voice, pi_reset idle)도 온다 —
    ///     모르는 event/키는 조용히 통과시킨다 (수신기가 계약을 좁히면 안 된다).
    /// </summary>
    public sealed class UdpJsonListener : IDisposable
    {
        public const int DefaultPort = 5005;

        private readonly UdpClient _client;
        private readonly Thread _thread;
        private readonly ConcurrentQueue<string> _queue = new ConcurrentQueue<string>();
        private volatile bool _running = true;

        public UdpJsonListener(int port = DefaultPort)
        {
            _client = new UdpClient(port);
            _thread = new Thread(ReceiveLoop) { IsBackground = true, Name = "eb-udp-5005" };
            _thread.Start();
        }

        private void ReceiveLoop()
        {
            var any = new IPEndPoint(IPAddress.Any, 0);
            while (_running)
            {
                try
                {
                    byte[] data = _client.Receive(ref any);
                    _queue.Enqueue(Encoding.UTF8.GetString(data));
                }
                catch (SocketException) { if (_running) Thread.Sleep(50); }
                catch (ObjectDisposedException) { return; }
            }
        }

        /// <summary>큐에서 원문 하나를 꺼낸다 — 호출 스레드는 호출자가 정한다(메인 스레드 권장).</summary>
        public bool TryDequeue(out string raw) => _queue.TryDequeue(out raw);

        /// <summary>
        /// 데이터그램 원문 → 관심 필드 요약. JSON 이 아니어도 던지지 않는다.
        /// 모든 필드는 없으면 null — 선택 필드 관용이 계약이다.
        /// </summary>
        public static PetDeviceMessage Parse(string raw)
        {
            var msg = new PetDeviceMessage { Raw = raw };
            try
            {
                var o = JObject.Parse(raw);
                msg.Valid = true;
                msg.Event = (string)o["event"];
                msg.ContentId = (string)o["content_id"];
                msg.PetId = (string)o["pet_id"];
                msg.MotionId = (string)o["motion_id"];
                msg.VideoUrl = (string)o["video_url"];
                msg.PackedUrl = (string)o["packed_url"];
                msg.DeliveryFormat = (string)o["delivery_format"];
                msg.Source = (string)o["source"];
            }
            catch (Exception)
            {
                msg.Valid = false; // JSON 아님 — 버리지 않고 원문만 남긴다
            }
            return msg;
        }

        public void Dispose()
        {
            _running = false;
            _client.Close();
        }
    }

    /// <summary>수신 요약 — 로깅/디스패치용. 전부 null 허용.</summary>
    public sealed class PetDeviceMessage
    {
        public bool Valid;
        public string Raw;
        public string Event;
        public string ContentId;
        public string PetId;
        public string MotionId;
        public string VideoUrl;
        public string PackedUrl;
        public string DeliveryFormat;
        public string Source;

        public string Summary()
        {
            if (!Valid) return $"[eb-udp] non-JSON datagram ({Raw?.Length ?? 0} bytes)";
            string V(string s) => string.IsNullOrEmpty(s) ? "-" : s;
            return "[eb-udp] event=" + V(Event)
                 + " content_id=" + V(ContentId)
                 + " pet_id=" + V(PetId)
                 + " motion_id=" + V(MotionId)
                 + " video_url=" + V(VideoUrl)
                 + " packed_url=" + V(PackedUrl)
                 + " delivery_format=" + V(DeliveryFormat)
                 + " source=" + V(Source);
        }
    }
}
