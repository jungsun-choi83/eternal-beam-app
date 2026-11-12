"use client";

import type { ReactNode } from "react";
import { ArrowLeft } from "lucide-react";

const MARK_SRC = "/brand/eternal-beam-mark.png";

/** Official metal mark plus the wordmark. The mark file is not redrawn. */
export function PaperBrandLockup({
  size = 44,
  className = "",
}: {
  size?: number;
  className?: string;
}) {
  return (
    <div className={`eb-lockup ${className}`.trim()}>
      <img
        src={MARK_SRC}
        alt=""
        width={465}
        height={316}
        className="eb-lockup__mark"
        style={{ width: size, height: "auto" }}
        draggable={false}
      />
      <p className="eb-lockup__wordmark">Eternal Beam</p>
    </div>
  );
}

export function PaperFlowIntro({ title, lead }: { title: string; lead?: string }) {
  return (
    <div className="eb-paper-intro">
      <h1 className="eb-paper-kicker">{title}</h1>
      {lead ? <p className="eb-paper-lead">{lead}</p> : null}
    </div>
  );
}

export function PaperBrandHeader({
  onBack,
  backLabel = "뒤로",
  trailing,
  className = "",
}: {
  onBack?: () => void;
  backLabel?: string;
  trailing?: ReactNode;
  className?: string;
}) {
  return (
    <header className={`eb-paper-chrome ${className}`.trim()}>
      <div className="eb-paper-chrome__side">
        {onBack ? (
          <button type="button" className="eb-paper-chrome__back" onClick={onBack} aria-label={backLabel}>
            <ArrowLeft strokeWidth={1.75} aria-hidden />
          </button>
        ) : null}
      </div>
      <PaperBrandLockup />
      <div className="eb-paper-chrome__side eb-paper-chrome__side--end">{trailing}</div>
    </header>
  );
}
