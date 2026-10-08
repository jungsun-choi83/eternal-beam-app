"use client";

import type { ReactNode } from "react";
import { Home, Library, Plus, Radio } from "lucide-react";
import type { DesktopNavKey } from "./desktop-nav";

type MobileNavKey = Extract<DesktopNavKey, "home" | "library" | "create" | "device">;

interface MobileNavItem {
  key: MobileNavKey;
  label: string;
  icon: ReactNode;
}

const NAV_ITEMS: MobileNavItem[] = [
  { key: "home", label: "Home", icon: <Home className="eb-mobile-nav__icon" aria-hidden /> },
  { key: "library", label: "Library", icon: <Library className="eb-mobile-nav__icon" aria-hidden /> },
  { key: "create", label: "Create", icon: <Plus className="eb-mobile-nav__icon" aria-hidden /> },
  { key: "device", label: "My Beam", icon: <Radio className="eb-mobile-nav__icon" aria-hidden /> },
];

interface MobileNavProps {
  active?: DesktopNavKey;
  onNavigate: (key: DesktopNavKey) => void;
}

/**
 * Mobile/tablet (<1024px) persistent bottom nav — the compact counterpart to
 * DesktopNav. Always mounted whenever DesktopNav would be (same
 * DESKTOP_NAV_HIDDEN_SCREENS gate in EternalBeamApp); foundation.css shows
 * this only below 1024px and DesktopNav only at/above it, so exactly one is
 * ever visible. Settings/profile stays in UserMenu — not a 5th item here.
 */
export function MobileNav({ active, onNavigate }: MobileNavProps) {
  return (
    <nav className="eb-mobile-nav" aria-label="Primary">
      {NAV_ITEMS.map((item) => (
        <button
          key={item.key}
          type="button"
          className={`eb-mobile-nav__link${
            active === item.key ? " eb-mobile-nav__link--active" : ""
          }`}
          aria-current={active === item.key ? "page" : undefined}
          onClick={() => onNavigate(item.key)}
        >
          {item.icon}
          <span className="eb-mobile-nav__label">{item.label}</span>
        </button>
      ))}
    </nav>
  );
}
