"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import {
  getDeviceConnectionState,
  type DeviceConnectionResult,
  type DeviceConnectionState,
} from "@/lib/device-connection-api";

export type BeamConnectionStatus = "checking" | "connected" | "offline" | "unavailable";

const POLL_INTERVAL_MS = 10_000;

export interface DeviceConnectionSnapshot {
  status: BeamConnectionStatus;
  state: DeviceConnectionState | null;
  /** Why we can't currently verify status (auth/network/not_provisioned) — set only when unavailable. */
  unavailableReason: "auth" | "not_provisioned" | "network" | "unknown" | null;
  /** True while a manual retry or the next poll tick is in flight. */
  isChecking: boolean;
  lastCheckedAt: number | null;
  retry: () => void;
}

function statusFor(result: DeviceConnectionResult): {
  status: BeamConnectionStatus;
  unavailableReason: DeviceConnectionSnapshot["unavailableReason"];
} {
  if (result.ok) {
    return { status: result.state.online ? "connected" : "offline", unavailableReason: null };
  }
  return { status: "unavailable", unavailableReason: result.reason };
}

/**
 * Polls the device gateway's connection state. No fake states while loading —
 * starts "checking". `deviceId: null` means no real device is paired (no
 * per-user pairing exists yet — see resolvePairedDeviceId): this never
 * fetches or polls a nonexistent device and reports "unavailable" so callers
 * can render a truthful "set up your device" state instead of guessing.
 */
export function useDeviceConnection(deviceId: string | null): DeviceConnectionSnapshot {
  const [status, setStatus] = useState<BeamConnectionStatus>(deviceId == null ? "unavailable" : "checking");
  const [state, setState] = useState<DeviceConnectionState | null>(null);
  const [unavailableReason, setUnavailableReason] = useState<DeviceConnectionSnapshot["unavailableReason"]>(
    deviceId == null ? "not_provisioned" : null,
  );
  const [isChecking, setIsChecking] = useState(false);
  const [lastCheckedAt, setLastCheckedAt] = useState<number | null>(null);
  const mountedRef = useRef(true);

  const check = useCallback(async () => {
    if (deviceId == null) return;
    setIsChecking(true);
    const result = await getDeviceConnectionState(deviceId);
    if (!mountedRef.current) return;
    const next = statusFor(result);
    setStatus(next.status);
    setUnavailableReason(next.unavailableReason);
    setState(result.ok ? result.state : null);
    setLastCheckedAt(Date.now());
    setIsChecking(false);
  }, [deviceId]);

  useEffect(() => {
    if (deviceId == null) return;
    mountedRef.current = true;
    void check();
    const interval = setInterval(() => void check(), POLL_INTERVAL_MS);
    return () => {
      mountedRef.current = false;
      clearInterval(interval);
    };
  }, [deviceId, check]);

  return { status, state, unavailableReason, isChecking, lastCheckedAt, retry: check };
}
