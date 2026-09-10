/**
 * Phase 7A publication response → existing browser pipeline compatibility.
 *
 * This module does not start generation and is deliberately not wired to Preview yet. It only
 * proves that a published Phase 6 BREATHING URL fits the current StoredPipeline contract.
 */

export interface Phase6BreathingPublication {
  publication_id: string;
  motion_version_id: string;
  selected_candidate_id: string;
  pet_id: string;
  content_id?: string | null;
  breathing_bucket: string;
  breathing_object_path: string;
  idle_video_url: string;
  background_baked: false;
  published_at?: string | null;
  deduplicated: boolean;
}

export interface BreathingPipelineLike {
  idle_video_url: string;
  background_baked?: boolean;
}

/** Keep every existing pipeline field and replace only its breathing playback source. */
export function applyPhase6BreathingPublication<T extends BreathingPipelineLike>(
  pipeline: T,
  publication: Phase6BreathingPublication
): T {
  if (!publication.idle_video_url.trim()) {
    throw new Error("Phase 7A publication is missing idle_video_url");
  }
  return {
    ...pipeline,
    idle_video_url: publication.idle_video_url,
    // Phase 6 is a theme-independent pet motion. Existing playback must render it as a subject.
    background_baked: false,
  };
}

