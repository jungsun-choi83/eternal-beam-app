using System;
using System.Collections;
using UnityEngine;
using UnityEngine.Video;

public class PreparedVideoCrossfadeController : MonoBehaviour
{
    public event Action OnVideoPlaybackStarted;
    public event Action OnNonLoopVideoFinished;
    public event Action OnVideoPlaybackFailed;

    [Header("References")]
    [SerializeField] private DeviceDebugOverlay debugOverlay;

    [Header("Video Players")]
    [SerializeField] private VideoPlayer playerA;
    [SerializeField] private VideoPlayer playerB;

    [Header("Render Objects")]
    [SerializeField] private Transform renderObjectA;
    [SerializeField] private Transform renderObjectB;

    [Header("Materials")]
    [SerializeField] private Material materialA;
    [SerializeField] private Material materialB;

    [Header("Video Clips")]
    [SerializeField] private VideoClip winterClip;
    [SerializeField] private VideoClip springClip;
    [SerializeField] private VideoClip halloweenClip;

    [Header("Settings")]
    [SerializeField] private float fadeDuration = 1.5f;

    private VideoPlayer currentPlayer;
    private VideoPlayer nextPlayer;
    private Material currentMaterial;
    private Material nextMaterial;
    private bool isSwitching = false;
    private bool nextFrameReady;
    private float brightness = 1f;
    private float contrast = 1f;
    private float saturation = 1f;
    private float gamma = 1f;
    private Color tint = Color.white;

    private void Start()
    {
        Application.runInBackground = true;
        currentPlayer = playerA;
        nextPlayer = playerB;
        currentMaterial = materialA;
        nextMaterial = materialB;
        ApplyTuning(currentMaterial);
        ApplyTuning(nextMaterial);
        currentPlayer.isLooping = true;
        nextPlayer.isLooping = true;
        SetMaterialAlpha(currentMaterial, 0f);
        SetMaterialAlpha(nextMaterial, 0f);
        currentPlayer.clip = null;
        playerA.loopPointReached += OnVideoFinished;
        playerB.loopPointReached += OnVideoFinished;
        playerA.errorReceived += OnVideoError;
        playerB.errorReceived += OnVideoError;
        debugOverlay?.SetPlayerStatus("IDLE");
    }

    private void OnDestroy()
    {
        if (playerA != null) playerA.loopPointReached -= OnVideoFinished;
        if (playerB != null) playerB.loopPointReached -= OnVideoFinished;
        if (playerA != null) playerA.errorReceived -= OnVideoError;
        if (playerB != null) playerB.errorReceived -= OnVideoError;
    }


    private void Update()
    {
        if (Input.GetKeyDown(KeyCode.Alpha1)) RequestSwitch(winterClip, false);
        else if (Input.GetKeyDown(KeyCode.Alpha2)) RequestSwitch(springClip, false);
        else if (Input.GetKeyDown(KeyCode.Alpha3)) RequestSwitch(halloweenClip, false);
    }

    public Transform GetIncomingRenderObject()
    {
        return nextPlayer == playerA ? renderObjectA : renderObjectB;
    }

    public void RequestSwitch(VideoClip targetClip, bool loop)
    {
        if (isSwitching) return;
        if (targetClip == null) return;
        if (currentPlayer.clip == targetClip) return;
        StartCoroutine(SwitchRoutine(targetClip, loop));
    }

    public void RequestSwitch(string videoUrl, bool loop = true)
    {
        Debug.Log($"[Crossfade] RequestSwitch URL={videoUrl}");
        if (isSwitching)
        {
            Debug.Log("[Crossfade] BLOCKED: isSwitching is already true");
            return;
        }
        if (string.IsNullOrEmpty(videoUrl)) return;
        StartCoroutine(SwitchRoutine(videoUrl, loop));
    }

    private void SwapRoles()
    {
        VideoPlayer tempPlayer = currentPlayer;
        currentPlayer = nextPlayer;
        nextPlayer = tempPlayer;
        Material tempMat = currentMaterial;
        currentMaterial = nextMaterial;
        nextMaterial = tempMat;
    }

    private void SetMaterialAlpha(Material mat, float alpha)
    {
        mat.SetFloat("_Fade", alpha);
    }

    public void SetBrightness(float value)
    {
        brightness = value;
        ApplyTuning(currentMaterial);
    }

    public void SetContrast(float value)
    {
        contrast = value;
        ApplyTuning(currentMaterial);
    }

    public void SetSaturation(float value)
    {
        saturation = value;
        ApplyTuning(currentMaterial);
    }

