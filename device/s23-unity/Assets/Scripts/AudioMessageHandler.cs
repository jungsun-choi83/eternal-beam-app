using System.Collections;
using UnityEngine;
using EternalBeam.Device;

public class AudioMessageHandler : MonoBehaviour
{
    private string currentThemeId;

    [Header("Network")]
    [SerializeField] private UdpJsonReceiver receiver;

    [Header("Background Music")]
    public BackgroundMusicData[] backgroundMusicData;
    public AudioSource backgroundMusicSourceA;
    public AudioSource backgroundMusicSourceB;
    public float backgroundMusicCrossFadeDuration = 1.5f;
    private AudioSource currentBackgroundMusicSource;
    private BackgroundMusicData currentBackgroundMusic;
    private Coroutine backgroundMusicCrossFadeCoroutine;

    [Header("Pet Music")]
    public AudioSource petAudioSource;
    public AudioClip petSpawnSound;

    [Header("Editor Test")]
    public BackgroundMusicType testMusicA;
    public BackgroundMusicType testMusicB;
    public bool testToggle;

    private void OnEnable()
    {
        if (receiver != null) receiver.OnMessage += HandleMessage;
    }

    private void OnDisable()
    {
        if (receiver != null) receiver.OnMessage -= HandleMessage;
    }

    private void Awake()
    {
        SetupBackgroundMusicSource(backgroundMusicSourceA);
        SetupBackgroundMusicSource(backgroundMusicSourceB);
        SetupBackgroundMusicSource(petAudioSource);
        currentBackgroundMusicSource = backgroundMusicSourceA;
    }

    private void SetupBackgroundMusicSource(AudioSource source)
    {
        source.playOnAwake = false;
        source.loop = true;
        source.volume = 0f;
    }

    private void HandleMessage(PetDeviceMessage msg)
    {
        if (msg == null || !msg.Valid) return;
        if (msg.Event == "demo_init")
        {
            StartCoroutine(PlayDemoAudioCoroutine(msg.ThemeId));
            return;
        }
        Debug.Log($"[Audio Manager] Received theme={msg.ThemeId}");
        if (currentThemeId == msg.ThemeId) return;
        currentThemeId = msg.ThemeId;
        PlayBackgroundMusic(msg.ThemeId);
    }

    private IEnumerator PlayDemoAudioCoroutine(string themeId)
    {
        Debug.Log("[Audio Manager] Demo Init received, playing demo audio");
        PlayPetSound(petSpawnSound);
        yield return new WaitForSeconds(5f);
        PlayBackgroundMusic(themeId);
    }

    public void PlayBackgroundMusic(string themeId)
    {
        BackgroundMusicType? musicType = GetBackgroundMusicType(themeId);
        if (!musicType.HasValue) return;
        foreach (BackgroundMusicData music in backgroundMusicData)
        {
            if (music.type == musicType.Value)
            {
                PlayBackgroundMusic(music);
                return;
            }
        }
        Debug.LogWarning($"Background Music not found: {themeId}");
    }

    private void PlayBackgroundMusic(BackgroundMusicData music)
    {
        if (music == null || music.clip == null) return;
        if (currentBackgroundMusic == music && currentBackgroundMusicSource.isPlaying) return;
        if (backgroundMusicCrossFadeCoroutine != null) StopCoroutine(backgroundMusicCrossFadeCoroutine);
        backgroundMusicCrossFadeCoroutine = StartCoroutine(CrossFadeBackgroundMusic(music));
    }

    private IEnumerator CrossFadeBackgroundMusic(BackgroundMusicData music)
    {
        AudioSource oldSource = currentBackgroundMusicSource;
        AudioSource newSource = currentBackgroundMusicSource == backgroundMusicSourceA ? backgroundMusicSourceB : backgroundMusicSourceA;
        newSource.Stop();
        newSource.clip = music.clip;
        newSource.volume = 0f;
        newSource.Play();
        float oldStartVolume = oldSource.volume;
        float targetVolume = music.volume;
        float timer = 0f;
        while (timer < backgroundMusicCrossFadeDuration)
        {
            timer += Time.deltaTime;
            float t = Mathf.Clamp01(timer / backgroundMusicCrossFadeDuration);
            oldSource.volume = Mathf.Lerp(oldStartVolume, 0f, t);
            newSource.volume = Mathf.Lerp(0f, targetVolume, t);
            yield return null;
        }
        oldSource.Stop();
        oldSource.clip = null;
        oldSource.volume = 0f;
        newSource.volume = targetVolume;
        currentBackgroundMusicSource = newSource;
        currentBackgroundMusic = music;
        backgroundMusicCrossFadeCoroutine = null;
    }

    private BackgroundMusicType? GetBackgroundMusicType(string themeId)
    {
        switch (themeId)
        {
            case "fresh_forest":
                return BackgroundMusicType.FreshForest;
            case "snow_forest":
                return BackgroundMusicType.SnowForest;
            default:
                Debug.LogWarning($"[NFC Audio] Unknown theme_id: {themeId}");
                return null;
        }
    }

    public void PlayPetSound(AudioClip clip)
    {
        if (clip == null || petAudioSource == null) return;
        petAudioSource.PlayOneShot(clip);
    }
}