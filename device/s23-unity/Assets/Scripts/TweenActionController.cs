using UnityEngine;
using DG.Tweening;

namespace EternalBeam.Device
{
    public class TweenActionController : MonoBehaviour
    {
        private Camera actionCamera;
        private Tween currentTween;
        private Transform currentTarget;

        [Header("Come Closer Action Settings")]
        public float idleZ = 0f;
        public float approachZ = -5f;
        public float petComeStart = 1f;
        public float petComeDuration = 3f;
        public float petBackStart = 6f;
        public float petBackDuration = 3f;
        public float idleFOV = 60f;
        public float approachFOV = 45f;
        public float fovComeStart = 0.5f;
        public float fovComeDuration = 4.5f;
        public float fovBackStart = 4f;
        public float fovBackDuration = 4.5f;
        public float idleCameraX = 0f;
        public float approachCameraX = 4f;
        public float rotationComeStart = 2f;
        public float rotationComeDuration = 1.5f;
        public float rotationBackStart = 5f;
        public float rotationBackDuration = 1.5f;

        private void Awake()
        {
            actionCamera = Camera.main;
        }

        public void PlayComeCloserAction(Transform target)
        {
            if (target == null || actionCamera == null) return;
            StopCurrentAction();
            currentTarget = target;
            SetZ(currentTarget, idleZ);
            actionCamera.fieldOfView = idleFOV;
            SetCameraX(idleCameraX);
            Sequence sequence = DOTween.Sequence();
            sequence.Insert(petComeStart, currentTarget
                .DOLocalMoveZ(approachZ, petComeDuration)
                .SetEase(Ease.OutQuad)
            );
            sequence.Insert(petBackStart, currentTarget
                .DOLocalMoveZ(idleZ, petBackDuration)
                .SetEase(Ease.InOutQuad)
            );
            sequence.Insert(fovComeStart, actionCamera
                .DOFieldOfView(approachFOV, fovComeDuration)
                .SetEase(Ease.OutQuad)
            );
            sequence.Insert(fovBackStart, actionCamera
                .DOFieldOfView(idleFOV, fovBackDuration)
                .SetEase(Ease.InOutQuad)
            );
            sequence.Insert(rotationComeStart, actionCamera.transform
                .DOLocalRotate(
                    GetCameraRotation(approachCameraX),
                    rotationComeDuration
                )
                .SetEase(Ease.OutQuad)
            );
            sequence.Insert(rotationBackStart, actionCamera.transform
                .DOLocalRotate(
                    GetCameraRotation(idleCameraX),
                    rotationBackDuration
                )
                .SetEase(Ease.InOutQuad)
            );
            sequence.OnComplete(() =>
            {
                currentTween = null;
                currentTarget = null;
            });
            currentTween = sequence;
        }

        public void StopCurrentAction()
        {
            currentTween?.Kill();
            currentTween = null;
            if (currentTarget != null)
            {
                SetZ(currentTarget, idleZ);
                currentTarget = null;
            }
        }

        private void SetZ(Transform target, float z)
        {
            Vector3 position = target.localPosition;
            position.z = z;
            target.localPosition = position;
        }

        private void SetCameraX(float x)
        {
            actionCamera.transform.localEulerAngles = GetCameraRotation(x);
        }

        private Vector3 GetCameraRotation(float x)
        {
            Vector3 rotation = actionCamera.transform.localEulerAngles;
            rotation.x = x;
            return rotation;
        }
    }
}