    public void SetGamma(float value)
    {
        gamma = value;
        ApplyTuning(currentMaterial);
    }

    public void SetTint(Color value)
    {
        tint = value;
        ApplyTuning(currentMaterial);
    }

    public void SetVideoTuning(float brightness, float contrast, float saturation, float gamma, Color tint)
    {
        if (currentMaterial == null) return;
        currentMaterial.SetFloat("_Brightness", brightness);
        currentMaterial.SetFloat("_Contrast", contrast);
        currentMaterial.SetFloat("_Saturation", saturation);
        currentMaterial.SetFloat("_Gamma", gamma);
        currentMaterial.SetColor("_Tint", tint);
    }

    private void ApplyTuning(Material mat)
    {
        if (mat == null) return;
        mat.SetFloat("_Brightness", brightness);
        mat.SetFloat("_Contrast", contrast);
        mat.SetFloat("_Saturation", saturation);
        mat.SetFloat("_Gamma", gamma);
        mat.SetColor("_Tint", tint);
    }

    public void SetPetTransform(float? positionX, float? positionY, float? scaleX, float? scaleY)
    {
        ApplyPetTransform(renderObjectA, positionX, positionY, scaleX, scaleY);
        ApplyPetTransform(renderObjectB, positionX, positionY, scaleX, scaleY);
    }

    private void ApplyPetTransform(Transform target, float? positionX, float? positionY, float? scaleX, float? scaleY)
    {
        if (target == null) return;
        Vector3 position = target.localPosition;
        if (positionX.HasValue) position.x = positionX.Value;
        if (positionY.HasValue) position.y = positionY.Value;
        target.localPosition = position;
        Vector3 scale = target.localScale;
        if (scaleX.HasValue) scale.x = scaleX.Value;
        if (scaleY.HasValue) scale.y = scaleY.Value;
        target.localScale = scale;
    }

    private IEnumerator SwitchRoutine(VideoClip targetClip)
    {
        isSwitching = true;
        debugOverlay?.SetPlayerStatus("PREPARING");
        debugOverlay?.SetError("-");
        nextPlayer.Stop();
        nextPlayer.clip = targetClip;
        ApplyTuning(nextMaterial);
        SetMaterialAlpha(nextMaterial, 0f);
        nextPlayer.Prepare();
        while (!nextPlayer.isPrepared) yield return null;
        debugOverlay?.SetPlayerStatus("PREPARED");
        nextFrameReady = false;
        nextPlayer.sendFrameReadyEvents = true;
        nextPlayer.frameReady += OnNextFrameReady;
        nextPlayer.Play();
        debugOverlay?.SetPlayerStatus("WAITING FRAME");
        while (!nextFrameReady) yield return null;
        debugOverlay?.SetPlayerStatus("PLAYING");
        nextPlayer.frameReady -= OnNextFrameReady;
        nextPlayer.sendFrameReadyEvents = false;
        float t = 0f;
        while (t < fadeDuration)
        {
            t += Time.deltaTime;
            float alpha = Mathf.Clamp01(t / fadeDuration);
            SetMaterialAlpha(currentMaterial, 1f - alpha);
            SetMaterialAlpha(nextMaterial, alpha);
            yield return null;
        }
        SetMaterialAlpha(currentMaterial, 0f);
        SetMaterialAlpha(nextMaterial, 1f);
        currentPlayer.Stop();
        SwapRoles();
        isSwitching = false;
    }

