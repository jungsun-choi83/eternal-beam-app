using System.Collections;
using UnityEngine;
using UnityEngine.Video;

namespace EternalBeam.Device
{
    public class PetMovementTester : MonoBehaviour
    {
        [Header("References")]
        [SerializeField] private VideoPlayer playerA;
        [SerializeField] private Transform renderObjectA;

        [SerializeField] private VideoPlayer playerB;
        [SerializeField] private Transform renderObjectB;

        [SerializeField] private LocalMotionClipReferenceTester localMotionReference;

        [Header("Movement")]
        [SerializeField] private float idleZ = 0f;
        [SerializeField] private float approachZ = -5f;

        [SerializeField] private float startDelay = 1f;
        [SerializeField] private float slowDownDuration = 2f;
        [SerializeField] private float endHoldDuration = 1f;

        private VideoPlayer approachPlayer;
        private Transform approachRenderObject;
        private Coroutine movementRoutine;


        public void StartApproachMovement()
        {
            StopApproachMovement();

            movementRoutine = StartCoroutine(
                ApproachMovementRoutine()
            );
        }


        public void StopApproachMovement()
        {
            if (movementRoutine != null)
            {
                StopCoroutine(movementRoutine);
                movementRoutine = null;
            }

            approachPlayer = null;
            approachRenderObject = null;

            ResetPositions();
        }


        private IEnumerator ApproachMovementRoutine()
        {
            VideoClip approachClip =
                localMotionReference.GetMotion(PetMotion.Approach);

            if (approachClip == null)
            {
                Debug.LogWarning("[ApproachMovement] Approach clip is null");
                yield break;
            }


            // Wait until A or B ACTUALLY starts playing Approach.
            while (approachPlayer == null)
            {
                ResetPositions();

                if (playerA.isPlaying &&
                    playerA.clip == approachClip)
                {
                    approachPlayer = playerA;
                    approachRenderObject = renderObjectA;
                }
                else if (playerB.isPlaying &&
                         playerB.clip == approachClip)
                {
                    approachPlayer = playerB;
                    approachRenderObject = renderObjectB;
                }

                yield return null;
            }


            Vector3 startPosition =
                approachRenderObject.localPosition;

            startPosition.z = idleZ;

            Vector3 targetPosition = startPosition;
            targetPosition.z = approachZ;


            while (
                approachPlayer.isPlaying &&
                approachPlayer.clip == approachClip)
            {
                float duration =
                    (float)approachClip.length;

                float currentTime =
                    (float)approachPlayer.time;

                float progress =
                    GetMovementProgress(
                        currentTime,
                        duration,
                        startDelay,
                        slowDownDuration,
                        endHoldDuration
                    );

                approachRenderObject.localPosition =
                    Vector3.Lerp(
                        startPosition,
                        targetPosition,
                        progress
                    );


                // The other RenderTexture must stay at Z = 0.
                if (approachPlayer == playerA)
                    SetZ(renderObjectB, idleZ);
                else
                    SetZ(renderObjectA, idleZ);


                yield return null;
            }


            movementRoutine = null;
        }


        private float GetMovementProgress(
            float currentTime,
            float duration,
            float startDelay,
            float slowDownDuration,
            float endHoldDuration)
        {
            // First 1 second: don't move.
            if (currentTime <= startDelay)
                return 0f;


            float movementEndTime =
                duration - endHoldDuration;

            // Last 1 second: stay at Z -5.
            if (currentTime >= movementEndTime)
                return 1f;


            float movementDuration =
                movementEndTime - startDelay;

            if (movementDuration <= 0f)
                return 1f;


            float movementTime =
                currentTime - startDelay;

            slowDownDuration =
                Mathf.Min(
                    slowDownDuration,
                    movementDuration
                );

            float slowStartTime =
                movementDuration - slowDownDuration;


            float speed =
                1f /
                (movementDuration -
                 slowDownDuration * 0.5f);


            // Constant speed.
            if (movementTime <= slowStartTime)
            {
                return Mathf.Clamp01(
                    movementTime * speed
                );
            }


            // Decelerate during final movement section.
            float timeIntoSlowdown =
                movementTime - slowStartTime;

            float progressAtSlowStart =
                slowStartTime * speed;

            float slowdownProgress =
                speed *
                (
                    timeIntoSlowdown -
                    (timeIntoSlowdown *
                     timeIntoSlowdown) /
                    (2f * slowDownDuration)
                );


            return Mathf.Clamp01(
                progressAtSlowStart +
                slowdownProgress
            );
        }


        private void ResetPositions()
        {
            SetZ(renderObjectA, idleZ);
            SetZ(renderObjectB, idleZ);
        }


        private void SetZ(Transform target, float z)
        {
            if (target == null)
                return;

            Vector3 position =
                target.localPosition;

            position.z = z;

            target.localPosition = position;
        }
    }
}
