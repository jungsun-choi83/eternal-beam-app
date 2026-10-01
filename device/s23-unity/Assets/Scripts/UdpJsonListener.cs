using UnityEngine;
using System;
using System.Net;
using System.Text;
using System.Threading;
using System.Net.Sockets;
using System.Collections.Concurrent;
using Newtonsoft.Json.Linq;

namespace EternalBeam.Device
{
    public sealed class PetDeviceMessage
    {
        public bool Valid;
        public string Raw;
        public string Event;
        public string ContentId;
        public string PetId;
        public string MotionId;
        public string ThemeId;
        public string VideoUrl;
        public string PackedUrl;
        public string DeliveryFormat;
        public string Source;
        public float? Brightness;
        public float? Contrast;
        public float? Saturation;
        public float? Gamma;
        public Color? Tint;
        public float? VfxIntensity;

        public string Summary()
        {
            if (!Valid) return $"[eb-udp] non-JSON datagram ({Raw?.Length ?? 0} bytes)";
            string V(string s) => string.IsNullOrEmpty(s) ? "-" : s;
            return "[eb-udp] event=" + V(Event)
                 + " content_id=" + V(ContentId)
                 + " pet_id=" + V(PetId)
                 + " motion_id=" + V(MotionId)
                 + " theme_id=" + V(ThemeId)
                 + " video_url=" + V(VideoUrl)
                 + " packed_url=" + V(PackedUrl)
                 + " delivery_format=" + V(DeliveryFormat)
                 + " source=" + V(Source);
        }
    }

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

        public bool TryDequeue(out string raw) => _queue.TryDequeue(out raw);

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
                msg.ThemeId = (string)o["theme_id"];
                msg.VideoUrl = (string)o["video_url"];
                msg.PackedUrl = (string)o["packed_url"];
                msg.DeliveryFormat = (string)o["delivery_format"];
                msg.Source = (string)o["source"];
                msg.Brightness = o["brightness"]?.Value<float>();
                msg.Contrast = o["contrast"]?.Value<float>();
                msg.Saturation = o["saturation"]?.Value<float>();
                msg.Gamma = o["gamma"]?.Value<float>();
                var tint = o["tint"] as JArray;
                if (tint != null && tint.Count >= 3)
                {
                    msg.Tint = new Color(
                        tint[0].Value<float>(),
                        tint[1].Value<float>(),
                        tint[2].Value<float>(),
                        tint.Count >= 4 ? tint[3].Value<float>() : 1f
                    );
                }
                msg.VfxIntensity = o["vfx_intensity"]?.Value<float>();
            }
            catch (Exception)
            {
                msg.Valid = false;
            }
            return msg;
        }

        public void Dispose()
        {
            _running = false;
            _client.Close();
        }
    }
}
