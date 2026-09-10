using UnityEngine;
using EternalBeam.Device;

public class PetVideoMessageHandler : MonoBehaviour
{
    [SerializeField] private UdpJsonReceiver receiver;
    [SerializeField] private PreparedVideoCrossfadeController crossfadeController;
    private string idleVideoUrl;
    private bool isActionPlaying;
    private string pendingActionUrl;
    private string pendingActionEvent;
    private bool isReady;
    private bool waitingForInitialIdle;

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
        switch (msg.Event)
        {
            case "idle":
            case "touch":
            case "approach":
            case "voice":
            case "nfc_match":
                break;
            default:
                return;
        }
        if (!isReady && msg.Event != "idle")
        {
            Debug.Log($"[Pet] Not ready - waiting for initial Idle, ignored Event={msg.Event}");
            return;
        }
        string url = !string.IsNullOrEmpty(msg.PackedUrl) ? msg.PackedUrl : msg.VideoUrl;
        if (string.IsNullOrEmpty(url))
        {
            Debug.LogWarning($"[Pet] No video URL for event={msg.Event}, motion={msg.MotionId}");
            return;
        }
        if (msg.Event == "idle")
        {
            idleVideoUrl = url;
            Debug.Log($"[Pet] Idle URL saved: {idleVideoUrl}");
            if (isActionPlaying)
            {
                Debug.Log("[Pet] Idle received during Action - saved but playback ignored");
                return;
            }
            if (!isReady) waitingForInitialIdle = true;
            crossfadeController.RequestSwitch(url, true);
            return;
        }
        if (isActionPlaying)
        {
            if (msg.Event == "touch")
            {
                pendingActionUrl = url;
                pendingActionEvent = msg.Event;
                Debug.Log($"[Pet] Action already playing - Touch queued URL={url}");
            }
            else
            {
                Debug.Log($"[Pet] Action already playing - ignored Event={msg.Event}");
            }
            return;
        }
        isActionPlaying = true;
        Debug.Log($"[Pet] Motion={msg.MotionId} URL={url}");
        crossfadeController.RequestSwitch(url, false);
    }

    private void HandleVideoPlaybackStarted()
    {
        if (!isReady && waitingForInitialIdle)
        {
            waitingForInitialIdle = false;
            isReady = true;
            Debug.Log("[Pet] Initial Idle playback started → READY");
        }
    }

    private void HandleActionFinished()
    {
        isActionPlaying = false;
        if (!string.IsNullOrEmpty(pendingActionUrl))
        {
            string nextUrl = pendingActionUrl;
            string nextEvent = pendingActionEvent;
            pendingActionUrl = null;
            pendingActionEvent = null;
            isActionPlaying = true;
            Debug.Log($"[Pet] Action finished → playing queued Event={nextEvent}");
            crossfadeController.RequestSwitch(nextUrl, false);
            return;
        }
        if (string.IsNullOrEmpty(idleVideoUrl))
        {
            Debug.LogWarning("[Pet] Action finished, but no Idle URL is saved.");
            return;
        }
        Debug.Log("[Pet] Action finished → returning to Idle");
        crossfadeController.RequestSwitch(idleVideoUrl, true);
    }

    private void HandlePlaybackFailed()
    {
        if (isActionPlaying)
        {
            isActionPlaying = false;
            Debug.LogWarning("[Pet] Action playback failed → returning to Idle");
            if (!string.IsNullOrEmpty(idleVideoUrl)) crossfadeController.RequestSwitch(idleVideoUrl, true);
            return;
        }
        Debug.LogError("[Pet] Idle playback failed. Waiting for a new valid Idle message.");
    }
}