using System;
using System.IO;
using System.Threading;
using UnityEditor;
using UnityEngine;

namespace EternalBeam.Device.EditorTools
{
    /// <summary>
    /// Milestone 1 배치 검증 — Unity 프로세스가 실제 브리지의 UDP 를 받는가.
    ///
    /// 실행:
    ///   Unity -batchmode -projectPath device/s23-unity \
    ///         -executeMethod EternalBeam.Device.EditorTools.Milestone1Proof.Run -logFile m1.log
    ///
    /// 절차: :5005 바인드 → 마커 파일(m1-listening.marker) 생성 → 외부에서
    /// curl 로 /demo/pet-ready POST → 수신 데이터그램을 수신기와 동일한
    /// 경로(UdpJsonListener.Parse + Summary)로 로그 → BREATHING+packed_alpha 를
    /// 실은 nfc_match/idle 두 이벤트를 확인하면 exit 0.
    /// </summary>
    public static class Milestone1Proof
    {
        public static void Run()
        {
            string marker = Path.Combine(Directory.GetCurrentDirectory(), "m1-listening.marker");
            int timeoutSec = int.TryParse(Environment.GetEnvironmentVariable("M1_TIMEOUT_SEC"), out var t) ? t : 90;

            UdpJsonListener listener;
            try
            {
                listener = new UdpJsonListener();
            }
            catch (Exception e)
            {
                Debug.LogError($"[m1] bind :5005 failed — {e.Message}");
                EditorApplication.Exit(2);
                return;
            }

            File.WriteAllText(marker, DateTime.UtcNow.ToString("o"));
            Debug.Log("[m1] listening on :5005 — marker written, waiting for bridge datagrams");

            bool sawNfcMatch = false, sawIdle = false;
            var deadline = DateTime.UtcNow.AddSeconds(timeoutSec);
            while (DateTime.UtcNow < deadline && !(sawNfcMatch && sawIdle))
            {
                while (listener.TryDequeue(out var raw))
                {
                    var msg = UdpJsonListener.Parse(raw);
                    Debug.Log(msg.Summary());
                    Debug.Log("[m1-raw] " + raw);
                    bool breathingPacked =
                        msg.Valid
                        && msg.MotionId == "BREATHING"
                        && msg.DeliveryFormat == "packed_alpha"
                        && !string.IsNullOrEmpty(msg.PackedUrl);
                    if (breathingPacked && msg.Event == "nfc_match") sawNfcMatch = true;
                    if (breathingPacked && msg.Event == "idle") sawIdle = true;
                }
                Thread.Sleep(50);
            }

            listener.Dispose();
            try { File.Delete(marker); } catch { /* 무시 */ }

            Debug.Log($"[m1] result: nfc_match={sawNfcMatch} idle={sawIdle}");
            EditorApplication.Exit(sawNfcMatch && sawIdle ? 0 : 1);
        }
    }
}
