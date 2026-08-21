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
import { MembershipSection } from "@/components/memorial/membership-section";
import { IdleGenerationTestPanel } from "@/components/memorial/idle-generation-test-panel";
import { SUBSCRIPTION_MOCK_ENABLED, IDLE_TEST_PANEL_ENABLED } from "@/lib/test-app-flags";

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
      <header className="px-6 pt-14 pb-4 flex items-center relative">
        <button onClick={onBack} className="p-2 -ml-2">
          <ChevronLeft className="w-5 h-5" style={{ color: "#F5F5F7" }} />
        </button>
        <h1
          className="text-xl font-medium absolute left-1/2 -translate-x-1/2"
          style={{ color: "#F5F5F7" }}
        >
          {s.title}
        </h1>
      </header>

      <div className="flex-1 overflow-y-auto px-4 pb-8">
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
            <p className="text-[11px] tracking-[0.12em] mb-2 px-2" style={{ color: "rgba(245,245,247,0.56)" }}>
              {group.title.toUpperCase()}
            </p>

            <div
              className="rounded-2xl overflow-hidden"
              style={{
                background: "rgba(255, 255, 255, 0.045)",
                border: "1px solid rgba(255, 255, 255, 0.10)",
              }}
            >
              {group.items.map((item, index) => (
                <button
                  key={item.id}
                  onClick={() => handleItemClick(item.id)}
                  className="w-full px-4 py-[15px] flex items-center justify-between transition-colors hover:bg-white/5"
                  style={{
                    borderBottom:
                      index < group.items.length - 1
                        ? "1px solid rgba(255, 255, 255, 0.09)"
                        : "none",
                    background:
                      (item.id === "subscription" && showSubscriptionTest) ||
                      (item.id === "idle-test" && showIdleTest)
                        ? "rgba(201, 162, 39, 0.06)"
                        : undefined,
                  }}
                >
                  <div className="flex items-center gap-3">
                    <item.icon className="w-5 h-5" style={{ color: "rgba(245,245,247,0.72)" }} />
                    <span className="text-[15px]" style={{ color: "#F5F5F7" }}>
                      {item.label}
                    </span>
                  </div>
                  <div className="flex items-center gap-2">
                    {item.id === "language" && (
                      <span className="text-[13px]" style={{ color: "rgba(245,245,247,0.58)" }}>
                        {languageLabels[lang]}
                      </span>
                    )}
                    <ChevronRight className="w-4 h-4" style={{ color: "rgba(245,245,247,0.58)" }} />
                  </div>
                </button>
              ))}
            </div>
          </motion.div>
        ))}

        <motion.button
          initial={{ opacity: 0, y: 20 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ delay: 0.4 }}
          onClick={onLogout}
          className="w-full py-4 rounded-2xl flex items-center justify-center gap-2 mt-4"
          style={{
            background: "rgba(255, 59, 48, 0.1)",
            border: "1px solid rgba(255, 59, 48, 0.2)",
          }}
        >
          <LogOut className="w-5 h-5 text-red-400" />
          <span className="text-red-400 font-medium">{s.logout}</span>
        </motion.button>

        <p className="text-center text-xs mt-6" style={{ color: "#A1A1A6" }}>
          Eternal Beam v1.0.0
        </p>
      </div>
    </div>
  );
}
