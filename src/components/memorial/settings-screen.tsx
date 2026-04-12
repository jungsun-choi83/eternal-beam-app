"use client";

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
  CreditCard
} from "lucide-react";

interface SettingsScreenProps {
  currentLanguage: string;
  onChangeLanguage: () => void;
  onDeviceSettings: () => void;
  onBack: () => void;
  onLogout: () => void;
}

const settingsGroups = [
  {
    title: "Device",
    items: [
      { id: "device", label: "Manage Device", icon: Smartphone },
      { id: "wifi", label: "Wi-Fi Settings", icon: Wifi },
    ],
  },
  {
    title: "Preferences",
    items: [
      { id: "language", label: "Language", icon: Globe, hasValue: true },
      { id: "notifications", label: "Notifications", icon: Bell },
    ],
  },
  {
    title: "Account",
    items: [
      { id: "subscription", label: "Subscription", icon: CreditCard },
      { id: "privacy", label: "Privacy & Security", icon: Shield },
    ],
  },
  {
    title: "Support",
    items: [
      { id: "help", label: "Help Center", icon: HelpCircle },
      { id: "terms", label: "Terms of Service", icon: FileText },
    ],
  },
];

const languageLabels: Record<string, string> = {
  en: "English",
  ko: "Korean",
  zh: "Chinese",
  ja: "Japanese",
};

export function SettingsScreen({ 
  currentLanguage, 
  onChangeLanguage, 
  onDeviceSettings, 
  onBack,
  onLogout 
}: SettingsScreenProps) {
  
  const handleItemClick = (id: string) => {
    switch (id) {
      case "language":
        onChangeLanguage();
        break;
      case "device":
      case "wifi":
        onDeviceSettings();
        break;
      default:
        break;
    }
  };

  return (
    <div className="h-full flex flex-col bg-[#0a0a0a] relative overflow-hidden">
      {/* Header */}
      <header className="px-6 pt-14 pb-4 flex items-center relative">
        <button onClick={onBack} className="p-2 -ml-2">
          <ChevronLeft className="w-5 h-5" style={{ color: "#F5F5F7" }} />
        </button>
        <h1 className="text-xl font-light absolute left-1/2 -translate-x-1/2" style={{ color: "#F5F5F7" }}>
          Settings
        </h1>
      </header>

      {/* Settings List */}
      <div className="flex-1 overflow-y-auto px-4 pb-8">
        {settingsGroups.map((group, groupIndex) => (
          <motion.div
            key={group.title}
            initial={{ opacity: 0, y: 20 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ delay: groupIndex * 0.1 }}
            className="mb-6"
          >
            <p 
              className="text-xs tracking-wide mb-2 px-2"
              style={{ color: "#A1A1A6" }}
            >
              {group.title.toUpperCase()}
            </p>
            
            <div
              className="rounded-2xl overflow-hidden"
              style={{
                background: "rgba(255, 255, 255, 0.03)",
                border: "1px solid rgba(255, 255, 255, 0.06)",
              }}
            >
              {group.items.map((item, index) => (
                <button
                  key={item.id}
                  onClick={() => handleItemClick(item.id)}
                  className="w-full px-4 py-4 flex items-center justify-between transition-colors hover:bg-white/5"
                  style={{
                    borderBottom: index < group.items.length - 1 
                      ? "1px solid rgba(255, 255, 255, 0.06)" 
                      : "none",
                  }}
                >
                  <div className="flex items-center gap-3">
                    <item.icon className="w-5 h-5" style={{ color: "#A1A1A6" }} />
                    <span className="text-sm" style={{ color: "#F5F5F7" }}>
                      {item.label}
                    </span>
                  </div>
                  <div className="flex items-center gap-2">
                    {item.id === "language" && (
                      <span className="text-sm" style={{ color: "#A1A1A6" }}>
                        {languageLabels[currentLanguage] || "English"}
                      </span>
                    )}
                    <ChevronRight className="w-4 h-4" style={{ color: "#A1A1A6" }} />
                  </div>
                </button>
              ))}
            </div>
          </motion.div>
        ))}

        {/* Logout Button */}
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
          <span className="text-red-400 font-medium">Log Out</span>
        </motion.button>

        {/* Version */}
        <p className="text-center text-xs mt-6" style={{ color: "#A1A1A6" }}>
          Eternal Beam v1.0.0
        </p>
      </div>
    </div>
  );
}
