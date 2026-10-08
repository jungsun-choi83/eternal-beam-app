/** Frontend client for the device gateway's connection-state endpoint. */

import { getPremiumAccessToken, type AuthTokenResult } from "./premium-auth-token.ts";
import { apiBase } from "./device-command-api.ts";

export interface DeviceConnectionState {
  device_id: string;
  online: boolean;
  connected_at: string | null;
  last_seen: string | null;
  last_ack: string | null;
}

export type DeviceConnectionFailureReason = "auth" | "not_provisioned" | "network" | "unknown";

export type DeviceConnectionResult =
  | { ok: true; state: DeviceConnectionState }
  | { ok: false; reason: DeviceConnectionFailureReason; message: string };

export interface DeviceConnectionDeps {
  fetchFn?: typeof globalThis.fetch;
  getToken?: () => Promise<AuthTokenResult>;
  apiBase?: string;
  logger?: Pick<Console, "warn">;
}

function isDeviceConnectionState(value: unknown): value is DeviceConnectionState {
  if (typeof value !== "object" || value === null) return false;
  const v = value as Record<string, unknown>;
  return typeof v.device_id === "string" && typeof v.online === "boolean";
}

/**
 * Fetch the authoritative connection state for one device.
 *
 * There is no per-user pairing yet — the gateway is configured with static
 * device credentials (ETERNAL_BEAM_DEVICE_TOKENS). A 404 here means this
 * device_id was never provisioned, which this reports as "not_provisioned"
 * rather than pretending the device is offline-but-real.
 */
export async function getDeviceConnectionState(
  deviceId: string,
  deps: DeviceConnectionDeps = {},
): Promise<DeviceConnectionResult> {
  const fetchFn = deps.fetchFn ?? globalThis.fetch;
  const getToken = deps.getToken ?? getPremiumAccessToken;
  const logger = deps.logger ?? console;

  try {
    const auth = await getToken();
    if (!auth.token) {
      return { ok: false, reason: "auth", message: "no_device_command_auth_token" };
    }

    const response = await fetchFn(
      `${deps.apiBase ?? apiBase()}/api/v1/device/devices/${encodeURIComponent(deviceId)}`,
      { headers: { Authorization: `Bearer ${auth.token}` } },
    );

    if (response.status === 404) {
      return { ok: false, reason: "not_provisioned", message: "device_not_provisioned" };
    }
    if (response.status === 401 || response.status === 403) {
      return { ok: false, reason: "auth", message: `HTTP ${response.status}` };
    }
    if (!response.ok) {
      logger.warn("[device-connection] backend error", response.status);
      return { ok: false, reason: "unknown", message: `HTTP ${response.status}` };
    }

    const body = await response.json();
    if (!isDeviceConnectionState(body)) {
      logger.warn("[device-connection] unexpected backend response", body);
      return { ok: false, reason: "unknown", message: "invalid_device_state_response" };
    }
    return { ok: true, state: body };
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    logger.warn("[device-connection] request failed", message);
    return { ok: false, reason: "network", message };
  }
}
