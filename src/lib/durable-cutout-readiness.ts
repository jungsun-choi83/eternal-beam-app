import { getPremiumAccessToken } from "./premium-auth-token.ts";

export type CutoutReadiness = "loading" | "ready" | "missing";

export type ActiveCutoutIdentity = {
  petId: string;
  contentId: string;
  expectedCutoutReferenceId?: string | null;
};

export type DurableCutoutState = {
  status: CutoutReadiness;
  petId: string | null;
  contentId: string | null;
  cutoutReferenceId: string | null;
  displayUrl: string | null;
  expiresAt: string | null;
};

export type PetReferencesResponse = {
  pet_id?: string | null;
  content_id?: string | null;
  intake_ready?: boolean;
  cutout_reference_id?: string | null;
  cutout_signed_url?: string | null;
  cutout_signed_url_expires_at?: string | null;
};

export const CUTOUT_REFRESH_SKEW_MS = 5 * 60 * 1000;

export const loadingCutoutState = (): DurableCutoutState => ({
  status: "loading",
  petId: null,
  contentId: null,
  cutoutReferenceId: null,
  displayUrl: null,
  expiresAt: null,
});

export const missingCutoutState = (identity?: ActiveCutoutIdentity | null): DurableCutoutState => ({
  status: "missing",
  petId: identity?.petId ?? null,
  contentId: identity?.contentId ?? null,
  cutoutReferenceId: null,
  displayUrl: null,
  expiresAt: null,
});

function apiBase(): string {
  try {
    const raw = (import.meta as { env?: Record<string, string> }).env?.VITE_API_BASE_URL;
    return (raw || "").trim().replace(/\/$/, "");
  } catch {
    return "";
  }
}

function normalized(value: string | null | undefined): string {
  return (value || "").trim();
}

function warnMismatch(
  reason: string,
  identity: ActiveCutoutIdentity,
  body: PetReferencesResponse,
): void {
  // Never include the signed URL in diagnostics.
  console.warn("[cutout-readiness] ignored mismatched Phase-1 response", {
    reason,
    expectedPetId: identity.petId,
    expectedContentId: identity.contentId,
    expectedCutoutReferenceId: identity.expectedCutoutReferenceId ?? null,
    receivedPetId: body.pet_id ?? null,
    receivedContentId: body.content_id ?? null,
    receivedCutoutReferenceId: body.cutout_reference_id ?? null,
  });
}

export function validateCutoutReadinessResponse(
  identity: ActiveCutoutIdentity,
  body: PetReferencesResponse,
): DurableCutoutState {
  const petId = normalized(body.pet_id);
  const contentId = normalized(body.content_id);
  const cutoutReferenceId = normalized(body.cutout_reference_id);
  const expectedReferenceId = normalized(identity.expectedCutoutReferenceId);

  if (petId !== identity.petId || contentId !== identity.contentId) {
    warnMismatch("identity", identity, body);
    return missingCutoutState(identity);
  }
  if (!body.intake_ready) return missingCutoutState(identity);
  if (!cutoutReferenceId || (expectedReferenceId && cutoutReferenceId !== expectedReferenceId)) {
    warnMismatch("cutout_reference", identity, body);
    return missingCutoutState(identity);
  }

  const displayUrl = normalized(body.cutout_signed_url);
  const expiresAt = normalized(body.cutout_signed_url_expires_at);
  if (!displayUrl || !expiresAt || !Number.isFinite(Date.parse(expiresAt))) {
    warnMismatch("signed_url", identity, body);
    return missingCutoutState(identity);
  }

  return {
    status: "ready",
    petId,
    contentId,
    cutoutReferenceId,
    displayUrl,
    expiresAt,
  };
}

type FetchOptions = {
  signal?: AbortSignal;
  fetchImpl?: typeof fetch;
  getAccessToken?: typeof getPremiumAccessToken;
  retryDelayMs?: number;
};

function waitForRetry(ms: number, signal?: AbortSignal): Promise<void> {
  if (ms <= 0) return Promise.resolve();
  return new Promise((resolve, reject) => {
    const timer = setTimeout(resolve, ms);
    signal?.addEventListener(
      "abort",
      () => {
        clearTimeout(timer);
        reject(new DOMException("Aborted", "AbortError"));
      },
      { once: true },
    );
  });
}

/** Fetches the exact active pet/content pair. Network and signing failures retry once. */
export async function fetchDurableCutoutReadiness(
  identity: ActiveCutoutIdentity,
  options: FetchOptions = {},
): Promise<DurableCutoutState> {
  const tokenResult = await (options.getAccessToken ?? getPremiumAccessToken)();
  if (!tokenResult.token) throw new Error("Phase-1 cutout lookup requires authentication.");

  const fetchImpl = options.fetchImpl ?? fetch;
  const url = `${apiBase()}/api/v1/pet/references/${encodeURIComponent(identity.petId)}` +
    `?content_id=${encodeURIComponent(identity.contentId)}`;
  let lastError: unknown = null;

  for (let attempt = 0; attempt < 2; attempt += 1) {
    try {
      const response = await fetchImpl(url, {
        method: "GET",
        headers: { Authorization: `Bearer ${tokenResult.token}` },
        signal: options.signal,
      });
      if (!response.ok) throw new Error(`Phase-1 cutout lookup failed (${response.status}).`);
      const body = (await response.json()) as PetReferencesResponse;
      return validateCutoutReadinessResponse(identity, body);
    } catch (error) {
      if (options.signal?.aborted) throw error;
      lastError = error;
      if (attempt === 0) await waitForRetry(options.retryDelayMs ?? 200, options.signal);
    }
  }
  throw lastError instanceof Error ? lastError : new Error("Phase-1 cutout lookup failed.");
}

export function millisecondsUntilCutoutRefresh(
  expiresAt: string | null,
  now = Date.now(),
): number | null {
  const expiry = Date.parse(expiresAt || "");
  if (!Number.isFinite(expiry)) return null;
  return Math.max(0, expiry - CUTOUT_REFRESH_SKEW_MS - now);
}

export function matchingCutoutFallback(
  identity: ActiveCutoutIdentity | null,
  pipeline: {
    content_id?: string | null;
    cutout_display_url?: string | null;
    dog_only_nobg_url?: string | null;
    phase1_intake?: { pet_id?: string | null } | null;
  } | null,
  pending: { contentId?: string | null; displayUrl?: string | null } | null,
): string | null {
  if (!identity) return null;
  if (
    normalized(pipeline?.content_id) === identity.contentId &&
    (!normalized(pipeline?.phase1_intake?.pet_id) ||
      normalized(pipeline?.phase1_intake?.pet_id) === identity.petId)
  ) {
    const pipelineDisplay = normalized(
      pipeline?.cutout_display_url || pipeline?.dog_only_nobg_url,
    );
    if (pipelineDisplay) return pipelineDisplay;
  }
  if (normalized(pending?.contentId) === identity.contentId) {
    return normalized(pending?.displayUrl) || null;
  }
  return null;
}
