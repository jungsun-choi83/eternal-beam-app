using System;
using UnityEngine;

public enum PetMode
{
    Demo,
    Event
}

public class PetModeController : MonoBehaviour
{
    public Action OnDemoModeActive;
    public Action OnEventModeActive;

    public PetMode startMode = PetMode.Demo;
    public PetMode CurrentMode { get; private set; }
    public EventPetAssetLoader eventPetAssetLoader;
    public GameObject demoPetRoot;
    public GameObject eventPetRoot;

    private void Start()
    {
        SetMode(startMode);
    }

    public void SetMode(PetMode mode)
    {
        CurrentMode = mode;
        bool isDemo = mode == PetMode.Demo;
        demoPetRoot.SetActive(isDemo);
        eventPetRoot.SetActive(!isDemo);
        if (isDemo)
        {
            OnDemoModeActive?.Invoke();
        }
        else
        {
            eventPetAssetLoader.FadeIn();
            OnEventModeActive?.Invoke();
        }
    }

    public void SwitchToDemoPet()
    {
        SetMode(PetMode.Demo);
    }

    public void SwitchToEventPet()
    {
        SetMode(PetMode.Event);
    }

    public void TogglePetMode()
    {
        SetMode(CurrentMode == PetMode.Demo ? PetMode.Event : PetMode.Demo);
    }
}