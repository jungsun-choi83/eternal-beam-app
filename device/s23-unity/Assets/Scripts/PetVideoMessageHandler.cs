using System.Collections;
using UnityEngine;
using UnityEngine.Video;
using EternalBeam.Device;

public class PetVideoMessageHandler : MonoBehaviour
{
    [SerializeField] private UdpJsonReceiver receiver;
    [SerializeField] private PreparedVideoCrossfadeController crossfadeController;
    [SerializeField] private TweenActionController tweenActionController;
    private string idleVideoUrl;
    private string pendingActionUrl;
    private string pendingActionEvent;
    private string currentActionEvent;
    private string startingEvent;
    private bool isActionPlaying;
    private bool isReady;
    private bool waitingForInitialIdle;

    [Header("Demo Mode")]
    public bool demoModeEnabled = false;
    [SerializeField] private LocalMotionClipReferenceTester localMotionReference;

    private void OnEnable()
    {
        receiver.OnMessage += HandleMessage;
        crossfadeController.OnVideoPlaybackStarted += HandleVideoPlaybackStarted;
        crossfadeController.OnNonLoopVideoFinished += HandleActionFinished;
        crossfadeController.OnVideoPlaybackFailed += HandlePlaybackFailed;
    }

    private void OnDisable()
    {
        receiver.OnMessage -= HandleMessage;
        crossfadeController.OnVideoPlaybackStarted -= HandleVideoPlaybackStarted;
        crossfadeController.OnNonLoopVideoFinished -= HandleActionFinished;
        crossfadeController.OnVideoPlaybackFailed -= HandlePlaybackFailed;
    }

    private void HandleMessage(PetDeviceMessage msg)
    {
        if (msg == null || !msg.Valid) return;
        string eventName = msg.Event?.ToLowerInvariant();
        if (eventName == "demo_init")
        {
            StartCoroutine(HandleDemoInit());
            return;
        }
        if (eventName == "video_tuning")
        {
            HandleVideoTuning(msg);
            return;
        }
        switch (eventName)
        {
            case "idle":
            case "touch":
            //case "approach":
            case "voice":
                //case "nfc_match":
                break;
            default:
                return;
        }
        if (!isReady && eventName != "idle")
        {
            Debug.Log($"[Pet] Not ready - waiting for initial Idle, ignored Event={eventName}");
            return;
        }
        string url = !string.IsNullOrEmpty(msg.PackedUrl) ? msg.PackedUrl : msg.VideoUrl;
        if (string.IsNullOrEmpty(url))
        {
            if (demoModeEnabled)
            {
                url = "__demo_local__";
            }
            else
            {
                Debug.LogWarning($"[Pet] No video URL for event={eventName}, motion={msg.MotionId}");
                return;
            }
        }
        if (eventName == "idle")
        {
            idleVideoUrl = url;
            if (isActionPlaying) return;
            if (!isReady) waitingForInitialIdle = true;
            RequestMotion(eventName, url, true);
            return;
        }
        if (isActionPlaying)
        {
            if (eventName == "touch")
            {
                if (currentActionEvent == "touch")
                {
                    pendingActionUrl = null;
                    pendingActionEvent = null;
                    return;
                }
                pendingActionUrl = url;
                pendingActionEvent = eventName;
                Debug.Log($"[Pet] Action already playing - Touch queued URL={url}");
                return;
            }
            Debug.Log($"[Pet] Action already playing - ignored Event={eventName}");
            return;
        }
        isActionPlaying = true;
        currentActionEvent = eventName;
        Debug.Log($"[Pet] Motion={msg.MotionId} Event={eventName} URL={url}");
        RequestMotion(eventName, url, false);
    }

    private void HandleVideoPlaybackStarted()
    {
        if (startingEvent == "touch")
        {
            Transform target = crossfadeController.GetIncomingRenderObject();
            tweenActionController.PlayComeCloserAction(target);
        }
        if (!isReady && waitingForInitialIdle)
        {
            waitingForInitialIdle = false;
            isReady = true;
            Debug.Log("[Pet] Initial Idle playback started → READY");
        }
        startingEvent = null;
    }

    private void HandleActionFinished()
    {
        StartCoroutine(WaitForComeCloserThenFinishAction());
    }

