using UnityEngine;

namespace EternalBeam
{
    /// <summary>
    /// 15cm Z-Depth: 배경 140mm, 피사체 75mm, UI 20mm
    /// subject_only: 배경 레이어 비활성화, 강아지만 표시
    /// </summary>
    public class HologramController : MonoBehaviour
    {
        [Header("Layers (15cm scale)")]
        [Tooltip("배경 Z (140mm)")]
        public float backgroundZ = 14f;
        [Tooltip("피사체 Z (75mm)")]
        public float subjectZ = 7.5f;
        [Tooltip("UI Z (20mm)")]
        public float uiZ = 2f;

        [Header("References")]
        public Transform backgroundLayer;
        public Transform subjectLayer;
        public Transform uiLayer;

        [Header("Subject Only")]
        [Tooltip("true: 배경 없이 피사체만. false: 배경+피사체 합성 (꼬리 흔드는 아이들 + 달려오는 피사체)")]
        public bool subjectOnly = false;

        [Header("Parallax")]
        [Tooltip("배경 움직임 배율 (피사체 대비)")]
        [Range(0f, 1f)]
        public float parallaxMultiplier = 0.4f;

        private void Start()
        {
            ApplyZDepth();
            ApplySubjectOnly();
        }

        private void ApplyZDepth()
        {
            if (backgroundLayer != null)
                backgroundLayer.localPosition = new Vector3(0, 0, backgroundZ);
            if (subjectLayer != null)
                subjectLayer.localPosition = new Vector3(0, 0, subjectZ);
            if (uiLayer != null)
                uiLayer.localPosition = new Vector3(0, 0, uiZ);
        }

        private void ApplySubjectOnly()
        {
            if (backgroundLayer != null)
                backgroundLayer.gameObject.SetActive(!subjectOnly);
        }

        private void OnValidate()
        {
            ApplyZDepth();
            ApplySubjectOnly();
        }
    }
}
