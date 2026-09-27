using UnityEngine;
using UnityEngine.UI;

public class DeviceDebugOverlay : MonoBehaviour
{
    [Header("UI")]
    [SerializeField] private GameObject debugPanel;
    [SerializeField] private Text debugText;
    private string udpStatus = "WAITING";
    private string lastEvent = "-";
    private string motion = "-";
    private string videoUrl = "-";
    private string playerStatus = "IDLE";
    private string error = "-";

    private void Start()
    {
        debugPanel.SetActive(false);
        Refresh();
    }

    private void Update()
    {
        if (Input.GetMouseButtonDown(0))
        {
            bool show = !debugPanel.activeSelf;
            debugPanel.SetActive(show);
            if (show) Refresh();
        }
    }

    public void SetUdpReady()
    {
        udpStatus = "READY";
        Refresh();
    }

    public void SetMessage(string eventName, string motionId, string url)
    {
        lastEvent = string.IsNullOrEmpty(eventName) ? "-" : eventName;
        motion = string.IsNullOrEmpty(motionId) ? "-" : motionId;
        videoUrl = string.IsNullOrEmpty(url) ? "-" : url;

        Refresh();
    }

    public void SetPlayerStatus(string status)
    {
        playerStatus = status;
        Refresh();
    }

    public void SetError(string message)
    {
        error = string.IsNullOrEmpty(message) ? "-" : message;
        Refresh();
    }

    private void Refresh()
    {
        if (debugText == null)
            return;

        debugText.text =
            $"ETERNAL BEAM DEBUG\n" +
            $"UDP :5005    {udpStatus}\n" +
            $"Last Event   {lastEvent}\n" +
            $"Motion       {motion}\n" +
            $"Video        {GetFileName(videoUrl)}\n" +
            $"Player       {playerStatus}\n" +
            $"Error        {error}";
    }

    private string GetFileName(string url)
    {
        if (string.IsNullOrEmpty(url) || url == "-")
            return "-";

        int index = url.LastIndexOf('/');

        return index >= 0
            ? url.Substring(index + 1)
            : url;
    }
}