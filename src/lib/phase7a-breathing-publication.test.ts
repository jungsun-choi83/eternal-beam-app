import assert from "node:assert/strict";
import test from "node:test";

import { shouldRenderThemeBackdrop, shouldTransparentComposite } from "./baked-playback.ts";
import { applyPhase6BreathingPublication } from "./phase7a-breathing-publication.ts";

test("published Phase 6 BREATHING fits the existing unbaked browser playback contract", () => {
  const before = {
    content_id: "content-1",
    cutout_display_url: "blob:cutout",
    dog_only_nobg_url: "https://storage/cutout.png",
    idle_video_url: "https://storage/legacy.mp4",
    action_video_url: "",
    background_baked: true,
  };

  const after = applyPhase6BreathingPublication(before, {
    publication_id: "publication-1",
    motion_version_id: "motion-version-1",
    selected_candidate_id: "candidate-1",
    pet_id: "pet_content-1",
    content_id: "content-1",
    breathing_bucket: "user-assets",
    breathing_object_path: "user/content-1/motions/breathing/v1/seedance_a1_raw.mp4",
    idle_video_url: "https://storage/fresh-phase6-url.mp4",
    background_baked: false,
    deduplicated: false,
  });

  assert.equal(after.idle_video_url, "https://storage/fresh-phase6-url.mp4");
  assert.equal(after.background_baked, false);
  assert.equal(after.cutout_display_url, before.cutout_display_url);
  assert.equal(shouldTransparentComposite(after), true);
  assert.equal(shouldRenderThemeBackdrop(after), true);
});

