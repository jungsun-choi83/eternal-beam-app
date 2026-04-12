using UnityEngine;
using UnityEngine.Video;
using UnityEditor;
using EternalBeam;

namespace EternalBeam.Editor
{
    /// <summary>
    /// fix_dog_video.py로 생성한 dog_rgb.mp4, dog_alpha.mp4 테스트용 씬 셋업
    /// 메뉴: EternalBeam > Setup Dog Video Test
    /// </summary>
    public static class SetupDogVideoTest
    {
        [MenuItem("EternalBeam/Setup Dog Video Test")]
        public static void Setup()
        {
            // PetHologram 머티리얼 생성
            var shader = Shader.Find("Custom/PetHologram");
            if (shader == null)
            {
                EditorUtility.DisplayDialog("오류", "Custom/PetHologram 셰이더를 찾을 수 없습니다.\nAssets/Shaders/PetHologram.shader가 있는지 확인하세요.", "확인");
                return;
            }

            var mat = new Material(shader);
            mat.name = "PetHologramMat";
            AssetDatabase.CreateAsset(mat, "Assets/Materials/PetHologramMat.mat");

            // SubjectQuad 생성 (Quad + VideoPlayer + VideoLayer)
            var quad = GameObject.CreatePrimitive(PrimitiveType.Quad);
            quad.name = "SubjectQuad";
            quad.transform.position = new Vector3(0, 0, 5);
            quad.transform.localScale = new Vector3(1.78f, 1f, 1f); // 16:9 비율

            // 기본 머티리얼 제거 후 PetHologram 적용
            var renderer = quad.GetComponent<MeshRenderer>();
            renderer.sharedMaterial = mat;

            // VideoPlayer 추가 (VideoLayer가 RequireComponent로 자동 추가)
            var videoLayer = quad.AddComponent<VideoLayer>();
            videoLayer.material = mat;
            videoLayer.clips = new VideoClip[1];      // Element 0: dog_rgb.mp4
            videoLayer.alphaClips = new VideoClip[1]; // Element 0: dog_alpha.mp4

            // 비디오 클립 자동 찾기 (Assets/Videos/에 있으면)
            var rgbClip = AssetDatabase.LoadAssetAtPath<VideoClip>("Assets/Videos/dog_rgb.mp4");
            var alphaClip = AssetDatabase.LoadAssetAtPath<VideoClip>("Assets/Videos/dog_alpha.mp4");
            if (rgbClip != null) videoLayer.clips[0] = rgbClip;
            if (alphaClip != null) videoLayer.alphaClips[0] = alphaClip;

            // 카메라가 없으면 기본 위치로
            var cam = Camera.main;
            if (cam == null)
            {
                var camObj = new GameObject("Main Camera");
                camObj.tag = "MainCamera";
                cam = camObj.AddComponent<Camera>();
                camObj.AddComponent<AudioListener>();
                cam.transform.position = new Vector3(0, 0, -10);
                cam.clearFlags = CameraClearFlags.SolidColor;
                cam.backgroundColor = new Color(0.1f, 0.1f, 0.15f);
            }

            Selection.activeGameObject = quad;
            EditorUtility.DisplayDialog(
                "Dog Video Test 셋업 완료",
                "SubjectQuad가 생성되었습니다.\n\n" +
                "dog_rgb.mp4, dog_alpha.mp4가 Assets/Videos/에 없다면\n" +
                "Inspector에서 VideoLayer > Clips / Alpha Clips에 수동으로 할당하세요.\n\n" +
                "플레이 모드로 테스트해보세요.",
                "확인"
            );
        }
    }
}
