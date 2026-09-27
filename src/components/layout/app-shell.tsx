"use client";

import { ReactNode } from "react";
import { motion } from "framer-motion";
import { MemorialSparkleField } from "@/components/memorial/memorial-sparkle-field";

interface AppShellProps {
  children: ReactNode;
  /** 데스크톱 헤더(DesktopNav). 모바일/태블릿에서는 CSS 로 숨는다. */
  nav?: ReactNode;
  /** 모바일/태블릿 하단 내비게이션(MobileNav). 데스크톱에서는 CSS 로 숨는다. */
  mobileNav?: ReactNode;
  /**
   * 데스크톱에서 스테이지 폭을 넓힌다(기본 520px → 1120px).
   *
   * 대부분의 화면은 한 번에 한 가지 일(사진 업로드·테마 선택 등)만 하는
   * 폰 목업형 흐름이라 520px 로 충분하다. 대시보드형 화면(Home)은 여러 섹션을
   * 나란히 배치해야 하므로 그 폭 안에 넣으면 "데스크톱 안의 폰 화면"이 된다 —
   * 이 화면만 옵트인으로 넓힌다.
   */
  wide?: boolean;
  /** Remove the desktop stage frame for page-style dashboards such as Home. */
  unframed?: boolean;
}

/**
 * 반응형 앱 셸 — 이전 MobileFrame 의 375×812 고정 폰 목업을 대체한다.
 *
 * 모바일(<768px)·태블릿(768–1023px): 전체 화면, 베젤 없음.
 * 데스크톱(1024px+): 가운데 정렬된 max-width 애플리케이션 레이아웃 — 폰 목업이
 * 아니라 위에 DesktopNav 가 붙는 실제 웹 앱 구조다. 높이는 고정 px 이 아니라
 * flex 로 뷰포트에 맞춰 늘고 줄어든다 — 짧은 노트북 화면에서 CTA/푸터가
 * 잘리지 않는다.
 */
export function AppShell({ children, nav, mobileNav, wide = false, unframed = false }: AppShellProps) {
  return (
    <motion.div
      className="app-shell"
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      transition={{ duration: 0.5, ease: [0.25, 0.46, 0.45, 0.94] }}
    >
      {nav}
      <div
        className={`app-shell__stage${wide ? " app-shell__stage--wide" : ""}${
          unframed ? " app-shell__stage--unframed" : ""
        }`}
      >
        <div className="memorial-ui app-shell__surface">
          <MemorialSparkleField />
          <div className="relative z-[2] h-full w-full overflow-hidden">{children}</div>
        </div>
      </div>
      {mobileNav}
    </motion.div>
  );
}
