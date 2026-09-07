using System;
using System.Collections;
using UnityEngine;
using UnityEngine.Video;

namespace EternalBeam.Device
{
    /// <summary>
    /// Milestone 3 — packed-alpha 렌더링.
    ///
    ///   VideoPlayer(1개 — RGB/매트 동기화 보장)
    ///     → 소스 크기 그대로의 RenderTexture (720×2560 vstack, 크롭/재인코딩 없음)
    ///     → PetHologram.shader (상단 절반 = RGB, 하단 절반 = 알파)
    ///     → 세로 9:16 월드 쿼드 = 투명 펫 1마리
    ///     → 뒤의 배경색이 매트 검정(투명) 영역으로 비친다
    ///
    /// packed 판정: **delivery_format="packed_alpha" 명시가 1순위**, 파일명
    /// (`_packed.mp4`, VideoLayer.IsPackedAlphaUrl)은 명시가 없을 때의 폴백이다.
    /// 비-packed 소스는 셰이더의 기존 휘도 키 모드(레거시 blackkey)로 그린다.
    /// </summary>
    public sealed class PetVideoScreen : MonoBehaviour
    {
        private static readonly string[] PlayableEvents = { "nfc_match", "idle" };

        //: 투명 증명용 테스트 배경색 — 펫 영상 어디에도 없는 선명한 초록.
        private static readonly Color TestBackground = new Color(0.10f, 0.65f, 0.25f, 1f);

        private static readonly int MainTexId = Shader.PropertyToID("_MainTex");
        private static readonly int PackedAlphaId = Shader.PropertyToID("_PackedAlpha");
        private static readonly int PackedRgbOnTopId = Shader.PropertyToID("_PackedRgbOnTop");
        private static readonly int PremulId = Shader.PropertyToID("_PremulRGB");
        private static readonly int UseAlphaTexId = Shader.PropertyToID("_UseAlphaTex");
        private static readonly int BrightnessId = Shader.PropertyToID("_Brightness");
        private static readonly int RimIntensityId = Shader.PropertyToID("_RimIntensity");
        private static readonly int TintStrengthId = Shader.PropertyToID("_TintStrength");

        private VideoPlayer _player;
        private RenderTexture _rt;
        private Material _material;
        private Camera _camera;
        private Transform _quad;
        private string _currentUrl;
        private bool _packed;

        public string LastState { get; private set; } = "idle";
        public bool IsPlaying => _player != null && _player.isPlaying;
        public double PlaybackTime => _player != null ? _player.time : -1;
        public long LoopCount { get; private set; }
        public bool PackedMode => _packed;
        public Camera OutputCamera => _camera;
        /// <summary>디코딩 표면(소스 크기 vstack). Milestone2 검증 테스트가 읽는다.</summary>
        public RenderTexture Output => _rt;

        // Milestone 4 — 전체 프레임 프로브 결과 (테스트가 읽는다).
        public bool ProbeCompleted { get; private set; }
        public bool ProbeCornersBackground { get; private set; }
        public bool ProbeBottomMatteGhost { get; private set; }
        /// <summary>배경색과 다른 픽셀 수 = 화면에 실제로 보이는 펫 픽셀.</summary>
        public int ProbePetPixels { get; private set; }

        private UdpJsonReceiver _receiver;

        /// <summary>
        /// 수신 구독은 스크린이 스스로 관리한다 — OnEnable 은 도메인 리로드
        /// 후에도 다시 불리므로 구독이 자가 복구된다. (M4 라이브에서 확인:
        /// Play 중 리컴파일 → DeviceBootstrap 배선(이벤트, 비직렬화)이 사라져
        /// 데이터그램이 수신 로그까지만 오고 재생에 닿지 않았다.)
        /// </summary>
        private void OnEnable()
        {
            _receiver = GetComponent<UdpJsonReceiver>()
                        ?? UnityEngine.Object.FindFirstObjectByType<UdpJsonReceiver>();
            if (_receiver != null) _receiver.OnMessage += HandleMessage;

            if (_player != null)
            {
                HookPlayerEvents();
                // 리로드 복구: prepareCompleted 가 리로드 사이에 허공으로 사라졌을
                // 수 있다 (M4 라이브에서 확인 — "preparing" 에서 영구 정지).
                // 리로드를 관통한 플레이어는 내부적으로 낀 상태일 수 있어 맨
                // Prepare 재호출로는 안 풀린다(역시 라이브 확인) — Stop 포함
                // 전체 사이클(SwitchTo)로 처음부터 다시 돈다.
                if (!string.IsNullOrEmpty(_currentUrl) && !_player.isPlaying)
                {
                    Debug.Log("[eb-video] reload recovery — full restart of current url");
                    string url = _currentUrl;
                    _currentUrl = null; // 동일-URL 가드 통과
                    SwitchTo(url, _packed);
                }
            }
        }

