"use client";

import { memorialLang } from "@/components/memorial/memorial-i18n";

interface LanguageToggleProps {
  language?: string;
  onChange: (lang: "ko" | "en") => void;
  className?: string;
}

/** KO / EN 언어 전환 (온보딩·인증·홈 등 공통) */
export function LanguageToggle({
  language = "ko",
  onChange,
  className = "",
}: LanguageToggleProps) {
  const active = memorialLang(language);

  return (
    <div
      className={`glass-panel flex items-center rounded-xl p-0.5 shrink-0 ${className}`}
      role="group"
      aria-label="Language"
    >
      {(
        [
          { code: "ko" as const, label: "KR" },
          { code: "en" as const, label: "EN" },
        ] as const
      ).map(({ code, label }) => {
        const selected = active === code;
        return (
          <button
            key={code}
            type="button"
            onClick={() => onChange(code)}
            className="px-2 py-1 rounded-[10px] text-[10px] font-semibold tracking-wide transition-colors min-w-[2rem]"
            style={{
              background: selected ? "rgba(201, 162, 39, 0.28)" : "transparent",
              color: selected ? "#f5d77a" : "#a1a1a6",
              boxShadow: selected ? "inset 0 0 0 1px rgba(201, 162, 39, 0.35)" : "none",
            }}
            aria-pressed={selected}
          >
            {label}
          </button>
        );
      })}
    </div>
  );
}
