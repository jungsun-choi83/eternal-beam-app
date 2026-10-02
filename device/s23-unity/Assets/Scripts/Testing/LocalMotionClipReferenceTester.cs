using UnityEngine;
using UnityEngine.Video;

namespace EternalBeam.Device
{
    public enum PetMotion
    {
        Idle,
        Touch,
        Approach,
        Voice,
        NfcMatch
    }

    public class LocalMotionClipReferenceTester : MonoBehaviour
    {
        [SerializeField] private VideoClip idle;
        [SerializeField] private VideoClip touch;
        [SerializeField] private VideoClip approach;
        [SerializeField] private VideoClip voice;
        [SerializeField] private VideoClip nfcMatch;

        public VideoClip GetMotion(PetMotion motion)
        {
            switch (motion)
            {
                case PetMotion.Idle:
                    return idle;
                case PetMotion.Touch:
                    return touch;
                case PetMotion.Approach:
                    return approach;
                case PetMotion.Voice:
                    return voice;
                case PetMotion.NfcMatch:
                    return nfcMatch;
                default:
                    return null;
            }
        }
    }
}
