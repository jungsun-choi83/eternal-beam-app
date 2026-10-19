/**
 * 전시 대기열 — 경로 판정 · 번호 표기 · SHOW NEXT 요청 본문.
 */

import assert from "node:assert/strict";
import test from "node:test";

import {
  canShowNext,
  formatQueueNumber,
  isExhibitionQueuePath,
  queueLabel,
  readExhibitionId,
  showNextBody,
  type QueueItem,
  type StaffQueue,
} from "./exhibition-queue.ts";
import { isOpsPath, opsRouteFor } from "./ops-nav.ts";

function item(n: number, name: string | null, run = `run-${n}`): QueueItem {
  return {
    run_id: run,
    queue_number: n,
    pet_name: name,
    created_at: null,
    display_status: "WAITING",
    processing_status: "READY",
    handoff_status: null,
    detail: null,
  };
}

function staff(partial: Partial<StaffQueue>): StaffQueue {
  return {
    exhibition_id: "expo-1",
    now_showing: null,
    up_next: null,
    ready: [],
    preparing: [],
    needs_attention: [],
    recently_complete: [],
    handoff_required: true,
    ...partial,
  };
}

test("공개 대기열 경로만 공개 화면이다", () => {
  assert.equal(isExhibitionQueuePath("/exhibition/queue"), true);
  assert.equal(isExhibitionQueuePath("/exhibition/queue/"), true);
  for (const p of ["/exhibition", "/exhibition/staff", "/exhibition/queues", "/", "/shaker"]) {
    assert.equal(isExhibitionQueuePath(p), false, p);
  }
});

test("스태프 화면은 운영 경로이고 공개 화면은 아니다", () => {
  assert.equal(opsRouteFor("/exhibition/staff"), "exhibition");
  assert.equal(isOpsPath("/exhibition/queue"), false);
});

test("전시 id 는 ?exhibition= 또는 ?exhibition_id=", () => {
  assert.equal(readExhibitionId("?exhibition=seoul-2026"), "seoul-2026");
  assert.equal(readExhibitionId("?exhibition_id=busan"), "busan");
  assert.equal(readExhibitionId("?exhibition=%20%20"), null);
  assert.equal(readExhibitionId(""), null);
});

test("번호는 세 자리로 표기한다", () => {
  assert.equal(formatQueueNumber(18), "#018");
  assert.equal(formatQueueNumber(1), "#001");
  assert.equal(formatQueueNumber(1234), "#1234");
  assert.equal(queueLabel({ queue_number: 21, pet_name: "Coco" }), "#021 Coco");
  assert.equal(queueLabel({ queue_number: 22, pet_name: null }), "#022");
});

test("넘길 것이 없으면 SHOW NEXT 를 누를 수 없다", () => {
  assert.equal(canShowNext(null), false);
  assert.equal(canShowNext(staff({ ready: [item(3, "Momo")] })), false);
  assert.equal(canShowNext(staff({ up_next: item(1, "Coco") })), true);
  assert.equal(canShowNext(staff({ now_showing: item(1, "Coco") })), true);
});

test("SHOW NEXT 는 화면이 본 상태를 함께 보낸다", () => {
  assert.deepEqual(showNextBody(staff({ now_showing: item(18, "Coco"), up_next: item(19, "Bori") })), {
    exhibition_id: "expo-1",
    expected_now_showing_run_id: "run-18",
    expected_up_next_run_id: "run-19",
  });
  assert.deepEqual(showNextBody(staff({ up_next: item(1, "Coco") })), {
    exhibition_id: "expo-1",
    expected_now_showing_run_id: null,
    expected_up_next_run_id: "run-1",
  });
});
