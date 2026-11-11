"use client";

import { memorialLang } from "@/components/memorial/memorial-i18n";

interface LanguageToggleProps {
  language?: string;
  onChange: (lang: "ko" | "en") => void;
  className?: string;
}

/** KO | EN 언어 전환 — SoulTrace paper pill */
export function LanguageToggle({
  language = "ko",
  onChange,
  className = "",
}: LanguageToggleProps) {
  const active = memorialLang(language);

  return (
    <div className={`eb-segmented font-display-en shrink-0 ${className}`} role="group" aria-label="Language">
      <button
        type="button"
        onClick={() => onChange("ko")}
        className="eb-segmented__item"
        aria-pressed={active === "ko"}
      >
        KO
      </button>
      <span className="eb-segmented__rule" aria-hidden>
        |
      </span>
      <button
        type="button"
        onClick={() => onChange("en")}
        className="eb-segmented__item"
        aria-pressed={active === "en"}
      >
        EN
      </button>
    </div>
  );
}
