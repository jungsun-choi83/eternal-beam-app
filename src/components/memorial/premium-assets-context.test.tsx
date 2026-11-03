import { act, cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { discoverPremiumAssets, type PremiumAssets } from "@/lib/premium-assets";
import {
  COMPANION_REFRESH_MS, COMPANION_REFRESH_WINDOW_MS, ENTITLEMENT_REFRESH_MS,
  PremiumAssetsProvider, usePremiumAssetsContext,
} from "./premium-assets-context";

vi.mock("@/lib/premium-auth-token", () => ({
  getPremiumAccessToken: vi.fn(async () => ({ token: "token" })),
}));
vi.mock("@/lib/premium-assets", async (importOriginal) => ({
  ...await importOriginal<typeof import("@/lib/premium-assets")>(),
  discoverPremiumAssets: vi.fn(),
}));

const pending: PremiumAssets = {
  petId: "pet_1", ready: { COME_CLOSER: "/closer.mp4" },
  readyAssets: { COME_CLOSER: { url: "/closer.mp4", deliveryFormat: "packed_alpha" } },
  generating: [], missing: [], idleEvents: [], actionEvents: [], prices: {},
  entitled: true, preferences: {}, subscriptionStatus: "active", subscriptionRequired: true,
};
function Consumer() {
  const { assets } = usePremiumAssetsContext();
  return <output>{assets?.readyAssets.RETURN_HOME?.url ?? "missing"}</output>;
}
async function mount() {
  const result = render(<PremiumAssetsProvider petId="pet_1" enabled><Consumer /></PremiumAssetsProvider>);
  await act(async () => {});
  return result;
}
async function advance(ms: number) {
  await act(async () => { await vi.advanceTimersByTimeAsync(ms); });
}
beforeEach(() => {
  vi.useFakeTimers();
  vi.spyOn(document, "visibilityState", "get").mockReturnValue("visible");
  vi.mocked(discoverPremiumAssets).mockReset().mockResolvedValue(pending);
});
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.useRealTimers();
});

it("discovers a companion that becomes ready after page load, then backs off", async () => {
  const h = await mount();
  expect(screen.getByText("missing")).toBeTruthy();
  expect(discoverPremiumAssets).toHaveBeenCalledTimes(1);
  vi.mocked(discoverPremiumAssets).mockResolvedValue({
    ...pending,
    readyAssets: { ...pending.readyAssets, RETURN_HOME: { url: "/home.mp4", deliveryFormat: "packed_alpha" } },
  });
  await advance(COMPANION_REFRESH_MS - 1);
  expect(discoverPremiumAssets).toHaveBeenCalledTimes(1);
  await advance(1);
  expect(screen.getByText("/home.mp4")).toBeTruthy();
  expect(discoverPremiumAssets).toHaveBeenCalledTimes(2);
  await advance(COMPANION_REFRESH_MS);
  expect(discoverPremiumAssets).toHaveBeenCalledTimes(2);
  h.unmount();
  await advance(ENTITLEMENT_REFRESH_MS);
  expect(discoverPremiumAssets).toHaveBeenCalledTimes(2);
});

it("backs off after ten minutes if the companion failed or remains unavailable", async () => {
  await mount();
  await advance(COMPANION_REFRESH_WINDOW_MS);
  const count = vi.mocked(discoverPremiumAssets).mock.calls.length;
  expect(count).toBe(1 + COMPANION_REFRESH_WINDOW_MS / COMPANION_REFRESH_MS);
  await advance(ENTITLEMENT_REFRESH_MS - 1);
  expect(discoverPremiumAssets).toHaveBeenCalledTimes(count);
  await advance(1);
  expect(discoverPremiumAssets).toHaveBeenCalledTimes(count + 1);
});

it("uses the slow refresh when hidden or COME_CLOSER is absent", async () => {
  vi.spyOn(document, "visibilityState", "get").mockReturnValue("hidden");
  const hidden = await mount();
  await advance(COMPANION_REFRESH_MS);
  expect(discoverPremiumAssets).toHaveBeenCalledTimes(1);
  hidden.unmount();
  vi.mocked(discoverPremiumAssets).mockReset().mockResolvedValue({ ...pending, ready: {}, readyAssets: {} });
  vi.spyOn(document, "visibilityState", "get").mockReturnValue("visible");
  await mount();
  await advance(COMPANION_REFRESH_MS);
  expect(discoverPremiumAssets).toHaveBeenCalledTimes(1);
});
