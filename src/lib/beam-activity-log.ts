/**
 * Local record of Beam commands this browser has actually sent.
 *
 * There is no backend "command history" endpoint — the gateway only tracks
 * pending commands and the single last-acknowledged command_id per device
 * (backend/services/device_gateway.py). So "recent playback" on My Beam is
 * built from what this browser itself sent, not a fabricated activity feed.
 */

const STORAGE_KEY = "eternal_beam_recent_commands";
const MAX_ENTRIES = 5;

export type BeamActivityEvent = "theme_play" | "pet_asset";
export type BeamActivityDelivery = "sent" | "pending" | "acked" | "failed";

export interface BeamActivityEntry {
  commandId?: string;
  event: BeamActivityEvent;
  /** theme_key for theme_play, pet_id for pet_asset — kept short, no invented labels. */
  label: string;
  delivery: BeamActivityDelivery;
  reason?: string;
  at: string;
}

function readAll(): BeamActivityEntry[] {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw);
    return Array.isArray(parsed) ? parsed.filter(isEntry) : [];
  } catch {
    return [];
  }
}

function isEntry(value: unknown): value is BeamActivityEntry {
  return (
    typeof value === "object" &&
    value !== null &&
    typeof (value as BeamActivityEntry).event === "string" &&
    typeof (value as BeamActivityEntry).delivery === "string" &&
    typeof (value as BeamActivityEntry).at === "string"
  );
}

/** Most-recent-first, capped at MAX_ENTRIES. */
export function recordBeamCommand(entry: Omit<BeamActivityEntry, "at">): void {
  try {
    const next = [{ ...entry, at: new Date().toISOString() }, ...readAll()].slice(0, MAX_ENTRIES);
    localStorage.setItem(STORAGE_KEY, JSON.stringify(next));
  } catch {
    /* private mode / quota — recent activity is a convenience, not a source of truth */
  }
}

export function getRecentBeamCommands(): BeamActivityEntry[] {
  return readAll();
}

/**
 * Upgrade a still-pending/sent entry to "acknowledged" once the device
 * gateway reports it as the last_ack for this device. Returns a new list —
 * callers that display this should re-render from the return value.
 */
export function reconcileAckedCommand(commandId: string | null): BeamActivityEntry[] {
  if (!commandId) return readAll();
  const all = readAll();
  const next = all.map((entry) =>
    entry.commandId === commandId && entry.delivery !== "failed"
      ? { ...entry, delivery: "acked" as const }
      : entry
  );
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(next));
  } catch {
    /* ignore */
  }
  return next;
}

export function clearBeamActivity(): void {
  try {
    localStorage.removeItem(STORAGE_KEY);
  } catch {
    /* ignore */
  }
}
