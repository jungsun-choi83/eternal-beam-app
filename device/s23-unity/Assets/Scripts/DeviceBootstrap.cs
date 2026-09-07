using UnityEngine;

namespace EternalBeam.Device
{
    /// <summary>
    /// 어떤 씬에서든 Play 진입 시 수신기를 세운다 — 씬 배선에 기대지 않는다.
    /// 키오스크 앱(단일 목적, 단일 씬)에 맞는 구조이고, 씬 YAML 을 손으로
    /// 관리하다 참조가 깨지는 사고를 원천 차단한다.
    /// </summary>
    public static class DeviceBootstrap
    {
        [RuntimeInitializeOnLoadMethod(RuntimeInitializeLoadType.AfterSceneLoad)]
        private static void Init()
        {
            // 키오스크 렌더러는 포커스를 잃어도 절대 멈추면 안 된다 — 멈추면
            // UDP 큐가 쌓이기만 하고(Update 미실행) 화면은 정지한다. Editor 에서도
            // 같은 증상이 난다: 다른 앱으로 전환한 동안 수신이 "사라진 것처럼" 보인다.
            Application.runInBackground = true;
            if (UnityEngine.Object.FindFirstObjectByType<UdpJsonReceiver>() != null) return;
            var go = new GameObject("EternalBeamDevice");
            UnityEngine.Object.DontDestroyOnLoad(go);
            go.AddComponent<UdpJsonReceiver>();
            // Milestone 4 — 구독은 PetVideoScreen.OnEnable 이 스스로 한다.
            // 여기서 이벤트를 배선하면 Play 중 도메인 리로드에 구독이 사라진다
            // (이벤트는 직렬화되지 않는다). OnEnable 은 리로드 후 다시 불린다.
            go.AddComponent<PetVideoScreen>();
        }
    }
}
