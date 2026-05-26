"use client";

import { memorialLang } from "@/components/memorial/memorial-i18n";

interface LanguageToggleProps {
  language?: string;
  onChange: (lang: "ko" | "en") => void;
  className?: string;
}

/** 홈 상단 KO / EN 토글 */
export function LanguageToggle({
  language = "ko",
  onChange,
  className = "",
}: LanguageToggleProps) {
  const active = memorialLang(language);

  return (
    <div
      className={`flex items-center rounded-xl p-0.5 shrink-0 ${className}`}
      style={{
        background: "rgba(255, 255, 255, 0.08)",
        border: "1px solid rgba(255, 255, 255, 0.1)",
      }}
      role="group"
      aria-label="Language"
    >
      {(
        [
          { code: "ko" as const, label: "한국어" },
          { code: "en" as const, label: "EN" },
        ] as const
      ).map(({ code, label }) => {
        const selected = active === code;
        return (
          <button
            key={code}
            type="button"
            onClick={() => onChange(code)}
            className="px-2.5 py-1.5 rounded-[10px] text-[11px] font-medium transition-colors min-w-[2.75rem]"
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
