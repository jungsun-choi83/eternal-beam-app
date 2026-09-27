/**
 * Phase 9 — "내 사진으로 나만의 배경 만들기" 새로고침 재개.
 *
 * 예전에는 화면이 마운트될 때마다 조건 없이 새 서버 작업을 만들었다. 생성이
 * 몇 분 걸리는 동안 새로고침이 나면 진행 중이던 작업은 잊히고 두 번째 작업이
 * 큐에 또 올라갔다 — 같은 배경을 두 번 유료로 만드는 셈이다.
 *
 * 여기서는 재개할지 새로 시작할지를 **순수 판정**으로 뺐다. 저장된 작업
 * 표식이 지금 펫(content_id)의 것일 때만 재개하고, 계보가 다르면(새 펫으로
 * 넘어갔거나 표식이 없으면) 새로 시작한다 — 남의 진행 중 작업을 잘못 이어
 * 받지 않기 위해서다.
 */

export type CustomBackgroundResumeState = {
  jobId: string | null;
  contentId: string | null;
};

export type CustomBackgroundResumeDecision =
  | { action: "resume"; jobId: string }
  | { action: "start" };

export function resolveCustomBackgroundResume(
  stored: CustomBackgroundResumeState,
  currentContentId: string | null
): CustomBackgroundResumeDecision {
  const jobId = (stored.jobId || "").trim() || null;
  const storedContentId = (stored.contentId || "").trim() || null;
  const contentId = (currentContentId || "").trim() || null;

  if (jobId && storedContentId && contentId && storedContentId === contentId) {
    return { action: "resume", jobId };
  }
  return { action: "start" };
}
