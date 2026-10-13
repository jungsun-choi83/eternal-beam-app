using System;
using System.IO;
using UnityEngine;
using DG.Tweening;
using EternalBeam.Device;

public class EventPetAssetLoader : MonoBehaviour
{
    private Material petMaterial;
    private Texture2D currentPetTexture;
    private Texture2D currentBreathingWeight;
    private Texture2D currentLockedMask;
    private static readonly int PetTextureID = Shader.PropertyToID("_PetTexture");
    private static readonly int BreathingWeightMaskID = Shader.PropertyToID("_BreathingWeightMask");
    private static readonly int LockedMaskID = Shader.PropertyToID("_LockedMask");
    private static readonly int BreathingCenterID = Shader.PropertyToID("_BreathingCenter");
    private static readonly int BreathingSideAxisID = Shader.PropertyToID("_BreathingSideAxis");
    private static readonly int FadeID = Shader.PropertyToID("_Fade");
    private Tween fadeTween;

    public SO_DemoSequenceControlSettings demoSettings;
    public Renderer petRenderer;

    [Serializable]
    private class Manifest
    {
        public int[] canvas_wh;
        public Anchors anchors;
        public Files files;
    }

    [Serializable]
    private class Anchors
    {
        public float[] breathing_center;
        public float[] breathing_axis;
    }

    [Serializable]
    private class Files
    {
        public FileEntry subject_rgba;
        public FileEntry breathing_weight;
        public FileEntry locked_mask;
    }

    [Serializable]
    private class FileEntry
    {
        public string path;
    }

    private void Awake()
    {
        if (petRenderer != null) petMaterial = petRenderer.material;
    }

    private void OnDestroy()
    {
        ReleaseCurrentTextures();
        if (petMaterial != null)
        {
            fadeTween?.Kill();
            Destroy(petMaterial);
            petMaterial = null;
        }
    }

    private bool LoadPetRig(string folderPath)
    {
        string manifestPath = Path.Combine(folderPath, "manifest.json");
        if (!File.Exists(manifestPath))
        {
            Debug.LogError($"Manifest not found: {manifestPath}");
            return false; ;
        }
        string json = File.ReadAllText(manifestPath);
        Manifest manifest = JsonUtility.FromJson<Manifest>(json);
        if (manifest == null || manifest.canvas_wh == null || manifest.canvas_wh.Length < 2 || manifest.anchors == null)
        {
            Debug.LogError("Invalid Pet Rig manifest.");
            return false; ;
        }
        Texture2D petTexture = LoadTexture(Path.Combine(folderPath, manifest.files.subject_rgba.path), false);
        Texture2D breathingWeight = LoadTexture(Path.Combine(folderPath, manifest.files.breathing_weight.path), true);
        Texture2D lockedMask = LoadTexture(Path.Combine(folderPath, manifest.files.locked_mask.path), true);
        if (petTexture == null || breathingWeight == null || lockedMask == null)
        {
            if (petTexture != null) Destroy(petTexture);
            if (breathingWeight != null) Destroy(breathingWeight);
            if (lockedMask != null) Destroy(lockedMask);
            Debug.LogError("Failed to load new Pet. Keeping current Pet.");
            return false; ;
        }
        ReleaseCurrentTextures();
        currentPetTexture = petTexture;
        currentBreathingWeight = breathingWeight;
        currentLockedMask = lockedMask;
        petMaterial.SetTexture(PetTextureID, currentPetTexture);
        petMaterial.SetTexture(BreathingWeightMaskID, currentBreathingWeight);
        petMaterial.SetTexture(LockedMaskID, currentLockedMask);
        float width = manifest.canvas_wh[0];
        float height = manifest.canvas_wh[1];
        float centerX = manifest.anchors.breathing_center[0] / width;
        float centerY = 1f - (manifest.anchors.breathing_center[1] / height);
        Vector2 breathingCenter = new Vector2(centerX, centerY);
        petMaterial.SetVector(BreathingCenterID, breathingCenter);
        Vector2 breathingAxis = new Vector2(manifest.anchors.breathing_axis[0], -manifest.anchors.breathing_axis[1]).normalized;
        Vector2 breathingSideAxis = new Vector2(breathingAxis.y, -breathingAxis.x).normalized;
        petMaterial.SetVector(BreathingSideAxisID, breathingSideAxis);
        FadeIn();
        return true;
    }

    private Texture2D LoadTexture(string path, bool linear)
    {
        if (!File.Exists(path))
        {
            Debug.LogError($"Texture not found: {path}");
            return null;
        }
        byte[] bytes = File.ReadAllBytes(path);
        Texture2D texture = new Texture2D(2, 2, TextureFormat.RGBA32, false, linear);
        if (!texture.LoadImage(bytes))
        {
            Debug.LogError($"Failed to load texture: {path}");
            Destroy(texture);
            return null;
        }
        texture.wrapMode = TextureWrapMode.Clamp;
        texture.filterMode = FilterMode.Bilinear;
        return texture;
    }

    private void ReleaseCurrentTextures()
    {
        if (currentPetTexture != null) Destroy(currentPetTexture);
        if (currentBreathingWeight != null) Destroy(currentBreathingWeight);
        if (currentLockedMask != null) Destroy(currentLockedMask);
        currentPetTexture = null;
        currentBreathingWeight = null;
        currentLockedMask = null;
    }

    public void SwitchPetRig(string folderPath, Action<bool> onComplete = null)
    {
        fadeTween?.Kill();
        if (currentPetTexture == null)
        {
            bool success = LoadPetRig(folderPath);
            onComplete?.Invoke(success);
            return;
        }
        fadeTween = DOTween.To(
            () => petMaterial.GetFloat(FadeID),
            value => petMaterial.SetFloat(FadeID, value),
            0f,
            demoSettings.eventPetFadeDurationTimeInSecond
        )
        .SetEase(Ease.InOutSine)
        .OnComplete(() =>
        {
            bool success = LoadPetRig(folderPath);
            onComplete?.Invoke(success);
        });
    }

    public void FadeIn()
    {
        if (currentPetTexture == null) return;
        fadeTween?.Kill();
        petMaterial.SetFloat(FadeID, 0f);
        fadeTween = DOTween.To(
            () => petMaterial.GetFloat(FadeID),
            value => petMaterial.SetFloat(FadeID, value),
            1f,
             demoSettings.eventPetFadeDurationTimeInSecond
        )
        .SetEase(Ease.InOutSine);
    }
}