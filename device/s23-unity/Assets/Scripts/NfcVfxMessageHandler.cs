using System.Collections;
using UnityEngine;
using UnityEngine.VFX;
using EternalBeam.Device;

public class NfcVfxMessageHandler : MonoBehaviour
{
    private string currentThemeId;
    private VisualEffect currentVisualEffect;

    public SO_DemoSequenceControlSettings demoSetting;
    public UdpJsonReceiver receiver;

    [Header("Theme VFX")]
    public GameObject forestVfx;
    public GameObject snowVfx;

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
        if (msg.Event == "demo_init")
        {
            StartCoroutine(HandleDemoInit(msg.ThemeId));
            return;
        }
        if (msg.Event == "video_tuning")
        {
            if (msg.VfxIntensity.HasValue) SetVFXIntensity(msg.VfxIntensity.Value);
            return;
        }
        if (msg.Event == "nfc_match")
        {
            Debug.Log($"[NFC VFX] Received theme={msg.ThemeId}");
            if (currentThemeId == msg.ThemeId) return;
            currentThemeId = msg.ThemeId;
            PlayThemeVfx(msg.ThemeId);
        }
    }

    private IEnumerator HandleDemoInit(string themeId)
    {
        yield return new WaitForSeconds(demoSetting.initThemeVFXDelayTimeInSecond);
        PlayThemeVfx(themeId);
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