    private IEnumerator WaitForComeCloserThenFinishAction()
    {
        if (currentActionEvent == "touch")
        {
            Debug.Log("[Pet] Touch video finished → waiting for ComeCloser tween");
            while (tweenActionController.IsComeCloserPlaying) yield return null;
            Debug.Log("[Pet] ComeCloser tween finished → continue");
        }
        FinishAction();
    }

    private void FinishAction()
    {
        isActionPlaying = false;
        currentActionEvent = null;
        if (!string.IsNullOrEmpty(pendingActionUrl))
        {
            string nextUrl = pendingActionUrl;
            string nextEvent = pendingActionEvent;
            pendingActionUrl = null;
            pendingActionEvent = null;
            isActionPlaying = true;
            currentActionEvent = nextEvent;
            Debug.Log($"[Pet] Action finished → playing queued Event={nextEvent}");
            RequestMotion(nextEvent, nextUrl, false);
            return;
        }
        if (demoModeEnabled)
        {
            Debug.Log("[Pet] Action finished → returning to Local Idle");
            RequestMotion("idle", null, true);
            return;
        }
        if (string.IsNullOrEmpty(idleVideoUrl))
        {
            Debug.LogWarning("[Pet] Action finished, but no Idle URL is saved.");
            return;
        }
        Debug.Log("[Pet] Action finished → returning to Idle");
        RequestMotion("idle", idleVideoUrl, true);
    }

    private void HandlePlaybackFailed()
    {
        if (isActionPlaying)
        {
            isActionPlaying = false;
            currentActionEvent = null;
            pendingActionUrl = null;
            pendingActionEvent = null;
            Debug.LogWarning("[Pet] Action playback failed → returning to Idle");
            if (!string.IsNullOrEmpty(idleVideoUrl)) RequestMotion("idle", idleVideoUrl, true);
            return;
        }
        Debug.LogError("[Pet] Idle playback failed. Waiting for a new valid Idle message.");
    }

    private IEnumerator HandleDemoInit()
    {
        Debug.Log("[Pet] Demo Init received");
        isReady = false;
        waitingForInitialIdle = true;
        isActionPlaying = false;
        currentActionEvent = null;
        pendingActionUrl = null;
        pendingActionEvent = null;
        startingEvent = null;
        if (demoModeEnabled)
        {
            yield return new WaitForSeconds(4f);
            VideoClip idleClip = localMotionReference.GetMotion(PetMotion.Idle);
            crossfadeController.RequestSwitch(idleClip, true);
            Debug.Log("[Pet] Demo Init → Local Idle requested");
            yield return null;
        }
        Debug.LogWarning("[Pet] Demo Init received while Demo Mode is disabled");
    }

    private void HandleVideoTuning(PetDeviceMessage msg)
    {
        if (msg.Target?.ToLowerInvariant() != "pet") return;
        crossfadeController.SetPetTransform(msg.PositionX, msg.PositionY, msg.ScaleX, msg.ScaleY);
        if (msg.Brightness.HasValue) crossfadeController.SetBrightness(msg.Brightness.Value);
        if (msg.Contrast.HasValue) crossfadeController.SetContrast(msg.Contrast.Value);
        if (msg.Saturation.HasValue) crossfadeController.SetSaturation(msg.Saturation.Value);
        if (msg.Gamma.HasValue) crossfadeController.SetGamma(msg.Gamma.Value);
        if (msg.Tint.HasValue) crossfadeController.SetTint(msg.Tint.Value);
    }

    #region Demo Mode Testing
    private PetMotion GetMotionType(string eventName)
    {
        switch (eventName)
        {
            case "idle":
                return PetMotion.Idle;
            case "touch":
                return PetMotion.Touch;
            case "approach":
                return PetMotion.Approach;
            case "voice":
                return PetMotion.Voice;
            case "nfc_match":
                return PetMotion.NfcMatch;
            default:
                return PetMotion.Idle;
        }
    }

    private void RequestMotion(string eventName, string url, bool loop)
    {
        startingEvent = eventName;
        if (demoModeEnabled)
        {
            PetMotion motion = GetMotionType(eventName);
            VideoClip clip = localMotionReference.GetMotion(motion);
            crossfadeController.RequestSwitch(clip, loop);
            return;
        }
        crossfadeController.RequestSwitch(url, loop);
    }
    #endregion
}