/**
 * Explicit "Play on Beam" for a PUBLISHED pet — the only production path that
 * sends anything to the device.
 *
 * theme_play → await success → pet_asset → await success. Both commands go
 * through the modern gateway (device-command-api → /api/v1/device/commands).
 * Nothing here is automatic: publication never calls this; only a user press
 * does. A missing paired device or pet is reported as a real failure so the
 * caller can show a truthful error instead of a fake "Sent".
 */

import {
  resolvePairedDeviceId,
  sendDeviceCommand,
  type DeviceCommandPayload,
  type DeviceCommandResult,
} from "./device-command-api.ts";

export type PlayOnBeamResult =
  | { ok: true; commandId?: string }
  | { ok: false; reason: string; status?: number };

export interface PlayOnBeamInput {
  themeKey: string;
  petId: string | null;
  motionId: string;
}

export interface PlayOnBeamDeps {
  send?: (payload: DeviceCommandPayload) => Promise<DeviceCommandResult>;
  resolveDeviceId?: () => string | null;
}

export async function playPublishedOnBeam(
  input: PlayOnBeamInput,
  deps: PlayOnBeamDeps = {},
): Promise<PlayOnBeamResult> {
  const send = deps.send ?? sendDeviceCommand;
  const deviceId = (deps.resolveDeviceId ?? resolvePairedDeviceId)();
  if (!deviceId) return { ok: false, reason: "no_paired_device" };
  if (!input.petId) return { ok: false, reason: "no_published_pet" };

  const themeResult = await send({
    device_id: deviceId,
    event: "theme_play",
    theme_id: input.themeKey,
  });
  if (!themeResult.ok) {
    return { ok: false, reason: themeResult.reason, status: themeResult.status };
  }

  const petResult = await send({
    device_id: deviceId,
    event: "pet_asset",
    pet_id: input.petId,
    motion_id: input.motionId,
    spawn_vfx: "heart",
  });
  return petResult.ok
    ? { ok: true, commandId: petResult.command_id }
    : { ok: false, reason: petResult.reason, status: petResult.status };
}
