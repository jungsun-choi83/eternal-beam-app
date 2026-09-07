using UnityEngine;

namespace EternalBeam
{
    /// <summary>
    /// 서버(idle_video_url / action_video_url 등)에서 받은 주소를 VideoLayer에 전달.
    /// </summary>
    public class PythonBridge : MonoBehaviour
    {
        [SerializeField] private VideoLayer videoLayer;

        /// <summary>RGBA 단일 스트림 URL만 있을 때 (alpha는 빈 문자열).</summary>
        public void OnPetVideoUrlReceived(string petVideoUrl, string alphaVideoUrl = null)
        {
            if (videoLayer == null)
                videoLayer = FindObjectOfType<VideoLayer>();
            if (videoLayer == null || string.IsNullOrEmpty(petVideoUrl))
                return;
            videoLayer.PlayFromUrl(petVideoUrl, alphaVideoUrl ?? string.Empty);
        }
    }
}
