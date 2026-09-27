"use client";

import { memorialLang } from "@/components/memorial/memorial-i18n";

interface LanguageToggleProps {
  language?: string;
  onChange: (lang: "ko" | "en") => void;
  className?: string;
}

/** KO / EN 언어 전환 (온보딩·인증·홈 등 공통) — brand.css .eb-segmented */
export function LanguageToggle({
  language = "ko",
  onChange,
  className = "",
}: LanguageToggleProps) {
  const active = memorialLang(language);

  return (
    <div className={`eb-segmented shrink-0 ${className}`} role="group" aria-label="Language">
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
            className="eb-segmented__item"
            aria-pressed={selected}
          >
            {label}
          </button>
        );
      })}
    </div>
  );
}
