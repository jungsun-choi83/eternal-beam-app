import type { StatusTone } from "@/components/ui/status-badge";
import type { BeamConnectionStatus } from "@/components/memorial/use-device-connection";
import type { memorialT } from "@/components/memorial/memorial-i18n";
import type { DeviceConnectionFailureReason } from "@/lib/device-connection-api";

/** Display status → shared status tone (brand.css). One gold highlight (connected); rest neutral/ochre/terracotta. */
export const BEAM_STATUS_TONE: Record<string, StatusTone> = {
  connected: "connected",
  offline: "offline",
  reconnecting: "waiting",
  unavailable: "error",
  checking: "loading",
};

type DeviceTexts = ReturnType<typeof memorialT>["device"];

export function beamDisplayStatus(
  status: BeamConnectionStatus,
  isChecking: boolean,
): BeamConnectionStatus | "reconnecting" {
  return isChecking && (status === "offline" || status === "unavailable") ? "reconnecting" : status;
}

export function beamStatusLabel(
  displayStatus: BeamConnectionStatus | "reconnecting",
  unavailableReason: DeviceConnectionFailureReason | null,
  t: DeviceTexts,
): string {
  if (displayStatus === "connected") return t.statusConnected;
  if (displayStatus === "offline") return t.statusOffline;
  if (displayStatus === "reconnecting") return t.statusReconnecting;
  if (displayStatus === "checking") return t.statusChecking;
  if (unavailableReason === "auth") return t.statusUnavailableAuth;
  if (unavailableReason === "not_provisioned") return t.statusUnavailableNotProvisioned;
  if (unavailableReason === "network") return t.statusUnavailableNetwork;
  return t.statusUnavailableUnknown;
}
