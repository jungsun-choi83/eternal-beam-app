"use client";

import { useEffect, useRef, useState } from "react";
import { ChevronDown, LogOut, Settings, User } from "lucide-react";
import { LanguageToggle } from "@/components/memorial/language-toggle";

interface UserMenuProps {
  userName?: string;
  settingsLabel: string;
  logoutLabel: string;
  onSettings?: () => void;
  onLogout?: () => void;
  /** Language row inside the panel — omitted entirely unless both are given. */
  language?: string;
  onChangeLanguage?: (lang: "ko" | "en") => void;
}

function initialsFor(name: string): string {
  const parts = name.trim().split(/\s+/).filter(Boolean);
  if (parts.length === 0) return "";
  if (parts.length === 1) return parts[0].slice(0, 2).toUpperCase();
  return `${parts[0][0]}${parts[parts.length - 1][0]}`.toUpperCase();
}

/** Desktop nav trailing slot: avatar + name + a settings/logout menu. Real user data only — no fake account fields. */
export function UserMenu({
  userName,
  settingsLabel,
  logoutLabel,
  onSettings,
  onLogout,
  language,
  onChangeLanguage,
}: UserMenuProps) {
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);
  const initials = userName ? initialsFor(userName) : "";

  useEffect(() => {
    if (!open) return;
    const onPointerDown = (event: PointerEvent) => {
      if (rootRef.current && !rootRef.current.contains(event.target as Node)) setOpen(false);
    };
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") setOpen(false);
    };
    document.addEventListener("pointerdown", onPointerDown);
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("pointerdown", onPointerDown);
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [open]);

  return (
    <div className="eb-user-menu" ref={rootRef}>
      <button
        type="button"
        className="eb-user-menu__trigger"
        onClick={() => setOpen((v) => !v)}
        aria-haspopup="menu"
        aria-expanded={open}
      >
        <span className="eb-user-menu__avatar" aria-hidden>
          {initials || <User className="w-4 h-4" />}
        </span>
        {userName ? <span className="eb-user-menu__name">{userName}</span> : null}
        <ChevronDown className="w-4 h-4 shrink-0" aria-hidden />
      </button>

      {open ? (
        <div className="eb-user-menu__panel" role="menu">
          {onChangeLanguage ? (
            <div className="eb-user-menu__lang" role="none">
              <LanguageToggle language={language} onChange={onChangeLanguage} />
            </div>
          ) : null}
          {onSettings ? (
            <button
              type="button"
              role="menuitem"
              className="eb-user-menu__item"
              onClick={() => {
                setOpen(false);
                onSettings();
              }}
            >
              <Settings className="w-4 h-4" aria-hidden />
              <span>{settingsLabel}</span>
            </button>
          ) : null}
          {onLogout ? (
            <button
              type="button"
              role="menuitem"
              className="eb-user-menu__item eb-user-menu__item--danger"
              onClick={() => {
                setOpen(false);
                onLogout();
              }}
            >
              <LogOut className="w-4 h-4" aria-hidden />
              <span>{logoutLabel}</span>
            </button>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}
