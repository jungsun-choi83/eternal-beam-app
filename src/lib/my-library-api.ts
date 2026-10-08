import { getPremiumAccessToken, type AuthTokenResult } from "./premium-auth-token.ts";

export interface PublishedLibraryMotion {
  motionId: string;
  version: number;
  url: string;
  deliveryFormat: string | null;
  backgroundBaked: boolean;
}

export interface LibraryPet {
  petId: string;
  contentId: string | null;
  motions: PublishedLibraryMotion[];
}

export interface LibraryApiDeps {
  fetchFn?: typeof globalThis.fetch;
  getToken?: () => Promise<AuthTokenResult>;
  apiBase?: string;
}

function apiBase(): string {
  try {
    const raw = (import.meta as { env?: Record<string, string> }).env?.VITE_API_BASE_URL;
    return (raw || "").trim().replace(/\/$/, "");
  } catch {
    return "";
  }
}

async function authedGet<T>(path: string, deps: LibraryApiDeps): Promise<Response> {
  const auth = await (deps.getToken ?? getPremiumAccessToken)();
  if (!auth.token) throw new Error("로그인이 필요합니다.");
  return (deps.fetchFn ?? globalThis.fetch)(`${deps.apiBase ?? apiBase()}${path}`, {
    method: "GET",
    cache: "no-store",
    headers: { Authorization: `Bearer ${auth.token}` },
  });
}

export interface LibraryPetId {
  petId: string;
  contentId: string | null;
}

/** Registered pets only — no publication data yet. Used to paint pet cards immediately. */
export async function fetchLibraryPetIds(deps: LibraryApiDeps = {}): Promise<LibraryPetId[]> {
  const petsResponse = await authedGet<{ pets: Array<{ pet_id: string; content_id?: string | null }> }>(
    "/api/v1/pet/registry/mine",
    deps,
  );
  if (!petsResponse.ok) throw new Error(`Library pets request failed (${petsResponse.status})`);
  const pets: Array<{ pet_id: string; content_id?: string | null }> =
    (await petsResponse.json()).pets ?? [];
  return pets
    .map((pet) => ({ petId: String(pet.pet_id || "").trim(), contentId: pet.content_id ?? null }))
    .filter((pet): pet is LibraryPetId => pet.petId.length > 0);
}

export interface PublishedMotionsResult {
  motions: PublishedLibraryMotion[];
  contentId: string | null;
}

/**
 * Published BREATHING pointer for **one** pet. Callers loop this per pet
 * instead of failing the whole library when a single pet's lookup errors —
 * a 404 (not published yet) is not an error, an unexpected status is.
 */
export async function fetchPublishedMotionsForPet(
  petId: string,
  deps: LibraryApiDeps = {},
): Promise<PublishedMotionsResult> {
  const motionResponse = await authedGet(
    `/api/v1/pet/motions/${encodeURIComponent(petId)}/BREATHING/published`,
    deps,
  );
  if (motionResponse.status === 404) return { motions: [], contentId: null };
  if (!motionResponse.ok) {
    throw new Error(`Published motion request failed (${motionResponse.status})`);
  }
  const motion = (await motionResponse.json()) as {
    motion_id?: string;
    motion_version_id?: string | null;
    url?: string;
    delivery_format?: string | null;
    background_baked?: boolean;
    content_id?: string | null;
  };
  if (!motion.url?.trim()) return { motions: [], contentId: motion.content_id ?? null };
  return {
    motions: [
      {
        motionId: String(motion.motion_id || "BREATHING").toUpperCase(),
        version: 1,
        url: motion.url,
        // 백엔드는 BREATHING 발행에 packed_alpha 밖에 만들지 않는다
        // (motion_delivery_service.py 의 DELIVERY_PACKED_ALPHA 가 유일한
        // 전달 포맷 상수이고, delivery_format 컬럼의 DB 체크 제약도
        // NULL 또는 'packed_alpha' 뿐이다). null 은 "다른 포맷"이 아니라
        // 그 컬럼이 생기기 전에 발행된 낡은 레코드라는 뜻이다.
        //
        // 그 null 을 그대로 흘려보내면 재생기가 파일명/크로마 휴리스틱으로
        // 떨어지고, 서명 URL 이 원본 파일명을 보존하지 않거나 캔버스 픽셀
        // 판독이 막히면(CORS) packed 판정을 놓쳐 raw vstack(RGB+알파 매트)이
        // 그대로 보인다 — 여기서 알고 있는 값으로 미리 채운다.
        //
        // 구운 장면(background_baked=true)은 예외다 — 완전히 다른 자산
        // 모양이라 여기서 지어내지 않는다.
        deliveryFormat:
          motion.delivery_format ?? (motion.background_baked === true ? null : "packed_alpha"),
        backgroundBaked: motion.background_baked === true,
      },
    ],
    contentId: motion.content_id ?? null,
  };
}