    private IEnumerator SwitchRoutine(string videoUrl, bool loop = true)
    {
        isSwitching = true;
        debugOverlay?.SetPlayerStatus("PREPARING");
        debugOverlay?.SetError("-");
        nextPlayer.Stop();
        nextPlayer.clip = null;
        nextPlayer.source = VideoSource.Url;
        nextPlayer.url = videoUrl;
        nextPlayer.isLooping = loop;
        ApplyTuning(nextMaterial);
        SetMaterialAlpha(nextMaterial, 0f);
        Debug.Log($"[Crossfade] Preparing URL={nextPlayer.url}, Loop={nextPlayer.isLooping}");
        nextPlayer.Prepare();
        float prepareTimer = 0f;
        while (!nextPlayer.isPrepared)
        {
            prepareTimer += Time.deltaTime;
            if (prepareTimer >= 10f)
            {
                Debug.LogError($"[Crossfade] PREPARE TIMEOUT after {prepareTimer:F1}s URL={nextPlayer.url}");
                isSwitching = false;
                OnVideoPlaybackFailed?.Invoke();
                yield break;
            }
            yield return null;
        }
        debugOverlay?.SetPlayerStatus("PREPARED");
        nextFrameReady = false;
        nextPlayer.sendFrameReadyEvents = true;
        nextPlayer.frameReady += OnNextFrameReady;
        nextPlayer.Play();
        Debug.Log($"[Crossfade] PLAY started. Loop={nextPlayer.isLooping}, URL={nextPlayer.url}");
        debugOverlay?.SetPlayerStatus("WAITING FRAME");
        float frameTimer = 0f;
        while (!nextFrameReady)
        {
            frameTimer += Time.deltaTime;
            if (frameTimer >= 10f)
            {
                Debug.LogError($"[Crossfade] FIRST FRAME TIMEOUT URL={nextPlayer.url}");
                nextPlayer.frameReady -= OnNextFrameReady;
                nextPlayer.sendFrameReadyEvents = false;
                nextPlayer.Stop();
                isSwitching = false;
                OnVideoPlaybackFailed?.Invoke();
                yield break;
            }
            yield return null;
        }

        debugOverlay?.SetPlayerStatus("PLAYING");
        OnVideoPlaybackStarted?.Invoke();
        nextPlayer.frameReady -= OnNextFrameReady;
        nextPlayer.sendFrameReadyEvents = false;
        float t = 0f;
        while (t < fadeDuration)
        {
            t += Time.deltaTime;
            float alpha = Mathf.Clamp01(t / fadeDuration);
            SetMaterialAlpha(currentMaterial, 1f - alpha);
            SetMaterialAlpha(nextMaterial, alpha);
            yield return null;
        }
        SetMaterialAlpha(currentMaterial, 0f);
        SetMaterialAlpha(nextMaterial, 1f);
        currentPlayer.Stop();
        SwapRoles();
        isSwitching = false;
    }

    private void OnNextFrameReady(VideoPlayer source, long frameIdx)
    {
        nextFrameReady = true;
    }

    private void OnVideoFinished(VideoPlayer source)
    {
        if (source.isLooping) return;
        Debug.Log("[Crossfade] Non-loop video finished");
        debugOverlay?.SetPlayerStatus("Stop");
        OnNonLoopVideoFinished?.Invoke();
    }

    private void OnVideoError(VideoPlayer source, string message)
    {
        Debug.LogError($"[Crossfade] VideoPlayer Error: {message}");
        debugOverlay?.SetPlayerStatus("ERROR");
        debugOverlay?.SetError(message);
        isSwitching = false;
        nextFrameReady = false;
        if (source != null)
        {
            source.frameReady -= OnNextFrameReady;
            source.sendFrameReadyEvents = false;
            source.Stop();
        }
        OnVideoPlaybackFailed?.Invoke();
    }

    #region Testing
    public IEnumerator SwitchRoutine(VideoClip targetClip, bool loop)
    {
        isSwitching = true;
        debugOverlay?.SetPlayerStatus("PREPARING");
        debugOverlay?.SetError("-");
        nextPlayer.Stop();
        nextPlayer.url = "";
        nextPlayer.source = VideoSource.VideoClip;
        nextPlayer.clip = targetClip;
        nextPlayer.isLooping = loop;
        ApplyTuning(nextMaterial);
        SetMaterialAlpha(nextMaterial, 0f);
        nextPlayer.Prepare();
        while (!nextPlayer.isPrepared) yield return null;
        debugOverlay?.SetPlayerStatus("PREPARED");
        nextFrameReady = false;
        nextPlayer.sendFrameReadyEvents = true;
        nextPlayer.frameReady += OnNextFrameReady;
        nextPlayer.Play();
        debugOverlay?.SetPlayerStatus("WAITING FRAME");
        while (!nextFrameReady) yield return null;
        debugOverlay?.SetPlayerStatus("PLAYING");
        OnVideoPlaybackStarted?.Invoke();
        nextPlayer.frameReady -= OnNextFrameReady;
        nextPlayer.sendFrameReadyEvents = false;
        float t = 0f;
        while (t < fadeDuration)
        {
            t += Time.deltaTime;
            float alpha = Mathf.Clamp01(t / fadeDuration);
            SetMaterialAlpha(currentMaterial, 1f - alpha);
            SetMaterialAlpha(nextMaterial, alpha);
            yield return null;
        }
        SetMaterialAlpha(currentMaterial, 0f);
        SetMaterialAlpha(nextMaterial, 1f);
        currentPlayer.Stop();
        SwapRoles();
        isSwitching = false;
    }
    #endregion
}
