import { useState } from "react";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import {
  fetchDurableCutoutReadiness,
  missingCutoutState,
  validateCutoutReadinessResponse,
  type ActiveCutoutIdentity,
  type DurableCutoutState,
} from "@/lib/durable-cutout-readiness";
import { useDurableCutoutReadiness } from "@/lib/use-durable-cutout-readiness";
import { ThemeSelectionScreen } from "./theme-selection-screen";

vi.mock("./use-theme-ownership", () => ({
  useThemeOwnership: () => ({
    offers: new Map(),
    loading: false,
    unavailable: false,
    buying: null,
    error: null,
    balance: null,
    buy: vi.fn(async () => true),
    refresh: vi.fn(async () => {}),
  }),
}));

afterEach(() => {
  cleanup();
  sessionStorage.clear();
  localStorage.clear();
  vi.restoreAllMocks();
});

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => {
    resolve = done;
  });
  return { promise, resolve };
}

function ready(identity: ActiveCutoutIdentity, url = "https://signed.test/cutout.png?token=one"):
  DurableCutoutState {
  return {
    status: "ready",
    petId: identity.petId,
    contentId: identity.contentId,
    cutoutReferenceId: identity.expectedCutoutReferenceId || "cutout-ref",
    displayUrl: url,
    expiresAt: new Date(Date.now() + 60 * 60 * 1000).toISOString(),
  };
}

type Load = Parameters<typeof useDurableCutoutReadiness>[1] extends { load?: infer T }
  ? NonNullable<T>
  : never;

function Harness({
  initialIdentity,
  load,
  loadingFallback = null,
}: {
  initialIdentity: ActiveCutoutIdentity;
  load: Load;
  loadingFallback?: string | null;
}) {
  const [identity, setIdentity] = useState(initialIdentity);
  const { state, refresh } = useDurableCutoutReadiness(identity, { load });
  const display =
    state.status === "ready"
      ? state.displayUrl
      : state.status === "loading"
        ? loadingFallback
        : null;

  return (
    <>
      <button
        type="button"
        onClick={() =>
          setIdentity({ petId: "pet_b", contentId: "b", expectedCutoutReferenceId: "cut-b" })
        }
      >
        switch pet
      </button>
      <ThemeSelectionScreen
        cutoutImage={display}
        cutoutReadiness={state.status}
        onCutoutImageError={refresh}
        originalPhoto={null}
        selectedTheme={1}
        language="en"
        onSelectTheme={() => {}}
        onContinue={() => {}}
        onBack={() => {}}
      />
    </>
  );
}

describe("Theme Selection durable cutout readiness", () => {
  const identity = {
    petId: "pet_a",
    contentId: "a",
    expectedCutoutReferenceId: "cut-a",
  };

  it("reload + failed generation + Back stays loading then ready without a false warning", async () => {
    const request = deferred<DurableCutoutState>();
    const load = vi.fn(() => request.promise) as unknown as Load;
    render(
      <Harness
        initialIdentity={identity}
        load={load}
        loadingFallback="data:image/png;base64,FALLBACK"
      />,
    );

    expect(screen.getByText("Checking your prepared pet…")).toBeTruthy();
    expect(screen.queryByText("Cutout is missing. Please upload again and run processing.")).toBeNull();
    expect(document.querySelector('img[src="data:image/png;base64,FALLBACK"]')).not.toBeNull();

    await act(async () => request.resolve(ready(identity)));
    await screen.findByText("Pet ready");
    expect(screen.queryByText("Cutout is missing. Please upload again and run processing.")).toBeNull();
  });

  it("shows missing only after the backend reports not ready", async () => {
    const request = deferred<DurableCutoutState>();
    const load = vi.fn(() => request.promise) as unknown as Load;
    render(<Harness initialIdentity={identity} load={load} />);

    expect(screen.queryByText("Cutout is missing. Please upload again and run processing.")).toBeNull();
    await act(async () => request.resolve(missingCutoutState(identity)));
    expect(await screen.findByText("Cutout is missing. Please upload again and run processing.")).toBeTruthy();
  });

  it("treats mismatched pet/content/reference identifiers as missing", async () => {
    vi.spyOn(console, "warn").mockImplementation(() => {});
    const load = vi.fn(async (active: ActiveCutoutIdentity) =>
      validateCutoutReadinessResponse(active, {
        pet_id: active.petId,
        content_id: "another-slot",
        intake_ready: true,
        cutout_reference_id: "another-cutout",
        cutout_signed_url: "https://signed.test/wrong.png",
        cutout_signed_url_expires_at: new Date(Date.now() + 60_000).toISOString(),
      }),
    ) as unknown as Load;
    render(<Harness initialIdentity={identity} load={load} />);
    expect(await screen.findByText("Cutout is missing. Please upload again and run processing.")).toBeTruthy();
  });

  it("ignores a stale response after a fast pet-slot switch", async () => {
    const first = deferred<DurableCutoutState>();
    const second = deferred<DurableCutoutState>();
    const load = vi.fn((active: ActiveCutoutIdentity) =>
      active.contentId === "a" ? first.promise : second.promise,
    ) as unknown as Load;
    render(<Harness initialIdentity={identity} load={load} />);

    fireEvent.click(screen.getByText("switch pet"));
    const identityB = { petId: "pet_b", contentId: "b", expectedCutoutReferenceId: "cut-b" };
    await act(async () => second.resolve(ready(identityB, "https://signed.test/b.png")));
    await screen.findByText("Pet ready");
    expect(document.querySelector('img[src="https://signed.test/b.png"]')).not.toBeNull();

    await act(async () => first.resolve(missingCutoutState(identity)));
    await waitFor(() => {
      expect(screen.queryByText("Cutout is missing. Please upload again and run processing.")).toBeNull();
      expect(document.querySelector('img[src="https://signed.test/b.png"]')).not.toBeNull();
    });
  });

  it("refreshes a signed URL when the displayed cutout fails to load", async () => {
    let calls = 0;
    const load = vi.fn(async (active: ActiveCutoutIdentity) => {
      calls += 1;
      return ready(active, `https://signed.test/cutout.png?token=${calls}`);
    }) as unknown as Load;
    render(<Harness initialIdentity={identity} load={load} />);
    await screen.findByText("Pet ready");

    fireEvent.error(document.querySelector('img[src*="token=1"]')!);
    await waitFor(() => expect(document.querySelector('img[src*="token=2"]')).not.toBeNull());
  });

  it("retries a failed exact pet/content lookup once before accepting readiness", async () => {
    const fetchImpl = vi
      .fn()
      .mockRejectedValueOnce(new TypeError("temporary network failure"))
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({
            pet_id: identity.petId,
            content_id: identity.contentId,
            intake_ready: true,
            cutout_reference_id: identity.expectedCutoutReferenceId,
            cutout_signed_url: "https://signed.test/retried.png",
            cutout_signed_url_expires_at: new Date(Date.now() + 60_000).toISOString(),
          }),
          { status: 200, headers: { "Content-Type": "application/json" } },
        ),
      );

    const state = await fetchDurableCutoutReadiness(identity, {
      fetchImpl: fetchImpl as typeof fetch,
      getAccessToken: vi.fn(async () => ({ token: "test-token", source: "supabase" as const })),
      retryDelayMs: 0,
    });

    expect(state.status).toBe("ready");
    expect(fetchImpl).toHaveBeenCalledTimes(2);
    expect(fetchImpl.mock.calls[1]?.[0]).toBe(
      "/api/v1/pet/references/pet_a?content_id=a",
    );
  });
});
