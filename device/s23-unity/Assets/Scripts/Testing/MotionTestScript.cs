using System.Collections;
using UnityEngine;
using UnityEngine.Video;

public class MotionTestScript : MonoBehaviour
{
    [Header("References")]
    [SerializeField] private PreparedVideoCrossfadeController videoController;

    [Header("Player A")]
    [SerializeField] private VideoPlayer playerA;
    [SerializeField] private Transform renderObjectA;

    [Header("Player B")]
    [SerializeField] private VideoPlayer playerB;
    [SerializeField] private Transform renderObjectB;

    [Header("Motion Clips")]
    [SerializeField] private VideoClip idleClip;
    [SerializeField] private VideoClip approachClip;

    [Header("Approach Movement")]
    [SerializeField] private float idleZ = 0f;
    [SerializeField] private float approachZ = -5f;

    private int tapIndex = 0;

    private bool isApproaching = false;

    private VideoPlayer approachPlayer;
    private Transform approachRenderObject;

    private Coroutine approachRoutine;


    private void OnEnable()
    {
        playerA.loopPointReached += OnVideoFinished;
        playerB.loopPointReached += OnVideoFinished;
    }


    private void OnDisable()
    {
        playerA.loopPointReached -= OnVideoFinished;
        playerB.loopPointReached -= OnVideoFinished;
    }


    private void Update()
    {
        // Phone
        if (Input.touchCount > 0)
        {
            Touch touch = Input.GetTouch(0);

            if (touch.phase == TouchPhase.Began)
            {
                HandleTap();
            }

            return;
        }

#if UNITY_EDITOR

        // Editor
        if (Input.GetMouseButtonDown(0))
        {
            HandleTap();
        }

#endif
    }


    private void HandleTap()
    {
        tapIndex++;

        switch (tapIndex)
        {
            case 1:
                PlayIdle();
                break;

            case 2:
                PlayApproach();
                tapIndex = 0;
                break;
        }
    }


    private void PlayIdle()
    {
        Debug.Log("[MotionTest] Idle");

        StopApproach();

        ResetPositions();

        videoController.RequestSwitch(idleClip, false);
    }


    private void PlayApproach()
    {
        if (isApproaching)
            return;

        Debug.Log("[MotionTest] Request Approach");

        ResetPositions();

        isApproaching = true;

        videoController.RequestSwitch(approachClip, false);

        approachRoutine = StartCoroutine(ApproachRoutine());
    }


    private IEnumerator ApproachRoutine()
    {
        approachPlayer = null;
        approachRenderObject = null;

        // IMPORTANT:
        // Do NOT check clip only.
        //
        // Wait until one of the players is ACTUALLY PLAYING
        // the Approach clip.
        while (approachPlayer == null)
        {
            // Default: both stay at Z = 0
            SetZ(renderObjectA, idleZ);
            SetZ(renderObjectB, idleZ);

            if (playerA.isPlaying && playerA.clip == approachClip)
            {
                approachPlayer = playerA;
                approachRenderObject = renderObjectA;

                Debug.Log("[MotionTest] Player A is now playing Approach");
            }
            else if (playerB.isPlaying && playerB.clip == approachClip)
            {
                approachPlayer = playerB;
                approachRenderObject = renderObjectB;

                Debug.Log("[MotionTest] Player B is now playing Approach");
            }

            yield return null;
        }


        // Only the confirmed Approach Render Object moves.
        Vector3 startPosition = approachRenderObject.localPosition;
        startPosition.z = idleZ;

        Vector3 targetPosition = startPosition;
        targetPosition.z = approachZ;

        approachRenderObject.localPosition = startPosition;


        while (
            isApproaching &&
            approachPlayer.isPlaying &&
            approachPlayer.clip == approachClip
        )
        {
            float duration = (float)approachClip.length;
            float currentTime = (float)approachPlayer.time;
            float progress = GetMovementProgress(
                currentTime,
                duration,
                1f, // 開頭 1 秒不動
                2f, // 2 秒減速
                1f  // 結尾 1 秒不動
            );

            approachRenderObject.localPosition =
                Vector3.Lerp(
                    startPosition,
                    targetPosition,
                    progress
                );


            // Make absolutely sure the OTHER object stays Z = 0.
            if (approachPlayer == playerA)
            {
                SetZ(renderObjectB, idleZ);
            }
            else
            {
                SetZ(renderObjectA, idleZ);
            }


            yield return null;
        }

        approachRoutine = null;
    }


    private void OnVideoFinished(VideoPlayer source)
    {
        // Only care about the player we confirmed
        // is actually playing Approach.
        if (!isApproaching)
            return;

        if (source != approachPlayer)
            return;

        if (source.clip != approachClip)
            return;


        Debug.Log("[MotionTest] Approach finished");

        FinishApproach();
    }


    private void FinishApproach()
    {
        isApproaching = false;

        if (movementRoutineExists())
        {
            StopCoroutine(approachRoutine);
            approachRoutine = null;
        }

        // Everything returns to Z = 0 before Idle.
        ResetPositions();

        approachPlayer = null;
        approachRenderObject = null;

        Debug.Log("[MotionTest] Switch back to Idle");

        // Existing controller handles crossfade.
        videoController.RequestSwitch(idleClip, false);
    }


    private bool movementRoutineExists()
    {
        return approachRoutine != null;
    }


    private void StopApproach()
    {
        isApproaching = false;

        if (approachRoutine != null)
        {
            StopCoroutine(approachRoutine);
            approachRoutine = null;
        }

        approachPlayer = null;
        approachRenderObject = null;
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

        Vector3 position = target.localPosition;
        position.z = z;

        target.localPosition = position;
    }

    private float GetMovementProgress(
    float currentTime,
    float duration,
    float startDelay,
    float slowDownDuration,
    float endHoldDuration)
    {
        // 前 1 秒不動
        if (currentTime <= startDelay)
            return 0f;

        // 最後 1 秒已經到終點，不再移動
        float movementEndTime = duration - endHoldDuration;

        if (currentTime >= movementEndTime)
            return 1f;

        float movementDuration =
            movementEndTime - startDelay;

        if (movementDuration <= 0f)
            return 1f;

        float movementTime =
            currentTime - startDelay;

        slowDownDuration = Mathf.Min(
            slowDownDuration,
            movementDuration
        );

        float slowStartTime =
            movementDuration - slowDownDuration;

        // 等速 + 最後 slowdown 的總移動距離 = 1
        float speed =
            1f /
            (movementDuration - slowDownDuration * 0.5f);

        // 等速移動
        if (movementTime <= slowStartTime)
        {
            return Mathf.Clamp01(
                movementTime * speed
            );
        }

        // 最後 2 秒逐漸減速
        float timeIntoSlowdown =
            movementTime - slowStartTime;

        float progressAtSlowStart =
            slowStartTime * speed;

        float slowdownProgress =
            speed *
            (
                timeIntoSlowdown -
                (timeIntoSlowdown * timeIntoSlowdown) /
                (2f * slowDownDuration)
            );

        return Mathf.Clamp01(
            progressAtSlowStart + slowdownProgress
        );
    }
}