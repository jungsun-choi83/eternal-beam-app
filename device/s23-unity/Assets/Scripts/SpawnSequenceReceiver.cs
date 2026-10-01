using UnityEngine;

public class SpawnSequenceReceiver : MonoBehaviour
{
    public void ShowPet()
    {
        Debug.Log("[Spawn Sequence] Show Pet");
    }

    public void SpawnBurst()
    {
        Debug.Log("[Spawn Sequence] Burst");
    }

    public void Complete()
    {
        Debug.Log("[Spawn Sequence] Complete");
    }
}