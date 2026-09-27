"use client";

import { useState } from "react";
import { motion } from "framer-motion";
import {
  ChevronLeft,
  ChevronRight,
  Globe,
  Wifi,
  Smartphone,
  HelpCircle,
  FileText,
  LogOut,
  Bell,
  Shield,
  CreditCard,
  Film,
} from "lucide-react";
import { languageLabels, memorialLang, memorialT } from "@/components/memorial/memorial-i18n";
import { SubscriptionTestPanel } from "@/components/memorial/subscription-test-panel";
import { MEMBERSHIP_SECTION_ID, MembershipSection } from "@/components/memorial/membership-section";
import { IdleGenerationTestPanel } from "@/components/memorial/idle-generation-test-panel";
import { SUBSCRIPTION_MOCK_ENABLED, IDLE_TEST_PANEL_ENABLED } from "@/lib/test-app-flags";
import { DestructiveButton } from "@/components/ui/buttons";

interface SettingsScreenProps {
  currentLanguage: string;
  userId?: string;
  onChangeLanguage: () => void;
  onDeviceSettings: () => void;
  onBack: () => void;
  onLogout: () => void;
  onCreditsChanged?: (remaining: number) => void;
  /** Memorial 의 "크레딧 받기" 로 들어왔는가 — 크레딧 섹션으로 스크롤·강조한다. */
  focusMembership?: boolean;
}

export function SettingsScreen({
  currentLanguage,
  userId = "demo-user",
  onChangeLanguage,
  onDeviceSettings,
  onBack,
  onLogout,
  onCreditsChanged,
  focusMembership,
}: SettingsScreenProps) {
  const s = memorialT(currentLanguage).settings;
  const lang = memorialLang(currentLanguage);
  const [showSubscriptionTest, setShowSubscriptionTest] = useState(false);
  const [showIdleTest, setShowIdleTest] = useState(false);

  const settingsGroups = [
    {
      title: s.device,
      items: [
        { id: "device", label: s.manageDevice, icon: Smartphone },
        { id: "wifi", label: s.wifi, icon: Wifi },
      ],
    },
    {
      title: s.preferences,
      items: [
        { id: "language", label: s.language, icon: Globe, hasValue: true },
        { id: "notifications", label: s.notifications, icon: Bell },
      ],
    },
    {
      title: s.account,
      items: [
        {
          id: "subscription",
          label: SUBSCRIPTION_MOCK_ENABLED ? s.subscriptionTest : s.subscription,
          icon: CreditCard,
        },
        { id: "privacy", label: s.privacy, icon: Shield },
        ...(IDLE_TEST_PANEL_ENABLED
          ? [{ id: "idle-test", label: "아이들 5종 테스트", icon: Film }]
          : []),
      ],
    },
    {
      title: s.support,
      items: [
        { id: "help", label: s.help, icon: HelpCircle },
        { id: "terms", label: s.terms, icon: FileText },
      ],
    },
  ];

  const handleItemClick = (id: string) => {
    switch (id) {
      case "language":
        onChangeLanguage();
        break;
      case "device":
      case "wifi":
        onDeviceSettings();
        break;
      case "subscription":
        if (SUBSCRIPTION_MOCK_ENABLED) {
          setShowSubscriptionTest((v) => !v);
        } else {
          document.getElementById(MEMBERSHIP_SECTION_ID)?.scrollIntoView({
            behavior: "smooth",
            block: "center",
          });
        }
        break;
      case "idle-test":
        if (IDLE_TEST_PANEL_ENABLED) {
          setShowIdleTest((v) => !v);
        }
        break;
      default:
        break;
    }
  };

  return (
    <div className="h-full flex flex-col relative overflow-hidden">
      <header className="px-6 pt-[var(--eb-header-top)] pb-4 flex items-center relative shrink-0">
        <button onClick={onBack} className="mem-icon-btn eb-back-btn -ml-2" aria-label="Back">
          <ChevronLeft className="w-5 h-5" />
        </button>
        <h1 className="screen-title eb-title absolute left-1/2 -translate-x-1/2">{s.title}</h1>
      </header>

      <div className="flex-1 min-h-0 overflow-y-auto px-4 pb-[var(--eb-footer-bottom)]">
        <div className="w-full max-w-2xl mx-auto">
        {/* 상시 노출 — 숨은 테스트 패널 뒤에 두지 않는다. Memorial 이 "설정에서
            충전하세요" 라고 안내하는데 실제 충전 UI 가 없던 것이 원래 문제였다. */}
        <MembershipSection language={currentLanguage} focusOnMount={focusMembership} />

        {showSubscriptionTest && SUBSCRIPTION_MOCK_ENABLED ? (
          <SubscriptionTestPanel
            userId={userId}
            language={currentLanguage}
            onClose={() => setShowSubscriptionTest(false)}
            onCreditsChanged={onCreditsChanged}
          />
        ) : null}

        {showIdleTest && IDLE_TEST_PANEL_ENABLED ? (
          <IdleGenerationTestPanel userId={userId} onClose={() => setShowIdleTest(false)} />
        ) : null}

        {settingsGroups.map((group, groupIndex) => (
          <motion.div
            key={group.title}
            initial={{ opacity: 0, y: 20 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ delay: groupIndex * 0.1 }}
            className="mb-6"
          >
            <p className="eb-section-title mb-2 px-2">{group.title.toUpperCase()}</p>

            <div className="eb-card overflow-hidden">
              {group.items.map((item, index) => {
                const active =
                  (item.id === "subscription" && showSubscriptionTest) ||
                  (item.id === "idle-test" && showIdleTest);
                return (
                  <button
                    key={item.id}
                    type="button"
                    onClick={() => handleItemClick(item.id)}
                    aria-expanded={
                      item.id === "subscription" || item.id === "idle-test" ? active : undefined
                    }
                    className="w-full min-h-[var(--eb-touch)] px-4 py-3 flex items-center justify-between gap-3 text-left transition-colors duration-[var(--eb-dur)] hover:bg-[var(--eb-surface-2)]"
                    style={{
                      borderBottom:
                        index < group.items.length - 1 ? "1px solid var(--eb-hairline)" : "none",
                      background: active ? "var(--eb-gold-wash)" : undefined,
                    }}
                  >
                    <div className="flex items-center gap-3 min-w-0">
                      <item.icon className="w-5 h-5 shrink-0 text-[var(--eb-text-2)]" />
                      <span className="text-[15px] text-[var(--eb-text)] truncate">
                        {item.label}
                      </span>
                    </div>
                    <div className="flex items-center gap-2 shrink-0">
                      {item.id === "language" && (
                        <span className="text-[13px] text-[var(--eb-text-3)]">
                          {languageLabels[lang]}
                        </span>
                      )}
                      <ChevronRight className="w-4 h-4 text-[var(--eb-text-3)]" />
                    </div>
                  </button>
                );
              })}
            </div>
          </motion.div>
        ))}

        <DestructiveButton
          block
          initial={{ opacity: 0, y: 20 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ delay: 0.4 }}
          onClick={onLogout}
          className="mt-4"
        >
          <span className="inline-flex items-center gap-2">
            <LogOut className="w-5 h-5" />
            {s.logout}
          </span>
        </DestructiveButton>

        <p className="eb-caption text-center mt-6">Eternal Beam v1.0.0</p>
        </div>
      </div>
    </div>
  );
}
