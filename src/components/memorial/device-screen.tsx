"use client";

import { useEffect, useMemo, useState } from "react";
import { motion } from "framer-motion";
import {
  ChevronLeft,
  Wifi,
  WifiOff,
  RefreshCw,
  Loader2,
  AlertTriangle,
  LogIn,
} from "lucide-react";
import { demoDeviceId } from "@/lib/device-command-api";
import {
  getRecentBeamCommands,
  reconcileAckedCommand,
  type BeamActivityEntry,
} from "@/lib/beam-activity-log";
import { StatusBadge, StatusDot, type StatusTone } from "@/components/ui/status-badge";
import { useDeviceConnection } from "./use-device-connection";
import { memorialLang, memorialT } from "./memorial-i18n";
import { formatRelative } from "@/lib/relative-time";
import { BEAM_STATUS_TONE, beamDisplayStatus, beamStatusLabel } from "@/lib/beam-status";

interface DeviceScreenProps {
  onBack: () => void;
  language?: string;
}

const DELIVERY_TONE: Record<BeamActivityEntry["delivery"], StatusTone> = {
  acked: "success",
  sent: "waiting",
  pending: "waiting",
  failed: "error",
};

function activityLabel(entry: BeamActivityEntry, t: ReturnType<typeof memorialT>["device"]): string {
  const kind = entry.event === "theme_play" ? t.activityTheme : t.activityPet;
  const delivery =
    entry.delivery === "acked"
      ? t.activityAcked
      : entry.delivery === "sent"
        ? t.activitySent
        : entry.delivery === "pending"
          ? t.activityPending
          : t.activityFailed;
  return `${kind} · ${delivery}`;
}

export function DeviceScreen({ onBack, language = "ko" }: DeviceScreenProps) {
  const lang = memorialLang(language);
  const t = memorialT(language).device;
  const deviceId = useMemo(() => demoDeviceId(), []);
  const { status, state, unavailableReason, isChecking, retry } = useDeviceConnection(deviceId);
  const [activity, setActivity] = useState<BeamActivityEntry[]>([]);

  useEffect(() => {
    setActivity(state?.last_ack ? reconcileAckedCommand(state.last_ack) : getRecentBeamCommands());
  }, [status, state?.last_ack]);

  const displayStatus = beamDisplayStatus(status, isChecking);
  const reconnecting = displayStatus === "reconnecting";
  const tone: StatusTone = BEAM_STATUS_TONE[displayStatus] ?? "neutral";
  const statusLabel = beamStatusLabel(displayStatus, unavailableReason, t);

  const retryLabel = isChecking
    ? t.retryChecking
    : status === "connected"
      ? t.retryConnected
      : status === "offline"
        ? t.retryOffline
        : t.retryUnavailable;

  const StatusIcon =
    displayStatus === "connected" ? Wifi : displayStatus === "reconnecting" ? Loader2 : WifiOff;

  return (
    <div className="h-full flex flex-col relative overflow-hidden eb-beam">
      <header className="px-6 pt-[var(--eb-header-top)] pb-4 flex items-center relative shrink-0">
        <button onClick={onBack} className="mem-icon-btn eb-back-btn -ml-2" aria-label="Back">
          <ChevronLeft className="w-5 h-5" />
        </button>
        <h1 className="screen-title eb-title absolute left-1/2 -translate-x-1/2">{t.title}</h1>
      </header>

      <div className="flex-1 min-h-0 overflow-y-auto px-4 pb-[var(--eb-footer-bottom)] eb-beam__scroll">
        <div className="eb-beam__grid">
          {/* Connection overview */}
          <motion.div
            initial={{ opacity: 0, y: 20 }}
            animate={{ opacity: 1, y: 0 }}
            className="eb-beam__card eb-card eb-beam__overview"
          >
            <div className="eb-beam__badge-wrap">
              <div className="eb-beam__badge" data-tone={tone}>
                <StatusIcon
                  className={`w-9 h-9 ${displayStatus === "reconnecting" ? "animate-spin" : ""}`}
                />
              </div>
            </div>

            <p className="eb-beam__device-label">{t.deviceIdLabel}</p>
            <h2 className="eb-beam__device-id">{deviceId}</h2>

            <div className="eb-beam__status-row" role="status">
              <StatusBadge tone={tone}>{statusLabel}</StatusBadge>
            </div>

            {status === "unavailable" && unavailableReason === "not_provisioned" ? (
              <div className="eb-beam__notice eb-notice eb-notice--warning">
                <AlertTriangle className="w-4 h-4 shrink-0 mt-0.5" />
                <div>
                  <p className="eb-beam__notice-title">{t.notSetUpTitle}</p>
                  <p className="eb-beam__notice-body">{t.notSetUpBody}</p>
                </div>
              </div>
            ) : null}

            {status === "unavailable" && unavailableReason === "auth" ? (
              <div className="eb-beam__notice eb-notice eb-notice--warning">
                <LogIn className="w-4 h-4 shrink-0 mt-0.5" />
                <div>
                  <p className="eb-beam__notice-title">{t.signInRequiredTitle}</p>
                  <p className="eb-beam__notice-body">{t.signInRequiredBody}</p>
                </div>
              </div>
            ) : null}

            <button
              onClick={retry}
              disabled={isChecking}
              aria-busy={isChecking || undefined}
              className="eb-beam__retry-btn eb-btn eb-btn--secondary mem-btn-secondary eb-btn--block"
            >
              <RefreshCw className={`w-4 h-4 ${isChecking ? "animate-spin" : ""}`} />
              <span>{retryLabel}</span>
            </button>
          </motion.div>

          {/* Status detail panel (desktop: side-by-side with overview) */}
          {state ? (
            <motion.div
              initial={{ opacity: 0, y: 20 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ delay: 0.05 }}
              className="eb-beam__card eb-card eb-beam__detail"
            >
              <div className="eb-beam__detail-row">
                <span className="eb-beam__detail-label">{t.connectedSinceLabel}</span>
                <span className="eb-beam__detail-value">
                  {formatRelative(state.connected_at, lang, t.never)}
                </span>
              </div>
              <div className="eb-beam__detail-row">
                <span className="eb-beam__detail-label">{t.lastSeenLabel}</span>
                <span className="eb-beam__detail-value">
                  {formatRelative(state.last_seen, lang, t.never)}
                </span>
              </div>
            </motion.div>
          ) : null}

          {/* Recent playback / control — sourced only from commands this browser sent */}
          <motion.div
            initial={{ opacity: 0, y: 20 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ delay: 0.1 }}
            className="eb-beam__card eb-card eb-beam__activity"
          >
            <h3 className="eb-beam__section-title eb-section-title">{t.recentActivityTitle}</h3>
            {activity.length === 0 ? (
              <p className="eb-beam__empty">{t.noRecentActivity}</p>
            ) : (
              <ul className="eb-beam__activity-list">
                {activity.map((entry, index) => (
                  <li key={`${entry.at}-${index}`} className="eb-beam__activity-row">
                    <StatusDot
                      tone={DELIVERY_TONE[entry.delivery] ?? "neutral"}
                      pulse={entry.delivery === "pending" || entry.delivery === "sent"}
                      className="eb-beam__activity-dot"
                    />
                    <span className="eb-beam__activity-text">{activityLabel(entry, t)}</span>
                    <span className="eb-beam__activity-time">
                      {formatRelative(entry.at, lang, t.never)}
                    </span>
                  </li>
                ))}
              </ul>
            )}
          </motion.div>
        </div>
      </div>
    </div>
  );
}
