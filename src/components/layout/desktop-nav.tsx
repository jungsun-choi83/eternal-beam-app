"use client";

import type { ReactNode } from "react";

export type DesktopNavKey = "home" | "library" | "create" | "device" | "settings";

interface DesktopNavItem {
  key: DesktopNavKey;
  label: string;
}

const NAV_ITEMS: DesktopNavItem[] = [
  { key: "home", label: "Home" },
  { key: "library", label: "Library" },
  { key: "create", label: "Create" },
  { key: "device", label: "My Beam" },
];

interface DesktopNavProps {
  active?: DesktopNavKey;
  onNavigate: (key: DesktopNavKey) => void;
  onHome?: () => void;
  right?: ReactNode;
}

/**
 * 데스크톱(1024px+) 전용 상단 내비게이션. 모바일/태블릿에서는 foundation.css 의
 * 미디어쿼리로 display:none 처리된다 — 별도 분기 없이 항상 마운트해도 된다.
 *
 * "모바일에는 데스크톱 내비게이션을 강제하지 않는다" — 모바일 화면은 기존
 * 화면별 컴팩트 헤더/뒤로가기를 그대로 쓴다.
 */
export function DesktopNav({ active, onNavigate, onHome, right }: DesktopNavProps) {
  return (
    <header className="eb-desktop-nav" role="banner">
      <div className="eb-desktop-nav__inner">
        <button
          type="button"
          className="eb-desktop-nav__brand logo-title"
          onClick={onHome}
          aria-label="Eternal Beam Home"
        >
          Eternal Beam
        </button>
        <nav className="eb-desktop-nav__links" aria-label="Primary">
          {NAV_ITEMS.map((item) => (
            <button
              key={item.key}
              type="button"
              className={`eb-desktop-nav__link${
                active === item.key ? " eb-desktop-nav__link--active" : ""
              }`}
              aria-current={active === item.key ? "page" : undefined}
              onClick={() => onNavigate(item.key)}
            >
              {item.label}
            </button>
          ))}
        </nav>
        <div className="eb-desktop-nav__trailing">{right}</div>
      </div>
    </header>
  );
}
