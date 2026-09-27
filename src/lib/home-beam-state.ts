/**
 * Home "My Beam" card — the only states the card may claim.
 *
 * The card reads the same live gateway snapshot as the full My Beam screen
 * (useDeviceConnection) and collapses it to what a user can act on:
 *
 *   setup     — no device id, or the gateway says the device is not
 *               provisioned: "Not set up" + connect
 *   checking  — first poll still in flight; we do not guess offline/online
 *   connected — gateway reports the Beam online
 *   offline   — gateway reports it offline, or we could not verify (auth /
 *               network / unknown): the Beam is not reachable right now
 *
 * No battery, no firmware, no capacity, no invented last-seen — the card
 * shows a status word and a real action, nothing else.
 */
export type HomeBeamCardState = "setup" | "checking" | "connected" | "offline";

export type HomeBeamConnectionStatus = "checking" | "connected" | "offline" | "unavailable";
export type HomeBeamUnavailableReason = "auth" | "not_provisioned" | "network" | "unknown" | null;

export function homeBeamCardState(input: {
  deviceId: string | null;
  status: HomeBeamConnectionStatus;
  unavailableReason: HomeBeamUnavailableReason;
}): HomeBeamCardState {
  if (input.deviceId == null) return "setup";
  if (input.status === "unavailable" && input.unavailableReason === "not_provisioned") return "setup";
  if (input.status === "checking") return "checking";
  if (input.status === "connected") return "connected";
  return "offline";
}
