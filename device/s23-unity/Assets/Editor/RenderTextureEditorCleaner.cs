using UnityEditor;
using UnityEngine;

[InitializeOnLoad]
public static class RenderTextureEditorCleaner
{
    static RenderTextureEditorCleaner()
    {
        EditorApplication.playModeStateChanged += OnPlayModeStateChanged;
    }

    private static void OnPlayModeStateChanged(PlayModeStateChange state)
    {
        if (state != PlayModeStateChange.EnteredEditMode) return;
        RenderTexture[] renderTextures = Resources.FindObjectsOfTypeAll<RenderTexture>();
        foreach (RenderTexture rt in renderTextures)
        {
            if (rt == null) continue;
            rt.Release();
        }
    }
}