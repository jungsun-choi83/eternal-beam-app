using System;
using System.Collections;
using System.IO;
using System.IO.Compression;
using UnityEngine;
using UnityEngine.Networking;

public class EventPetAssetDownloader : MonoBehaviour
{
    [Serializable]
    private class PetStatusMessage
    {
        public string pet_id;
        public string status;
    }

    private bool isDownloading = false;
    private string CurrentPetFolder => Path.Combine(Application.persistentDataPath, "CurrentPet");
    private string IncomingPetFolder => Path.Combine(Application.persistentDataPath, "IncomingPet");
    private string DownloadedZipPath => Path.Combine(Application.persistentDataPath, "incoming_pet.zip");

    public EventPetAssetLoader petAssetLoader;
    public DeviceDebugOverlay debugOverlay;
    public NetworkSettings networkSettings;
    private string currentPetId;
    private string lastReportedStatus;

    public void DownloadEventPet(string petId, string zipUrl)
    {
        if (isDownloading)
        {
            Debug.LogWarning("Pet download already in progress.");
            return;
        }
        if (string.IsNullOrWhiteSpace(zipUrl))
        {
            Debug.LogError("Pet ZIP URL is empty.");
            return;
        }
        currentPetId = petId;
        StartCoroutine(DownloadPetRoutine(zipUrl));
    }

    private IEnumerator DownloadPetRoutine(string zipUrl)
    {
        isDownloading = true;
        SetPetStatus("STARTING");
        Debug.Log($"Starting Pet ZIP download:\n{zipUrl}");
        CleanupIncoming();
        if (File.Exists(DownloadedZipPath)) File.Delete(DownloadedZipPath);
        using (UnityWebRequest request = UnityWebRequest.Get(zipUrl))
        {
            request.downloadHandler = new DownloadHandlerFile(DownloadedZipPath);
            UnityWebRequestAsyncOperation operation = request.SendWebRequest();
            while (!operation.isDone)
            {
                int progress = Mathf.RoundToInt(request.downloadProgress * 100f);
                SetPetStatus($"DOWNLOADING {progress}%");
                yield return null;
            }
            if (request.result != UnityWebRequest.Result.Success)
            {
                Debug.LogError($"Pet ZIP download failed:\n" + $"{request.error}");
                DownloadFailed();
                yield break;
            }
            SetPetStatus("DOWNLOADED");
        }
        Debug.Log($"Pet ZIP downloaded:\n{DownloadedZipPath}");
        SetPetStatus("EXTRACTING");
        try
        {
            Directory.CreateDirectory(IncomingPetFolder);
            ZipFile.ExtractToDirectory(DownloadedZipPath, IncomingPetFolder);
        }
        catch (Exception e)
        {
            Debug.LogError($"Failed to extract Pet ZIP:\n{e}");
            DownloadFailed();
            yield break;
        }
        Debug.Log($"Pet ZIP extracted:\n{IncomingPetFolder}");
        try
        {
            if (File.Exists(DownloadedZipPath)) File.Delete(DownloadedZipPath);
        }
        catch (Exception e)
        {
            Debug.LogWarning($"Could not delete temporary ZIP:\n{e.Message}");
        }
        SetPetStatus("VALIDATING");
        if (!ValidateIncomingPet())
        {
            Debug.LogError("Incoming Pet validation failed. " + "Current Pet will remain unchanged.");
            DownloadFailed();
            yield break;
        }
        Debug.Log("Incoming Pet validation passed.");
        try
        {
            ReplaceCurrentPet();
        }
        catch (Exception e)
        {
            Debug.LogError($"Failed to replace Current Pet:\n{e}");
            DownloadFailed();
            yield break;
        }
        SetPetStatus("LOADING");
        Debug.Log("New Pet files ready. Loading Pet Rig...");
        petAssetLoader.SwitchPetRig(CurrentPetFolder, success =>
        {
            isDownloading = false;
            if (!success)
            {
                SetPetStatus("FAILED");
                Debug.LogError("New Pet files were downloaded, but Pet Rig failed to load.");
                return;
            }
            SetPetStatus("READY");
            Debug.Log("NEW PET READY");
        });
    }

    private bool ValidateIncomingPet()
    {
        string[] requiredFiles =
        {
            "subject_rgba.png",
            "breathing_weight.png",
            "locked_mask.png",
            "manifest.json"
        };
        foreach (string fileName in requiredFiles)
        {
            string filePath = Path.Combine(IncomingPetFolder, fileName);
            if (!File.Exists(filePath))
            {
                Debug.LogError($"Missing Pet file:\n{fileName}");
                return false;
            }
            FileInfo fileInfo = new FileInfo(filePath);
            if (fileInfo.Length <= 0)
            {
                Debug.LogError($"Pet file is empty:\n{fileName}");
                return false;
            }
        }
        return true;
    }

    private void ReplaceCurrentPet()
    {
        if (Directory.Exists(CurrentPetFolder)) Directory.Delete(CurrentPetFolder, true);
        Directory.Move(IncomingPetFolder, CurrentPetFolder);
        Debug.Log($"Current Pet replaced:\n{CurrentPetFolder}");
    }

    private void DownloadFailed()
    {
        CleanupIncoming();
        try
        {
            if (File.Exists(DownloadedZipPath)) File.Delete(DownloadedZipPath);
        }
        catch (Exception e)
        {
            Debug.LogWarning($"Temporary ZIP cleanup failed:\n{e.Message}");
        }
        isDownloading = false;
        SetPetStatus("FAILED");
        Debug.LogWarning("New Pet failed. Existing Current Pet remains active.");
    }

    private void CleanupIncoming()
    {
        try
        {
            if (Directory.Exists(IncomingPetFolder)) Directory.Delete(IncomingPetFolder, true);
        }
        catch (Exception e)
        {
            Debug.LogWarning($"IncomingPet cleanup failed:\n{e.Message}");
        }
    }

    private void SetPetStatus(string status)
    {
        debugOverlay?.SetPetAssetStatus(status);
        if (lastReportedStatus == status) return;
        lastReportedStatus = status;
        StartCoroutine(SendPetStatus(status));
    }

    private IEnumerator SendPetStatus(string status)
    {
        string json = JsonUtility.ToJson(new PetStatusMessage
        {
            pet_id = currentPetId,
            status = status
        });
        using UnityWebRequest request = new UnityWebRequest(
            networkSettings.PetStatusUrl,
            UnityWebRequest.kHttpVerbPOST
        );
        byte[] body = System.Text.Encoding.UTF8.GetBytes(json);
        request.uploadHandler = new UploadHandlerRaw(body);
        request.downloadHandler = new DownloadHandlerBuffer();
        request.SetRequestHeader("Content-Type", "application/json");
        yield return request.SendWebRequest();
        if (request.result != UnityWebRequest.Result.Success)
        {
            Debug.LogWarning(
                $"[EventPet] Failed to report status to Pi: {request.error}"
            );
        }
    }

    public string testPetZipName = "1.zip";
    [ContextMenu("Test Event Pet Asset")]
    public void TestPetSpawn()
    {
        string zipPath = Path.Combine(@"C:\Manscom\PetTest", testPetZipName);
        Debug.Log($"Test Pet ZIP: {zipPath}");
        DownloadEventPet("local-test: " + testPetZipName, new Uri(zipPath).AbsoluteUri);
    }
}