        private void OnDisable()
        {
            if (_receiver != null) _receiver.OnMessage -= HandleMessage;
            _receiver = null;
            if (_player != null) UnhookPlayerEvents();
        }

        /// <summary>-= 후 += — 어떤 경로로 두 번 불려도 구독은 정확히 1회.</summary>
        private void HookPlayerEvents()
        {
            UnhookPlayerEvents();
            _player.prepareCompleted += OnPrepared;
            _player.started += OnStarted;
            _player.errorReceived += OnError;
            _player.loopPointReached += OnLoop;
        }

        private void UnhookPlayerEvents()
        {
            _player.prepareCompleted -= OnPrepared;
            _player.started -= OnStarted;
            _player.errorReceived -= OnError;
            _player.loopPointReached -= OnLoop;
        }

        private void Awake()
        {
            _player = gameObject.AddComponent<VideoPlayer>();
            _player.playOnAwake = false;
            _player.renderMode = VideoRenderMode.RenderTexture;
            _player.audioOutputMode = VideoAudioOutputMode.None;
            _player.isLooping = true;
            // 이벤트 구독은 OnEnable(HookPlayerEvents)이 한다 — Awake 에서 걸면
            // 도메인 리로드에 델리게이트가 사라진 채 재구독 기회가 없다.
            HookPlayerEvents();

            BuildStage();
        }

        /// <summary>카메라(테스트 배경색) + 9:16 펫 쿼드 + PetHologram 머티리얼.</summary>
        private void BuildStage()
        {
            var camGo = new GameObject("eb-camera");
            camGo.transform.SetParent(transform, false);
            camGo.transform.position = new Vector3(0, 0, -10);
            _camera = camGo.AddComponent<Camera>();
            _camera.orthographic = true;
            _camera.orthographicSize = 8f; // 쿼드 높이 16 유닛이 화면 세로를 꽉 채운다
            _camera.clearFlags = CameraClearFlags.SolidColor;
            _camera.backgroundColor = TestBackground;

            var shader = Shader.Find("Custom/PetHologram");
            if (shader == null)
            {
                Debug.LogError("[eb-video] Custom/PetHologram 셰이더를 찾지 못했다 — URP 구성 확인");
                return;
            }
            // PetMat.mat 의 속성 계약을 코드로 재현한다 — 구 폴더의 .mat 은 셰이더
            // .meta(GUID)가 레포에 없어 파일 참조로는 복원할 수 없다.
            _material = new Material(shader);
            _material.SetFloat(PackedRgbOnTopId, 1f);   // 우리 포장 계약: RGB 상단
            _material.SetFloat(UseAlphaTexId, 0f);      // 분리 알파 스트림 없음
            _material.SetFloat(BrightnessId, 1f);       // 검증은 원색 그대로
            _material.SetFloat(RimIntensityId, 0f);
            _material.SetFloat(TintStrengthId, 0f);

            var quadGo = GameObject.CreatePrimitive(PrimitiveType.Quad);
            quadGo.name = "eb-pet-quad";
            quadGo.transform.SetParent(transform, false);
            UnityEngine.Object.Destroy(quadGo.GetComponent<Collider>());
            // 최종 표시면은 9:16 — 720×2560 vstack 이 아니라 콘텐츠 비율이다.
            quadGo.transform.localScale = new Vector3(9f, 16f, 1f);
            quadGo.GetComponent<MeshRenderer>().material = _material;
            _quad = quadGo.transform;
        }

