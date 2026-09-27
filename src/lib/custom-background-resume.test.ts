import { strict as assert } from "node:assert";
import { test } from "node:test";

import { resolveCustomBackgroundResume } from "./custom-background-resume.ts";

test("같은 펫(content_id)의 저장된 작업이 있으면 재개한다", () => {
  const decision = resolveCustomBackgroundResume(
    { jobId: "job-1", contentId: "cid-1" },
    "cid-1"
  );
  assert.deepEqual(decision, { action: "resume", jobId: "job-1" });
});

test("다른 펫(content_id)의 표식이면 새로 시작한다 — 남의 작업을 이어받지 않는다", () => {
  const decision = resolveCustomBackgroundResume(
    { jobId: "job-1", contentId: "cid-old" },
    "cid-new"
  );
  assert.deepEqual(decision, { action: "start" });
});

test("표식이 아예 없으면 새로 시작한다", () => {
  const decision = resolveCustomBackgroundResume({ jobId: null, contentId: null }, "cid-1");
  assert.deepEqual(decision, { action: "start" });
});

test("작업 id만 있고 content_id 결속이 없으면(예전 데이터) 재개하지 않는다", () => {
  const decision = resolveCustomBackgroundResume({ jobId: "job-1", contentId: null }, "cid-1");
  assert.deepEqual(decision, { action: "start" });
});

test("지금 content_id 를 아직 모르면(콘텐츠 결속 전) 새로 시작한다", () => {
  const decision = resolveCustomBackgroundResume({ jobId: "job-1", contentId: "cid-1" }, null);
  assert.deepEqual(decision, { action: "start" });
});
