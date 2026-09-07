using UnityEditor;
using UnityEngine;
using UnityEngine.Rendering;
using UnityEngine.Rendering.Universal;

namespace EternalBeam.Device.EditorTools
{
    /// <summary>
    /// URP 파이프라인 자산 자동 구성 — PetHologram.shader 는 URP 전용이라
    /// ("RenderPipeline"="UniversalPipeline") 파이프라인 자산이 지정되지 않으면
    /// 마젠타/미표시가 된다. 손으로 만든 프로젝트라 자산이 없으므로 최초 1회
    /// 코드로 만들어 GraphicsSettings 에 지정한다.
    /// </summary>
    [InitializeOnLoad]
    public static class UrpAutoSetup
    {
        static UrpAutoSetup()
        {
            EditorApplication.delayCall += Ensure;
        }

        private static void Ensure()
        {
            if (GraphicsSettings.defaultRenderPipeline is UniversalRenderPipelineAsset) return;

            const string dir = "Assets/Settings";
            if (!AssetDatabase.IsValidFolder(dir)) AssetDatabase.CreateFolder("Assets", "Settings");

            var rendererData = ScriptableObject.CreateInstance<UniversalRendererData>();
            AssetDatabase.CreateAsset(rendererData, dir + "/EB_URP_Renderer.asset");
            var pipeline = UniversalRenderPipelineAsset.Create(rendererData);
            AssetDatabase.CreateAsset(pipeline, dir + "/EB_URP_Pipeline.asset");

            GraphicsSettings.defaultRenderPipeline = pipeline;
            QualitySettings.renderPipeline = pipeline;
            AssetDatabase.SaveAssets();
            Debug.Log("[eb-urp] URP pipeline asset created and assigned (EB_URP_Pipeline)");
        }
    }
}
