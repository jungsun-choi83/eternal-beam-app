#if UNITY_EDITOR
using UnityEditor;
using UnityEngine;

[InitializeOnLoad]
public static class KeepVFXSelection
{
    static GameObject lockedObject;

    static KeepVFXSelection()
    {
        EditorApplication.update += Update;
    }

    [MenuItem("Tools/VFX/Lock Current Selection")]
    static void LockSelection()
    {
        lockedObject = Selection.activeGameObject;
    }

    [MenuItem("Tools/VFX/Unlock Selection")]
    static void UnlockSelection()
    {
        lockedObject = null;
    }

    static void Update()
    {
        if (lockedObject != null && Selection.activeGameObject != lockedObject)
        {
            Selection.activeGameObject = lockedObject;
        }
    }
}
#endif