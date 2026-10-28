import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("./supabase-auth.ts", () => ({
  getAccessToken: vi.fn(async () => null),
  isSupabaseAuthConfigured: vi.fn(() => false),
}));

vi.mock("./eternal-beam-user.ts", () => ({
  getEternalBeamUserId: () => "preview-user",
}));

import { getPremiumAccessToken, localExhibitionPreviewEnabled } from "./premium-auth-token.ts";

afterEach(() => {
  vi.unstubAllEnvs();
});

describe("local Exhibition preview authentication", () => {
  it("recognizes only the explicitly enabled isolated Exhibition preview", async () => {
    expect(localExhibitionPreviewEnabled({ DEV: true, VITE_ENABLE_EXHIBITION_LOCAL_PREVIEW: "1" })).toBe(true);
    expect(localExhibitionPreviewEnabled({ DEV: true, VITE_ENABLE_EXHIBITION_LOCAL_PREVIEW: "0" })).toBe(false);

    await expect(getPremiumAccessToken()).resolves.toMatchObject({ token: null, source: "none" });
  });

  it("does not bypass authentication in ordinary development", async () => {
    vi.stubEnv("DEV", true);
    vi.stubEnv("VITE_ENABLE_EXHIBITION_LOCAL_PREVIEW", "0");
    vi.stubEnv("VITE_ALLOW_INSECURE_TEST_AUTH", "0");
    expect(localExhibitionPreviewEnabled({ DEV: true })).toBe(false);
    await expect(getPremiumAccessToken({ allowLocalExhibitionPreview: true })).resolves.toMatchObject({
      token: null,
      source: "none",
    });
  });

  it("does not bypass authentication in production, even if the preview flag is present", async () => {
    vi.stubEnv("DEV", false);
    vi.stubEnv("VITE_ENABLE_EXHIBITION_LOCAL_PREVIEW", "1");
    vi.stubEnv("VITE_ALLOW_INSECURE_TEST_AUTH", "1");
    expect(localExhibitionPreviewEnabled({ DEV: false, VITE_ENABLE_EXHIBITION_LOCAL_PREVIEW: "1" })).toBe(false);
    await expect(getPremiumAccessToken({ allowLocalExhibitionPreview: true })).resolves.toMatchObject({
      token: null,
      source: "none",
    });
  });

  it("keeps non-Exhibition premium callers on the normal authentication path", async () => {
    vi.stubEnv("DEV", true);
    vi.stubEnv("VITE_ENABLE_EXHIBITION_LOCAL_PREVIEW", "1");
    expect(localExhibitionPreviewEnabled({ DEV: true, VITE_ENABLE_EXHIBITION_LOCAL_PREVIEW: "1" })).toBe(true);
    await expect(getPremiumAccessToken()).resolves.toMatchObject({
      token: null,
      source: "none",
    });
  });
});