        public void HandleMessage(PetDeviceMessage msg)
        {
            if (msg == null || !msg.Valid) return;
            if (Array.IndexOf(PlayableEvents, msg.Event) < 0) return;
            if (string.IsNullOrEmpty(msg.VideoUrl)) return;

            // packed 판정 — 명시 delivery_format 우선, 파일명은 폴백 (Phase 7I 규칙).
            string url = string.IsNullOrEmpty(msg.PackedUrl) ? msg.VideoUrl : msg.PackedUrl;
            bool packed = msg.DeliveryFormat == "packed_alpha"
                          || (string.IsNullOrEmpty(msg.DeliveryFormat)
                              && VideoLayer.IsPackedAlphaUrl(url));
            SwitchTo(url, packed);
        }

        public void SwitchTo(string url, bool packed)
        {
            // 같은 URL 은 재생 중이든 준비 중이든 무시한다 — /demo/pet-ready 1회는
            // nfc_match + idle 두 데이터그램(동일 본문)이라, prepare 진행 중 재진입이
            // 표준 경로다 (M4 라이브 로그에서 prepare 2회로 확인됨).
            bool failed = LastState.StartsWith("error");
            if (url == _currentUrl && packed == _packed && !failed)
            {
                Debug.Log($"[eb-video] same url — keep ({LastState})");
                return;
            }
            if (_currentUrl != null)
                Debug.Log($"[eb-video] url replaced: {Trim(_currentUrl)} → {Trim(url)}");
            StopAllCoroutines(); // 이전 클립의 프로브가 새 클립을 오판하지 않게
            ProbeCompleted = false;
            _currentUrl = url;
            _packed = packed;
            LastState = "preparing";
            LoopCount = 0;
            _player.Stop();
            _player.source = VideoSource.Url;
            _player.url = url;
            Debug.Log($"[eb-video] prepare ({(packed ? "packed_alpha" : "plain")}): {Trim(url)}");
            _player.Prepare();
        }

        private void OnPrepared(VideoPlayer vp)
        {
            LastState = "prepared";
            int w = (int)vp.width, h = (int)vp.height;
            Debug.Log($"[eb-video] prepared: {w}x{h} {vp.frameCount}f @{vp.frameRate:F1}fps packed={_packed}");

            // 소스 크기 그대로 — 크롭/스케일 없음. 절반 샘플링은 셰이더가 한다.
            if (_rt == null || _rt.width != w || _rt.height != h)
            {
                if (_rt != null) _rt.Release();
                _rt = new RenderTexture(w, h, 0, RenderTextureFormat.ARGB32) { name = "eb-pet-rt" };
                _rt.Create();
            }
            vp.targetTexture = _rt;

            if (_material != null)
            {
                _material.SetTexture(MainTexId, _rt);
                // 모드 플래그는 매 클립 시작에 확정한다 (VideoLayer 와 같은 규칙).
                _material.SetFloat(PackedAlphaId, _packed ? 1f : 0f);
                // 포장 계약: packed RGB 는 premultiplied — 셰이더가 straight 복원.
                _material.SetFloat(PremulId, _packed ? 1f : 0f);
            }
            vp.Play();
        }

        private void OnStarted(VideoPlayer vp)
        {
            LastState = "playing";
            Debug.Log("[eb-video] started");
            StartCoroutine(TransparencyProbe());
        }

