using System.Collections;
using UnityEngine;
using UnityEngine.Video;

namespace EternalBeam
{
    /// <summary>
    /// RGBA 또는 RGB+Alpha 분리 비디오를 PetShader에 렌더링.
    /// subject_only: 배경 없이 피사체만 (검은 배경 = DLP 투명)
    /// </summary>
    [RequireComponent(typeof(VideoPlayer))]
    public class VideoLayer : MonoBehaviour
    {
        [Header("Video Clips")]
        [Tooltip("RGBA 단일 또는 RGB 클립 (subject_only.mp4 등)")]
        public VideoClip[] clips = new VideoClip[0];
        [Tooltip("Alpha 클립 (RGB+Alpha 분리 시, 선택)")]
        public VideoClip[] alphaClips = new VideoClip[0];

        [Header("Material")]
        public Material material;
        [Tooltip("alphaClips 있으면 자동 1")]
        public bool useAlphaTex = false;

        private VideoPlayer _player;
        private VideoPlayer _alphaPlayer;
        private RenderTexture _renderTexture;
        private RenderTexture _alphaRenderTexture;
        private int _currentIndex;

        private static readonly int MainTexId = Shader.PropertyToID("_MainTex");
        private static readonly int AlphaTexId = Shader.PropertyToID("_AlphaTex");
        private static readonly int UseAlphaTexId = Shader.PropertyToID("_UseAlphaTex");

        private void Awake()
        {
            _player = GetComponent<VideoPlayer>();
            _player.renderMode = VideoRenderMode.RenderTexture;
            _player.isLooping = true;
            _player.playOnAwake = true;
        }

        private void Start()
        {
            if (clips != null && clips.Length > 0)
            {
                useAlphaTex = alphaClips != null && alphaClips.Length > 0 && alphaClips[0] != null;
                SetupRenderTextures();
                PlayClip(0);
            }
        }

        private void OnDestroy()
        {
            ReleaseRenderTextures();
        }

        private void SetupRenderTextures()
        {
            if (clips == null || clips.Length == 0 || clips[0] == null) return;

            int w = (int)clips[0].width;
            int h = (int)clips[0].height;
            if (w <= 0 || h <= 0) { w = 720; h = 960; }

            ReleaseRenderTextures();

            _renderTexture = new RenderTexture(w, h, 0, RenderTextureFormat.ARGB32);
            _renderTexture.Create();
            _player.targetTexture = _renderTexture;

            if (useAlphaTex && alphaClips != null && alphaClips.Length > 0 && alphaClips[0] != null)
            {
                _alphaRenderTexture = new RenderTexture(w, h, 0, RenderTextureFormat.R8);
                _alphaRenderTexture.Create();
                _alphaPlayer = gameObject.AddComponent<VideoPlayer>();
                _alphaPlayer.renderMode = VideoRenderMode.RenderTexture;
                _alphaPlayer.targetTexture = _alphaRenderTexture;
                _alphaPlayer.isLooping = true;
                _alphaPlayer.playOnAwake = true;
            }

            if (material != null)
                material.SetFloat(UseAlphaTexId, useAlphaTex ? 1f : 0f);
        }

        private void ReleaseRenderTextures()
        {
            if (_renderTexture != null) { _renderTexture.Release(); _renderTexture = null; }
            if (_alphaRenderTexture != null) { _alphaRenderTexture.Release(); _alphaRenderTexture = null; }
        }

        public void PlayFromUrl(string rgbUrl, string alphaUrl)
        {
            StartCoroutine(LoadAndPlay(rgbUrl, alphaUrl));
        }

        private IEnumerator LoadAndPlay(string rgbUrl, string alphaUrl)
        {
            _player.Stop();
            if (_alphaPlayer != null) _alphaPlayer.Stop();

            _player.source = VideoSource.Url;
            _player.url = rgbUrl;
            _player.Prepare();

            if (!string.IsNullOrEmpty(alphaUrl))
            {
                if (_alphaPlayer == null)
                {
                    _alphaPlayer = gameObject.AddComponent<VideoPlayer>();
                    _alphaPlayer.renderMode = VideoRenderMode.RenderTexture;
                    _alphaPlayer.isLooping = true;
                    _alphaPlayer.playOnAwake = false;
                }
                _alphaPlayer.source = VideoSource.Url;
                _alphaPlayer.url = alphaUrl;
                _alphaPlayer.Prepare();
            }

            float timeout = 15f;
            while (!_player.isPrepared && timeout > 0f)
            {
                timeout -= Time.deltaTime;
                yield return null;
            }

            if (_player.isPrepared)
            {
                int w = (int)_player.width;
                int h = (int)_player.height;
                if (w <= 0 || h <= 0) { w = 720; h = 960; }
                ReleaseRenderTextures();
                _renderTexture = new RenderTexture(w, h, 0, RenderTextureFormat.ARGB32);
                _renderTexture.Create();
                _player.targetTexture = _renderTexture;

                if (_alphaPlayer != null && !string.IsNullOrEmpty(alphaUrl))
                {
                    float ta = 15f;
                    while (!_alphaPlayer.isPrepared && ta > 0f)
                    {
                        ta -= Time.deltaTime;
                        yield return null;
                    }
                    if (_alphaPlayer.isPrepared)
                    {
                        _alphaRenderTexture = new RenderTexture(w, h, 0, RenderTextureFormat.R8);
                        _alphaRenderTexture.Create();
                        _alphaPlayer.targetTexture = _alphaRenderTexture;
                        useAlphaTex = true;
                        if (material != null) material.SetFloat(UseAlphaTexId, 1f);
                    }
                }
                else
                {
                    useAlphaTex = false;
                    if (material != null) material.SetFloat(UseAlphaTexId, 0f);
                }

                _player.Play();
                if (_alphaPlayer != null && !string.IsNullOrEmpty(alphaUrl) && _alphaPlayer.isPrepared)
                    _alphaPlayer.Play();
                if (material != null)
                {
                    material.SetTexture(MainTexId, _renderTexture);
                    if (useAlphaTex && _alphaRenderTexture != null)
                        material.SetTexture(AlphaTexId, _alphaRenderTexture);
                }
                Debug.Log("URL 영상 로드 성공: " + rgbUrl);
            }
            else
            {
                Debug.LogError("URL 영상 로드 실패: " + rgbUrl);
            }
        }

        public void PlayClip(int index)
        {
            if (clips == null || index < 0 || index >= clips.Length || clips[index] == null) return;

            _currentIndex = index;
            _player.clip = clips[index];
            _player.Play();

            if (useAlphaTex && _alphaPlayer != null && alphaClips != null && index < alphaClips.Length && alphaClips[index] != null)
            {
                _alphaPlayer.clip = alphaClips[index];
                _alphaPlayer.Play();
            }

            if (material != null)
            {
                material.SetTexture(MainTexId, _renderTexture);
                if (useAlphaTex && _alphaRenderTexture != null)
                    material.SetTexture(AlphaTexId, _alphaRenderTexture);
            }
        }

        private void LateUpdate()
        {
            if (_renderTexture != null && material != null)
                material.SetTexture(MainTexId, _renderTexture);
        }
    }
}
