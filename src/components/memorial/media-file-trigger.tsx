"use client";

import type { ReactNode } from "react";
import { MEDIA_FILE_ACCEPT } from "@/lib/media-file-kind";

type MediaFileTriggerProps = {
  onFile?: (file: File) => void;
  onFiles?: (files: File[]) => void;
  accept?: string;
  className?: string;
  disabled?: boolean;
  multiple?: boolean;
  children: ReactNode;
};

/**
 * Android(갤럭시) / iOS 공통 — 탭 영역 위에 투명 file input 을 덮어 갤러리를 연다.
 * programmatic input.click() 은 삼성 브라우저에서 막히는 경우가 많음.
 */
export function MediaFileTrigger({
  onFile,
  onFiles,
  accept = MEDIA_FILE_ACCEPT,
  className = "",
  disabled = false,
  multiple = false,
  children,
}: MediaFileTriggerProps) {
  return (
    <label
      className={`relative block overflow-hidden ${disabled ? "pointer-events-none opacity-50" : "cursor-pointer"} ${className}`}
      style={{ WebkitTapHighlightColor: "transparent" }}
    >
      <input
        type="file"
        accept={accept}
        disabled={disabled}
        multiple={multiple}
        className="absolute inset-0 z-[200] h-full w-full cursor-pointer opacity-0"
        style={{ color: "transparent", fontSize: 0, touchAction: "manipulation" }}
        onChange={(e) => {
          const files = Array.from(e.target.files ?? []);
          if (files.length > 0) {
            if (onFiles) onFiles(files);
            else if (onFile) onFile(files[0]);
          }
          e.target.value = "";
        }}
      />
      <div className="relative z-0 pointer-events-none">{children}</div>
    </label>
  );
}
