"use client";

import { useState, useEffect } from "react";
import { motion } from "framer-motion";
import { ChevronLeft, Wifi, CheckCircle2, Loader2 } from "lucide-react";

interface DeviceConnectionScreenProps {
  onComplete: () => void;
  onBack: () => void;
  onSkip: () => void;
}

const wifiNetworks = [
  { id: 1, name: "Home_WiFi_5G", strength: 3, secured: true },
  { id: 2, name: "EternalBeam_Setup", strength: 3, secured: false },
  { id: 3, name: "Office_Network", strength: 2, secured: true },
  { id: 4, name: "Guest_WiFi", strength: 1, secured: true },
];

export function DeviceConnectionScreen({ onComplete, onBack, onSkip }: DeviceConnectionScreenProps) {
  const [selectedNetwork, setSelectedNetwork] = useState<number | null>(null);
  const [password, setPassword] = useState("");
  const [isConnecting, setIsConnecting] = useState(false);
  const [isConnected, setIsConnected] = useState(false);

  const handleConnect = () => {
    setIsConnecting(true);
  };

  useEffect(() => {
    if (isConnecting) {
      const timer = setTimeout(() => {
        setIsConnecting(false);
        setIsConnected(true);
      }, 2500);
      return () => clearTimeout(timer);
    }
  }, [isConnecting]);

  useEffect(() => {
    if (isConnected) {
      const timer = setTimeout(() => {
        onComplete();
      }, 1500);
      return () => clearTimeout(timer);
    }
  }, [isConnected, onComplete]);

  const selectedNetworkData = wifiNetworks.find(n => n.id === selectedNetwork);

  return (
    <div className="h-full flex flex-col bg-[#0a0a0a] relative overflow-hidden">
      {/* Header */}
      <header className="px-6 pt-6 pb-4 flex items-center justify-between">
        <button onClick={onBack} className="p-2 -ml-2">
          <ChevronLeft className="w-6 h-6" style={{ color: "#F5F5F7" }} />
        </button>
        <h1 className="text-lg font-light" style={{ color: "#F5F5F7" }}>
          Wi-Fi Setup
        </h1>
        <button onClick={onSkip} className="text-sm" style={{ color: "#A1A1A6" }}>
          Skip
        </button>
      </header>

      {!isConnected ? (
        <div className="flex-1 flex flex-col px-6 pb-8">
          {/* Network List */}
          <p className="text-xs mb-4 tracking-wide" style={{ color: "#A1A1A6" }}>
            SELECT NETWORK
          </p>
          
          <div className="space-y-2 mb-6">
            {wifiNetworks.map((network) => (
              <motion.button
                key={network.id}
                onClick={() => setSelectedNetwork(network.id)}
                className="w-full p-4 rounded-2xl flex items-center justify-between transition-all duration-300"
                style={{
                  background: selectedNetwork === network.id 
                    ? "rgba(212, 175, 55, 0.1)" 
                    : "rgba(255, 255, 255, 0.03)",
                  border: selectedNetwork === network.id 
                    ? "1px solid rgba(212, 175, 55, 0.3)" 
                    : "1px solid rgba(255, 255, 255, 0.06)",
                }}
                whileTap={{ scale: 0.98 }}
              >
                <div className="flex items-center gap-3">
                  <Wifi 
                    className="w-5 h-5" 
                    style={{ color: selectedNetwork === network.id ? "#d4af37" : "#A1A1A6" }} 
                  />
                  <span style={{ color: "#F5F5F7" }}>{network.name}</span>
                </div>
                <div className="flex items-center gap-2">
                  {network.secured && (
                    <span className="text-xs" style={{ color: "#A1A1A6" }}>Secured</span>
                  )}
                  {/* Signal Strength */}
                  <div className="flex gap-0.5">
                    {[1, 2, 3].map((level) => (
                      <div
                        key={level}
                        className="w-1 rounded-full"
                        style={{
                          height: `${level * 4 + 4}px`,
                          background: level <= network.strength ? "#d4af37" : "rgba(161, 161, 166, 0.3)",
                        }}
                      />
                    ))}
                  </div>
                </div>
              </motion.button>
            ))}
          </div>

          {/* Password Input */}
          {selectedNetworkData?.secured && selectedNetwork && (
            <motion.div
              initial={{ opacity: 0, height: 0 }}
              animate={{ opacity: 1, height: "auto" }}
              className="mb-6"
            >
              <p className="text-xs mb-3 tracking-wide" style={{ color: "#A1A1A6" }}>
                PASSWORD
              </p>
              <input
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                placeholder="Enter password"
                className="w-full p-4 rounded-2xl text-sm outline-none transition-all duration-300"
                style={{
                  background: "rgba(255, 255, 255, 0.03)",
                  border: "1px solid rgba(255, 255, 255, 0.08)",
                  color: "#F5F5F7",
                }}
              />
            </motion.div>
          )}

          {/* Spacer */}
          <div className="flex-1" />

          {/* Connect Button */}
          <motion.button
            onClick={handleConnect}
            disabled={!selectedNetwork || (selectedNetworkData?.secured && !password) || isConnecting}
            className="w-full py-4 rounded-2xl flex items-center justify-center gap-2 transition-all duration-300 disabled:opacity-50"
            style={{
              background: selectedNetwork 
                ? "linear-gradient(135deg, #d4af37 0%, #c9a227 100%)" 
                : "rgba(255, 255, 255, 0.1)",
              boxShadow: selectedNetwork ? "0 8px 32px rgba(212, 175, 55, 0.3)" : "none",
            }}
            whileHover={selectedNetwork ? { scale: 1.02 } : {}}
            whileTap={selectedNetwork ? { scale: 0.98 } : {}}
          >
            {isConnecting ? (
              <>
                <Loader2 className="w-5 h-5 animate-spin text-[#0a0a0a]" />
                <span className="text-[#0a0a0a] font-medium">Connecting...</span>
              </>
            ) : (
              <span style={{ color: selectedNetwork ? "#0a0a0a" : "#A1A1A6" }} className="font-medium">
                Connect
              </span>
            )}
          </motion.button>
        </div>
      ) : (
        <div className="flex-1 flex flex-col items-center justify-center px-8">
          <motion.div
            initial={{ opacity: 0, scale: 0.9 }}
            animate={{ opacity: 1, scale: 1 }}
            className="text-center"
          >
            <motion.div
              initial={{ scale: 0 }}
              animate={{ scale: 1 }}
              transition={{ type: "spring", damping: 15 }}
              className="w-24 h-24 mx-auto mb-6 rounded-full flex items-center justify-center"
              style={{ background: "rgba(212, 175, 55, 0.1)" }}
            >
              <CheckCircle2 className="w-12 h-12" style={{ color: "#d4af37" }} />
            </motion.div>
            <h2 className="text-xl font-light mb-2" style={{ color: "#F5F5F7" }}>
              Connected
            </h2>
            <p className="text-sm" style={{ color: "#A1A1A6" }}>
              {selectedNetworkData?.name}
            </p>
          </motion.div>
        </div>
      )}
    </div>
  );
}
