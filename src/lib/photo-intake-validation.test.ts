import { strict as assert } from "node:assert";
import { test } from "node:test";

import {
  classifyIntakeBatch,
  classifyIntakeFile,
  clampToRoom,
  MAX_IMAGE_BYTES,
  MAX_VIDEO_BYTES,
} from "./photo-intake-validation.ts";

function fakeFile(name: string, type: string, size: number): File {
  const bytes = new Uint8Array(Math.max(0, size));
  const file = new File([bytes], name, { type });
  // jsdom-less Node `File` derives size from the blob part length already,
  // but guard here in case a future Node changes that.
  Object.defineProperty(file, "size", { value: size });
  return file;
}

test("classifyIntakeFile accepts an in-limit image", () => {
  const file = fakeFile("dog.jpg", "image/jpeg", 1024);
  const verdict = classifyIntakeFile(file);
  assert.equal(verdict.accepted, true);
  assert.equal(verdict.kind, "image");
});

test("classifyIntakeFile rejects an oversized image", () => {
  const file = fakeFile("dog.jpg", "image/jpeg", MAX_IMAGE_BYTES + 1);
  const verdict = classifyIntakeFile(file);
  assert.equal(verdict.accepted, false);
  assert.equal(!verdict.accepted && verdict.reason, "oversized-image");
});

test("classifyIntakeFile rejects an oversized video", () => {
  const file = fakeFile("dog.mp4", "video/mp4", MAX_VIDEO_BYTES + 1);
  const verdict = classifyIntakeFile(file);
  assert.equal(verdict.accepted, false);
  assert.equal(!verdict.accepted && verdict.reason, "oversized-video");
});

test("classifyIntakeFile rejects an unsupported type", () => {
  const file = fakeFile("scan.pdf", "application/pdf", 1024);
  const verdict = classifyIntakeFile(file);
  assert.equal(verdict.accepted, false);
  assert.equal(!verdict.accepted && verdict.reason, "unsupported-type");
});

test("classifyIntakeBatch keeps valid photos even when an earlier pick is unsupported", () => {
  // Regression: the screen used to decide the whole batch from files[0]'s
  // kind — an unrecognized first file silently dropped every photo after it.
  const bad = fakeFile("note.pdf", "application/pdf", 1024);
  const good1 = fakeFile("a.jpg", "image/jpeg", 1024);
  const good2 = fakeFile("b.png", "image/png", 1024);
  const result = classifyIntakeBatch([bad, good1, good2]);
  assert.equal(result.kind, "image");
  assert.deepEqual(result.images, [good1, good2]);
  assert.equal(result.rejected.length, 1);
  assert.equal(result.rejected[0].reason, "unsupported-type");
});

test("classifyIntakeBatch reports each oversized photo instead of dropping the batch", () => {
  const ok = fakeFile("a.jpg", "image/jpeg", 1024);
  const tooBig = fakeFile("b.jpg", "image/jpeg", MAX_IMAGE_BYTES + 1);
  const result = classifyIntakeBatch([ok, tooBig]);
  assert.deepEqual(result.images, [ok]);
  assert.equal(result.rejected.length, 1);
  assert.equal(result.rejected[0].reason, "oversized-image");
});

test("classifyIntakeBatch treats a video pick as the whole slot, ignoring extra videos", () => {
  const v1 = fakeFile("a.mp4", "video/mp4", 1024);
  const v2 = fakeFile("b.mp4", "video/mp4", 1024);
  const result = classifyIntakeBatch([v1, v2]);
  assert.equal(result.kind, "video");
  assert.equal(result.video, v1);
  assert.equal(result.rejected.length, 1);
  assert.equal(result.rejected[0].reason, "unsupported-type");
});

test("classifyIntakeBatch with no files returns a null kind and no rejections", () => {
  const result = classifyIntakeBatch([]);
  assert.equal(result.kind, null);
  assert.deepEqual(result.images, []);
  assert.equal(result.video, null);
  assert.deepEqual(result.rejected, []);
});

test("clampToRoom caps accepted images to remaining room and reports overflow", () => {
  const files = [fakeFile("a.jpg", "image/jpeg", 1), fakeFile("b.jpg", "image/jpeg", 1), fakeFile("c.jpg", "image/jpeg", 1)];
  const { accepted, overflow } = clampToRoom(files, 1);
  assert.equal(accepted.length, 1);
  assert.equal(accepted[0], files[0]);
  assert.equal(overflow, 2);
});

test("clampToRoom with zero or negative room accepts nothing", () => {
  const files = [fakeFile("a.jpg", "image/jpeg", 1)];
  assert.deepEqual(clampToRoom(files, 0).accepted, []);
  assert.deepEqual(clampToRoom(files, -1).accepted, []);
});
