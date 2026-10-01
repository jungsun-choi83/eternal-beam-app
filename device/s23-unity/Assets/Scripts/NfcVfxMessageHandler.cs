using UnityEngine;
using UnityEngine.VFX;
using EternalBeam.Device;

public class NfcVfxMessageHandler : MonoBehaviour
{
    [Header("Network")]
    [SerializeField] private UdpJsonReceiver receiver;

    [Header("Theme VFX")]
    [SerializeField] private GameObject forestVfx;
    [SerializeField] private GameObject snowVfx;

    private VisualEffect currentVisualEffect;

    private void Start()
    {
        HideAll();
    }

    private void OnEnable()
    {
        if (receiver != null) receiver.OnMessage += HandleMessage;
    }

    private void OnDisable()
    {
        if (receiver != null) receiver.OnMessage -= HandleMessage;
    }

    private void HandleMessage(PetDeviceMessage msg)
    {
        if (msg == null || !msg.Valid) return;
        if (msg.Event == "video_tuning")
        {
            if (msg.VfxIntensity.HasValue) SetVFXIntensity(msg.VfxIntensity.Value);
            return;
        }
        if (msg.Event != "nfc_match") return;
        Debug.Log($"[NFC VFX] Received theme={msg.ThemeId}");
        PlayThemeVfx(msg.ThemeId);
    }

    private void PlayThemeVfx(string themeId)
    {
        HideAll();
        switch (themeId)
        {
            case "fresh_forest":
                forestVfx?.SetActive(true);
                currentVisualEffect = forestVfx?.GetComponent<VisualEffect>();
                break;
            case "snow_forest":
                snowVfx?.SetActive(true);
                currentVisualEffect = snowVfx?.GetComponent<VisualEffect>();
                break;
            default:
                Debug.LogWarning($"[NFC VFX] Unknown theme_id: {themeId}");
                break;
        }
    }

    private void HideAll()
    {
        forestVfx?.SetActive(false);
        snowVfx?.SetActive(false);
    }

    public void SetVFXIntensity(float value)
    {
        if (currentVisualEffect == null) return;
        currentVisualEffect.SetFloat("Intensity", value);
    }
}