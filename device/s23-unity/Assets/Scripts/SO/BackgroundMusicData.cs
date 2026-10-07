using UnityEngine;

public enum BackgroundMusicType
{
    FreshForest,
    SnowForest
}

[CreateAssetMenu(
    fileName = "BackgroundMusicData",
    menuName = "Eternal Beam/Audio/Background Music Data"
)]
public class BackgroundMusicData : ScriptableObject
{
    public BackgroundMusicType type;
    public AudioClip clip;

    [Range(0f, 1f)]
    public float volume = 1f;
}