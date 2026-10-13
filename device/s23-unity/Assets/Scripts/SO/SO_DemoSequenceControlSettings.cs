using UnityEngine;

namespace EternalBeam.Device
{
    [CreateAssetMenu(
    fileName = "DemoSettings",
    menuName = "Eternal Beam/Demo Sequence Data Control Settings")]
    public class SO_DemoSequenceControlSettings : ScriptableObject
    {
        public float initPetSpawnDelayTimeInSecond = 3f;
        public float initThemeVFXDelayTimeInSecond = 3f;
        public float initThemeAudioDelayTimeInSecond = 3f;
        public float petMotionSwitchCrossFadeTimeInSecond = 0.5f;
        public float eventPetFadeDurationTimeInSecond = 0.5f;
    }
}
