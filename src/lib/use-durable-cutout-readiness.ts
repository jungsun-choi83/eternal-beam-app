import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  fetchDurableCutoutReadiness,
  loadingCutoutState,
  millisecondsUntilCutoutRefresh,
  missingCutoutState,
  type ActiveCutoutIdentity,
  type DurableCutoutState,
} from "./durable-cutout-readiness.ts";

type LoadCutout = typeof fetchDurableCutoutReadiness;

export function useDurableCutoutReadiness(
  identity: ActiveCutoutIdentity | null,
  options: { enabled?: boolean; load?: LoadCutout } = {},
): { state: DurableCutoutState; refresh: () => void } {
  const enabled = options.enabled !== false;
  const load = options.load ?? fetchDurableCutoutReadiness;
  const petId = identity?.petId ?? "";
  const contentId = identity?.contentId ?? "";
  const expectedCutoutReferenceId = identity?.expectedCutoutReferenceId ?? null;
  const identityKey = JSON.stringify([petId, contentId, expectedCutoutReferenceId]);
  const stableIdentity = useMemo<ActiveCutoutIdentity | null>(
    () =>
      petId && contentId
        ? { petId, contentId, expectedCutoutReferenceId }
        : null,
    [contentId, expectedCutoutReferenceId, petId],
  );
  const [state, setState] = useState<DurableCutoutState>(() =>
    enabled && stableIdentity ? loadingCutoutState() : missingCutoutState(stableIdentity),
  );
  const [refreshVersion, setRefreshVersion] = useState(0);
  const requestIdRef = useRef(0);
  const previousIdentityKeyRef = useRef("");

  const refresh = useCallback(() => setRefreshVersion((value) => value + 1), []);

  useEffect(() => {
    const identityChanged = previousIdentityKeyRef.current !== identityKey;
    previousIdentityKeyRef.current = identityKey;
    const requestId = ++requestIdRef.current;
    const controller = new AbortController();

    if (!enabled || !stableIdentity) {
      setState(missingCutoutState(stableIdentity));
      return () => controller.abort();
    }
    if (identityChanged) setState(loadingCutoutState());

    void load(stableIdentity, { signal: controller.signal })
      .then((next) => {
        if (!controller.signal.aborted && requestId === requestIdRef.current) setState(next);
      })
      .catch((error) => {
        if (controller.signal.aborted || requestId !== requestIdRef.current) return;
        console.warn("[cutout-readiness] Phase-1 lookup failed after retry", {
          petId: stableIdentity.petId,
          contentId: stableIdentity.contentId,
          message: error instanceof Error ? error.message : String(error),
        });
        setState(missingCutoutState(stableIdentity));
      });

    return () => controller.abort();
  }, [enabled, identityKey, load, refreshVersion, stableIdentity]);

  useEffect(() => {
    if (state.status !== "ready") return;
    const delay = millisecondsUntilCutoutRefresh(state.expiresAt);
    if (delay == null) return;
    const timer = window.setTimeout(refresh, Math.min(delay, 2_147_483_647));
    return () => window.clearTimeout(timer);
  }, [refresh, state.expiresAt, state.status]);

  return { state, refresh };
}
