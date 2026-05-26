"use client";

import type { ReactNode } from "react";
import { MEDIA_FILE_ACCEPT } from "@/lib/media-file-kind";

type MediaFileTriggerProps = {
  onFile: (file: File) => void;
  accept?: string;
  className?: string;
  disabled?: boolean;
  children: ReactNode;
};

/**
 * Android(갤럭시) / iOS 공통 — 탭 영역 위에 투명 file input 을 덮어 갤러리를 연다.
 * programmatic input.click() 은 삼성 브라우저에서 막히는 경우가 많음.
 */
export function MediaFileTrigger({
  onFile,
  accept = MEDIA_FILE_ACCEPT,
  className = "",
  disabled = false,
  children,
}: MediaFileTriggerProps) {
  return (
    <label
      className={`relative block ${disabled ? "pointer-events-none opacity-50" : "cursor-pointer"} ${className}`}
      style={{ WebkitTapHighlightColor: "transparent" }}
    >
      <input
        type="file"
        accept={accept}
        disabled={disabled}
        className="absolute inset-0 z-[200] h-full w-full opacity-[0.02] cursor-pointer"
        style={{ fontSize: 16, touchAction: "manipulation" }}
        onChange={(e) => {
          const file = e.target.files?.[0];
          if (file) onFile(file);
          e.target.value = "";
        }}
      />
      <div className="relative z-0 pointer-events-none">{children}</div>
    </label>
  );
}
