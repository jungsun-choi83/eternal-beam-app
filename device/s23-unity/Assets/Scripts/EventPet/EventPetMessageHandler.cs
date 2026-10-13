using UnityEngine;
using EternalBeam.Device;

public class EventPetMessageHandler : MonoBehaviour
{
    public UdpJsonReceiver receiver;
    public PetModeController petModeController;
    public EventPetAssetDownloader assetDownloader;

    private void OnEnable()
    {
        receiver.OnMessage += HandleMessage;
    }

    private void OnDisable()
    {
        receiver.OnMessage -= HandleMessage;
    }

    private void HandleMessage(PetDeviceMessage msg)
    {
        if (msg == null || !msg.Valid) return;
        string eventName = msg.Event?.ToLowerInvariant();
        if (eventName == "pet_mode_demo")
        {
            Debug.Log("[EventPet] Switching to Demo Pet");
            petModeController.SwitchToDemoPet();
            return;
        }
        if (eventName == "pet_mode_event")
        {
            Debug.Log("[EventPet] Switching to Event Pet");
            petModeController.SwitchToEventPet();
            return;
        }
        if (eventName != "pet_asset") return;
        string url = !string.IsNullOrEmpty(msg.PackedUrl) ? msg.PackedUrl : msg.VideoUrl;
        if (string.IsNullOrEmpty(url)) return;
        Debug.Log($"[EventPet] Pet asset received: {msg.PetId} / {url}");
        assetDownloader.DownloadEventPet(msg.PetId, url);
    }
}