        /// <summary>
        /// 재생 3초 뒤 카메라 출력 **전체**를 스캔해 투명 합성을 결정적으로 증명한다.
        /// M3 의 고정 지점 프로브는 "펫이 그 지점에 없다"와 "펫이 아예 안 그려졌다"를
        /// 구분하지 못했다 (실자산 분석: 펫 bbox x 0.30-0.99 — 중앙 열이 비어 있었다).
        ///   * 네 모서리 = 배경색 → 매트 검정 영역이 투명하게 뚫렸다
        ///   * 배경색과 다른 픽셀 수 > 0 → 펫이 실제로 화면에 보인다
        ///   * 하단 절반에 흰 매트 복제 없음 → vstack 이중상 부재
        /// </summary>
        private IEnumerator TransparencyProbe()
        {
            yield return new WaitForSecondsRealtime(3f);
            if (_camera == null || !IsPlaying) yield break;

            var probe = new RenderTexture(360, 640, 24);
            var prevTarget = _camera.targetTexture;
            _camera.targetTexture = probe;
            _camera.Render();
            _camera.targetTexture = prevTarget;

            var tex = new Texture2D(probe.width, probe.height, TextureFormat.RGBA32, false);
            var prevActive = RenderTexture.active;
            RenderTexture.active = probe;
            tex.ReadPixels(new Rect(0, 0, probe.width, probe.height), 0, 0);
            tex.Apply();
            RenderTexture.active = prevActive;
            probe.Release();

            int w = tex.width, h = tex.height;
            Color32[] px = tex.GetPixels32();
            var bg = (Color32)TestBackground;
            int petPixels = 0, mattePixels = 0;
            int minX = w, maxX = -1, minY = h, maxY = -1;
            for (int y = 0; y < h; y += 2)
            {
                int row = y * w;
                for (int x = 0; x < w; x += 2)
                {
                    Color32 c = px[row + x];
                    int d = Math.Abs(c.r - bg.r) + Math.Abs(c.g - bg.g) + Math.Abs(c.b - bg.b);
                    if (d <= 38) continue; // 배경(≈0.15 float 허용치와 동일)
                    petPixels++;
                    if (x < minX) minX = x;
                    if (x > maxX) maxX = x;
                    if (y < minY) minY = y;
                    if (y > maxY) maxY = y;
                    // 흰 매트 유령: 하단 절반의 고휘도 무채색 픽셀.
                    if (y < h / 2 && c.r > 216 && Math.Abs(c.r - c.g) < 13 && Math.Abs(c.g - c.b) < 13)
                        mattePixels++;
                }
            }
            Color tl = tex.GetPixel(8, h - 8);
            Color tr = tex.GetPixel(w - 8, h - 8);
            Color bl = tex.GetPixel(8, 8);
            Color br = tex.GetPixel(w - 8, 8);
            ProbeCornersBackground = ColorClose(tl, TestBackground) && ColorClose(tr, TestBackground)
                                  && ColorClose(bl, TestBackground) && ColorClose(br, TestBackground);
            // 임계 300(스캔 표본 ~57k 중): 압축 노이즈는 걸러내고 실제 펫은 수천 픽셀이다.
            ProbePetPixels = petPixels;
            ProbeBottomMatteGhost = mattePixels > 300;
            ProbeCompleted = true;
            string bbox = maxX >= 0
                ? $"bbox=({(float)minX / w:F2},{(float)minY / h:F2})-({(float)maxX / w:F2},{(float)maxY / h:F2})"
                : "bbox=none";
            Debug.Log(
                $"[eb-video] transparency probe v2 — corners=bg:{ProbeCornersBackground} " +
                $"petPixels:{petPixels} {bbox} matteGhost:{ProbeBottomMatteGhost} " +
                $"verdict:{(ProbeCornersBackground && petPixels > 300 && !ProbeBottomMatteGhost ? "TRANSPARENT-PET-OK" : "CHECK")}");
            UnityEngine.Object.Destroy(tex);
        }

        private static bool ColorClose(Color a, Color b) =>
            Mathf.Abs(a.r - b.r) + Mathf.Abs(a.g - b.g) + Mathf.Abs(a.b - b.b) < 0.15f;

        private void OnLoop(VideoPlayer vp)
        {
            LoopCount++;
            if (LoopCount == 1) Debug.Log("[eb-video] loop point reached — looping OK");
        }

        private void OnError(VideoPlayer vp, string message)
        {
            LastState = "error: " + message;
            Debug.LogError($"[eb-video] error: {message}");
        }

        private static string Trim(string url)
        {
            if (string.IsNullOrEmpty(url)) return "-";
            int q = url.IndexOf('?');
            return q > 0 ? url.Substring(0, q) + "?…" : url;
        }

        private void OnDestroy()
        {
            if (_rt != null) _rt.Release();
        }
    }
}
