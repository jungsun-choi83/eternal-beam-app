using UnityEngine;

public class VideoCrossfadeTest : MonoBehaviour
{
    [SerializeField] private Material videoBMaterial;
    [SerializeField] private float duration = 2f;

    private bool isShowingB;
    private float transition;

    private void Update()
    {
        if (Input.GetKeyDown(KeyCode.Space)) isShowingB = !isShowingB;
        float target = isShowingB ? 1f : 0f;
        transition = Mathf.MoveTowards(transition, target, Time.deltaTime / duration);
        Color color = videoBMaterial.color;
        color.a = transition;
        videoBMaterial.color = color;
    }
}