using UnityEngine;

[CreateAssetMenu(
    fileName = "NetworkSettings",
    menuName = "Eternal Beam/Network Settings"
)]
public class NetworkSettings : ScriptableObject
{
    [Header("Raspberry Pi")]
    public string piAddress = "192.168.1.102";
    public int piHttpPort = 8787;

    [Header("Unity")]
    public int udpPort = 5005;

    public string PetStatusUrl => $"http://{piAddress}:{piHttpPort}/pet-status";
}
