using UnityEngine;
using UnityEngine.Video;

namespace EternalBeam
{
    /// <summary>
    /// 메인 피사체를 비디오 대신 <b>누끼 PNG(RGBA)</b>로 고정할 때 사용합니다.
    /// 유령처럼 번지는 느낌이 비디오/압축 때문일 때, 정지 텍스처로 윤곽·알파를 확인해 보세요.
    /// <see cref="VideoLayer"/>와 같은 Quad에 두면, 실행 시 VideoPlayer를 끄고 _MainTex만 갱신합니다.
    /// </summary>
    [ExecuteAlways]
    [DisallowMultipleComponent]
    public class StaticSubjectLayer : MonoBehaviour
    {
        [Header("Texture")]
        [Tooltip("RGBA PNG 권장. 알파는 PetShader에서 col.a로 사용 (_UseAlphaTex=0).")]
        public Texture2D subjectTexture;

        [Header("Material (EternalBeam/PetShader)")]
        public Material targetMaterial;

        [Tooltip("플레이 시작 시 자동 적용")]
        public bool applyOnAwake = true;

        [Tooltip("PNG만 쓸 때 VideoPlayer/VideoLayer 갱신을 멈춤 (LateUpdate 덮어쓰기 방지)")]
        public bool disableVideoWhenUsingTexture = true;

        private static readonly int MainTexId = Shader.PropertyToID("_MainTex");
        private static readonly int UseAlphaTexId = Shader.PropertyToID("_UseAlphaTex");

        private void Awake()
        {
            if (applyOnAwake && Application.isPlaying)
                Apply();
        }

        private void OnValidate()
        {
#if UNITY_EDITOR
            if (!Application.isPlaying && subjectTexture != null && targetMaterial != null)
                ApplyInternal();
#endif
        }

        [ContextMenu("Apply Static Texture")]
        public void Apply()
        {
            ApplyInternal();
        }

        private void ApplyInternal()
        {
            if (targetMaterial == null || subjectTexture == null)
                return;

            targetMaterial.SetTexture(MainTexId, subjectTexture);
            targetMaterial.SetFloat(UseAlphaTexId, 0f);

            if (!disableVideoWhenUsingTexture || !Application.isPlaying)
                return;

            var vp = GetComponent<VideoPlayer>();
            if (vp != null)
            {
                vp.Stop();
                vp.enabled = false;
            }

            var vl = GetComponent<VideoLayer>();
            if (vl != null)
                vl.enabled = false;
        }
    }
}
