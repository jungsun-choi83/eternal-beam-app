"use client";

import { useState } from "react";
import { motion } from "framer-motion";
import { ChevronLeft, Wifi, Battery, HardDrive, RefreshCw, Trash2, CheckCircle2 } from "lucide-react";

interface DeviceScreenProps {
  onBack: () => void;
  onReconnect: () => void;
}

export function DeviceScreen({ onBack, onReconnect }: DeviceScreenProps) {
  const [isConnected, setIsConnected] = useState(true);

  const deviceInfo = {
    name: "Eternal Beam Pro",
    model: "EB-2024",
    firmware: "v2.1.3",
    storage: { used: 2.4, total: 8 },
    battery: 78,
    wifi: "Home_WiFi_5G",
  };

  return (
    <div className="h-full flex flex-col bg-[#0a0a0a] relative overflow-hidden">
      {/* Header */}
      <header className="px-6 pt-14 pb-4 flex items-center relative">
        <button onClick={onBack} className="p-2 -ml-2">
          <ChevronLeft className="w-5 h-5" style={{ color: "#F5F5F7" }} />
        </button>
        <h1 className="text-xl font-light absolute left-1/2 -translate-x-1/2" style={{ color: "#F5F5F7" }}>
          Device
        </h1>
      </header>

      <div className="flex-1 overflow-y-auto px-4 pb-8">
        {/* Device Card */}
        <motion.div
          initial={{ opacity: 0, y: 20 }}
          animate={{ opacity: 1, y: 0 }}
          className="p-6 rounded-3xl mb-6"
          style={{
            background: "rgba(255, 255, 255, 0.03)",
            border: "1px solid rgba(255, 255, 255, 0.06)",
          }}
        >
          {/* Device Visual */}
          <div className="flex justify-center mb-6">
            <div 
              className="w-32 h-32 rounded-3xl flex items-center justify-center relative"
              style={{
                background: "linear-gradient(135deg, rgba(212, 175, 55, 0.1) 0%, rgba(201, 162, 39, 0.05) 100%)",
                border: "1px solid rgba(212, 175, 55, 0.2)",
              }}
            >
              <motion.div
                className="absolute inset-4 rounded-2xl"
                style={{ background: "linear-gradient(135deg, #d4af37 0%, #c9a227 100%)", opacity: 0.1 }}
                animate={{ opacity: [0.1, 0.2, 0.1] }}
                transition={{ duration: 3, repeat: Infinity }}
              />
              {isConnected && (
                <motion.div
                  className="absolute -top-1 -right-1"
                  initial={{ scale: 0 }}
                  animate={{ scale: 1 }}
                >
                  <CheckCircle2 className="w-6 h-6" style={{ color: "#34C759" }} />
                </motion.div>
              )}
            </div>
          </div>

          {/* Device Name */}
          <h2 className="text-xl font-light text-center mb-1" style={{ color: "#F5F5F7" }}>
            {deviceInfo.name}
          </h2>
          <p className="text-sm text-center mb-4" style={{ color: "#A1A1A6" }}>
            {deviceInfo.model} | Firmware {deviceInfo.firmware}
          </p>

          {/* Connection Status */}
          <div 
            className="flex items-center justify-center gap-2 py-2 px-4 rounded-full mx-auto w-fit"
            style={{
              background: isConnected ? "rgba(52, 199, 89, 0.1)" : "rgba(255, 59, 48, 0.1)",
            }}
          >
            <div 
              className="w-2 h-2 rounded-full"
              style={{ background: isConnected ? "#34C759" : "#FF3B30" }}
            />
            <span 
              className="text-sm"
              style={{ color: isConnected ? "#34C759" : "#FF3B30" }}
            >
              {isConnected ? "Connected" : "Disconnected"}
            </span>
          </div>
        </motion.div>

        {/* Device Stats */}
        <motion.div
          initial={{ opacity: 0, y: 20 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ delay: 0.1 }}
          className="rounded-2xl overflow-hidden mb-6"
          style={{
            background: "rgba(255, 255, 255, 0.03)",
            border: "1px solid rgba(255, 255, 255, 0.06)",
          }}
        >
          {/* Storage */}
          <div className="p-4 border-b" style={{ borderColor: "rgba(255, 255, 255, 0.06)" }}>
            <div className="flex items-center justify-between mb-2">
              <div className="flex items-center gap-3">
                <HardDrive className="w-5 h-5" style={{ color: "#A1A1A6" }} />
                <span className="text-sm" style={{ color: "#F5F5F7" }}>Storage</span>
              </div>
              <span className="text-sm" style={{ color: "#A1A1A6" }}>
                {deviceInfo.storage.used} / {deviceInfo.storage.total} GB
              </span>
            </div>
            <div className="h-1.5 rounded-full overflow-hidden" style={{ background: "rgba(255, 255, 255, 0.1)" }}>
              <motion.div
                className="h-full rounded-full"
                style={{ 
                  background: "linear-gradient(90deg, #d4af37, #c9a227)",
                  width: `${(deviceInfo.storage.used / deviceInfo.storage.total) * 100}%`,
                }}
                initial={{ width: 0 }}
                animate={{ width: `${(deviceInfo.storage.used / deviceInfo.storage.total) * 100}%` }}
                transition={{ duration: 1, delay: 0.3 }}
              />
            </div>
          </div>

          {/* Battery */}
          <div className="p-4 border-b" style={{ borderColor: "rgba(255, 255, 255, 0.06)" }}>
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-3">
                <Battery className="w-5 h-5" style={{ color: "#A1A1A6" }} />
                <span className="text-sm" style={{ color: "#F5F5F7" }}>Battery</span>
              </div>
              <span className="text-sm" style={{ color: "#34C759" }}>
                {deviceInfo.battery}%
              </span>
            </div>
          </div>

          {/* Wi-Fi */}
          <div className="p-4">
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-3">
                <Wifi className="w-5 h-5" style={{ color: "#A1A1A6" }} />
                <span className="text-sm" style={{ color: "#F5F5F7" }}>Wi-Fi</span>
              </div>
              <span className="text-sm" style={{ color: "#A1A1A6" }}>
                {deviceInfo.wifi}
              </span>
            </div>
          </div>
        </motion.div>

        {/* Actions */}
        <motion.div
          initial={{ opacity: 0, y: 20 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ delay: 0.2 }}
          className="space-y-3"
        >
          <button
            onClick={onReconnect}
            className="w-full py-4 rounded-2xl flex items-center justify-center gap-2"
            style={{
              background: "rgba(255, 255, 255, 0.03)",
              border: "1px solid rgba(255, 255, 255, 0.06)",
            }}
          >
            <RefreshCw className="w-5 h-5" style={{ color: "#d4af37" }} />
            <span style={{ color: "#F5F5F7" }}>Reconnect Device</span>
          </button>

          <button
            className="w-full py-4 rounded-2xl flex items-center justify-center gap-2"
            style={{
              background: "rgba(255, 59, 48, 0.1)",
              border: "1px solid rgba(255, 59, 48, 0.2)",
            }}
          >
            <Trash2 className="w-5 h-5 text-red-400" />
            <span className="text-red-400">Remove Device</span>
          </button>
        </motion.div>
      </div>
    </div>
  );
}
