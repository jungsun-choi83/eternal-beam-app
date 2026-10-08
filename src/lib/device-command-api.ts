/** Frontend client for the new backend → external Pi/APK device gateway. */

import { getPremiumAccessToken, type AuthTokenResult } from "./premium-auth-token.ts";
import { recordBeamCommand } from "./beam-activity-log.ts";
import { isDeviceKickstarterDemo } from "./device-demo-config.ts";

const DEMO_DEVICE_ID = "beam-001";

export type DeviceCommandPayload =
  | {
      device_id: string;
      event: "theme_play";
      theme_id: string;
    }
  | {
      device_id: string;
      event: "pet_asset";
      pet_id: string;
      motion_id: string;
      spawn_vfx?: string;
    };

export type DeviceCommandResult =
  | { ok: true; delivery: "sent" | "pending"; command_id?: string }
  | { ok: false; reason: string; status?: number };

export interface DeviceCommandDeps {
  fetchFn?: typeof globalThis.fetch;
  getToken?: () => Promise<AuthTokenResult>;
  apiBase?: string;
  logger?: Pick<Console, "warn">;
}

export function apiBase(): string {
  try {
    const raw = (import.meta as { env?: Record<string, string> }).env?.VITE_API_BASE_URL;
    return (raw || "").trim().replace(/\/$/, "");
  } catch {
    return "";
  }
}

/** Playback-status UI needs a category, not just a raw backend message. */
export type DeviceCommandFailureCategory =
  | "auth"
  | "device_unavailable"
  | "rejected"
  | "network"
  | "unknown";

/**
 * Categorize a failed DeviceCommandResult for user-facing messaging.
 *
 * Backend codes: 404 DEVICE_UNKNOWN (not provisioned), 409 for business-rule
 * rejections (e.g. PUBLISHED_ASSET_REQUIRED), 401/403 for auth. A thrown
 * fetch error has no status — that's the only case treated as "network".
 */
export function classifyDeviceCommandFailure(failure: {
  reason: string;
  status?: number;
}): DeviceCommandFailureCategory {
  if (failure.status === 404) return "device_unavailable";
  if (failure.status === 409) return "rejected";
  if (failure.status === 401 || failure.status === 403) return "auth";
  if (failure.reason === "no_device_command_auth_token") return "auth";
  if (failure.reason === "no_paired_device") return "device_unavailable";
  if (failure.reason === "no_published_pet") return "rejected";
  if (typeof failure.status === "number") return "unknown";
  return "network";
}

async function responseMessage(response: Response): Promise<string> {
  try {
    const body = (await response.json()) as {
      detail?: { message?: string };
      message?: string;
    };
    return body.detail?.message || body.message || `HTTP ${response.status}`;
  } catch {
    return `HTTP ${response.status}`;
  }
}

function commandLabel(payload: DeviceCommandPayload): string {
  return payload.event === "theme_play" ? payload.theme_id : `${payload.pet_id}/${payload.motion_id}`;
}

/**
 * Queue a command for the external device.
 *
 * Both immediate and offline-pending backend delivery are successful. All
 * failures are reported and converted to a result so device availability never
 * blocks the normal web flow.
 */
export async function sendDeviceCommand(
  payload: DeviceCommandPayload,
  deps: DeviceCommandDeps = {},
): Promise<DeviceCommandResult> {
  const result = await sendDeviceCommandOverWire(payload, deps);
  recordBeamCommand({
    commandId: result.ok ? result.command_id : undefined,
    event: payload.event,
    label: commandLabel(payload),
    delivery: result.ok ? result.delivery : "failed",
    reason: result.ok ? undefined : result.reason,
  });
  return result;
}

async function sendDeviceCommandOverWire(
  payload: DeviceCommandPayload,
  deps: DeviceCommandDeps = {},
): Promise<DeviceCommandResult> {
  const fetchFn = deps.fetchFn ?? globalThis.fetch;
  const getToken = deps.getToken ?? getPremiumAccessToken;
  const logger = deps.logger ?? console;

  try {
    const auth = await getToken();
    if (!auth.token) {
      const reason = "no_device_command_auth_token";
      logger.warn("[device-command] command not sent", reason);
      return { ok: false, reason };
    }

    const response = await fetchFn(`${deps.apiBase ?? apiBase()}/api/v1/device/commands`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Authorization: `Bearer ${auth.token}`,
      },
      body: JSON.stringify(payload),
    });

    if (!response.ok) {
      const reason = await responseMessage(response);
      logger.warn("[device-command] backend rejected command", response.status, reason);
      return { ok: false, reason, status: response.status };
    }

    const body = (await response.json()) as {
      delivery?: unknown;
      command_id?: unknown;
    };
    if (body.delivery === "sent" || body.delivery === "pending") {
      return {
        ok: true,
        delivery: body.delivery,
        command_id: typeof body.command_id === "string" ? body.command_id : undefined,
      };
    }

    const reason = "invalid_device_command_response";
    logger.warn("[device-command] unexpected backend response", body);
    return { ok: false, reason };
  } catch (error) {
    const reason = error instanceof Error ? error.message : String(error);
    logger.warn("[device-command] request failed", reason);
    return { ok: false, reason };
  }
}

/** Demo wiring keeps the device choice in one frontend location. */
export function demoDeviceId(): string {
  return DEMO_DEVICE_ID;
}

/**
 * Explicit single-hardware test/staging override (VITE_DEVICE_TEST_ID).
 *
 * We currently have exactly one physical unit (beam-001) for testing. This
 * lets a test/staging build point at it deliberately — never a production
 * default: unset (the production case) resolves to null, same as having no
 * paired device at all.
 */
function testDeviceIdFromEnv(): string | null {
  try {
    const raw = (import.meta as { env?: Record<string, string> }).env?.VITE_DEVICE_TEST_ID;
    return raw?.trim() || null;
  } catch {
    return null;
  }
}

/**
 * The real device_id to send commands to, or null if none is available.
 *
 * The modern gateway has no per-user device pairing yet — it only knows a
 * static set of provisioned devices (ETERNAL_BEAM_DEVICE_TOKENS). Outside the
 * explicit kickstarter/demo flag (`?demo=device` / `?demo=kickstarter`,
 * isDeviceKickstarterDemo) or an explicit test/staging override
 * (VITE_DEVICE_TEST_ID) there is no real device_id to resolve, so callers
 * must treat null as "no device paired" and block Play on Beam truthfully
 * instead of guessing. Neither path is a production default — both require a
 * deliberate opt-in, never an unconditional beam-001 fallback.
 */
export function resolvePairedDeviceId(): string | null {
  if (isDeviceKickstarterDemo()) return DEMO_DEVICE_ID;
  return testDeviceIdFromEnv();
}