/**
 * Returning-user source of truth: registered pets plus their currently
 * published BREATHING pointer. No sessionStorage data is used for discovery.
 *
 * One pet's lookup failing rejects this whole call (unchanged contract for
 * existing callers/tests) — screens that need one bad pet to not blank out
 * the rest should call fetchLibraryPetIds + fetchPublishedMotionsForPet
 * directly and track state per pet instead.
 */
export async function fetchMyLibraryPets(deps: LibraryApiDeps = {}): Promise<LibraryPet[]> {
  const ids = await fetchLibraryPetIds(deps);
  const loaded = await Promise.all(
    ids.map(async ({ petId, contentId }): Promise<LibraryPet | null> => {
      const { motions, contentId: motionContentId } = await fetchPublishedMotionsForPet(petId, deps);
      if (!motions.length) return null;
      return { petId, contentId: contentId ?? motionContentId, motions };
    }),
  );
  return loaded.filter((pet): pet is LibraryPet => pet !== null);
}

// ── Phase 11 — aggregate library (backend/routers/library_v1.py) ───────────
//
// GET /api/v1/library?pet_id= returns every motion this pet actually owns or
// was included a free publication of (BREATHING + purchased/membership-
// generated premium motions) with a truthful ownership/access verdict. It
// deliberately excludes anything not yet committed (REVIEW/in-progress), so
// callers must merge in-flight generation state from elsewhere (see
// generation-resume.ts / premium-assets-context.tsx) — this module never
// invents a "generating" row on its own.

export type LibraryOwnershipType = "included" | "credit_purchase" | "membership" | "owned";
export type LibraryAccessState = "playable" | "locked" | "unavailable";

export interface LibraryMotion {
  id: string;
  petId: string;
  motionId: string;
  displayName: string | null;
  publicationId: string | null;
  motionVersionId: string | null;
  version: number | null;
  url: string | null;
  deliveryFormat: string | null;
  backgroundBaked: boolean;
  generatedAt: string | null;
  publishedAt: string | null;
  ownershipType: LibraryOwnershipType;
  ownershipPermanent: boolean;
  accessState: LibraryAccessState;
}

export interface LibraryForPet {
  petId: string;
  motions: LibraryMotion[];
  subscriptionStatus: "active" | "canceled" | "expired" | null;
  entitled: boolean;
}

function asOwnershipType(value: unknown): LibraryOwnershipType {
  return value === "included" || value === "credit_purchase" || value === "membership" || value === "owned"
    ? value
    : "owned";
}

function asAccessState(value: unknown): LibraryAccessState {
  return value === "playable" || value === "locked" || value === "unavailable" ? value : "unavailable";
}

/**
 * One pet's real owned/published motion set. Callers loop this per pet
 * (same fan-out convention as fetchPublishedMotionsForPet) so one pet's
 * failure never blanks out the others.
 */
export async function fetchLibraryForPet(petId: string, deps: LibraryApiDeps = {}): Promise<LibraryForPet> {
  const response = await authedGet<Record<string, unknown>>(
    `/api/v1/library?pet_id=${encodeURIComponent(petId)}`,
    deps,
  );
  if (!response.ok) throw new Error(`Library request failed (${response.status})`);
  const body = (await response.json()) as {
    pet_id?: string;
    motions?: Array<Record<string, unknown>>;
    subscription_status?: string | null;
    entitled?: boolean;
  };
  const motions = (body.motions ?? []).map((m): LibraryMotion => {
    const ownership = (m.ownership as Record<string, unknown>) ?? {};
    const access = (m.access as Record<string, unknown>) ?? {};
    return {
      id: String(m.id ?? ""),
      petId: String(m.pet_id ?? petId),
      motionId: String(m.motion_id ?? "").toUpperCase(),
      displayName: m.display_name == null ? null : String(m.display_name),
      publicationId: m.publication_id == null ? null : String(m.publication_id),
      motionVersionId: m.motion_version_id == null ? null : String(m.motion_version_id),
      version: m.version == null ? null : Number(m.version),
      url: m.url == null ? null : String(m.url),
      deliveryFormat: m.delivery_format == null ? null : String(m.delivery_format),
      backgroundBaked: Boolean(m.background_baked),
      generatedAt: m.generated_at == null ? null : String(m.generated_at),
      publishedAt: m.published_at == null ? null : String(m.published_at),
      ownershipType: asOwnershipType(ownership.type),
      ownershipPermanent: ownership.permanent !== false,
      accessState: asAccessState(access.state),
    };
  });
  return {
    petId: String(body.pet_id ?? petId),
    motions,
    subscriptionStatus:
      body.subscription_status === "active" || body.subscription_status === "canceled" || body.subscription_status === "expired"
        ? body.subscription_status
        : null,
    entitled: Boolean(body.entitled),
  };
}
