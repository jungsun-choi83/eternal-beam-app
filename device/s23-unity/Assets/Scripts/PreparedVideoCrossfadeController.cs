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

    private void Start()
    {
        Application.runInBackground = true;
        currentPlayer = playerA;
        nextPlayer = playerB;
        currentMaterial = materialA;
        nextMaterial = materialB;
        currentPlayer.isLooping = true;
        nextPlayer.isLooping = true;
        SetMaterialAlpha(currentMaterial, 1f);
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
        if (Input.GetKeyDown(KeyCode.Alpha1)) RequestSwitch(winterClip);
        else if (Input.GetKeyDown(KeyCode.Alpha2)) RequestSwitch(springClip);
        else if (Input.GetKeyDown(KeyCode.Alpha3)) RequestSwitch(halloweenClip);
    }

    public void RequestSwitch(VideoClip targetClip)
    {
        if (isSwitching) return;
        if (targetClip == null) return;
        if (currentPlayer.clip == targetClip) return;
        StartCoroutine(SwitchRoutine(targetClip));
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

    private IEnumerator SwitchRoutine(VideoClip targetClip)
    {
        isSwitching = true;
        debugOverlay?.SetPlayerStatus("PREPARING");
        debugOverlay?.SetError("-");
        nextPlayer.Stop();
        nextPlayer.clip = targetClip;
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
        if (nextPlayer != null)
        {
            nextPlayer.frameReady -= OnNextFrameReady;
            nextPlayer.sendFrameReadyEvents = false;
            nextPlayer.Stop();
        }
        OnVideoPlaybackFailed?.Invoke();
    }
}
