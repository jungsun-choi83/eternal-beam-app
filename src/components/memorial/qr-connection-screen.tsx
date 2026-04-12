"use client";

import { useState, useEffect } from "react";
import { motion } from "framer-motion";
import { ChevronLeft, Smartphone, CheckCircle2 } from "lucide-react";

interface QRConnectionScreenProps {
  onComplete: () => void;
  onBack: () => void;
  onSkip: () => void;
}

export function QRConnectionScreen({ onComplete, onBack, onSkip }: QRConnectionScreenProps) {
  const [isScanning, setIsScanning] = useState(false);
  const [isConnected, setIsConnected] = useState(false);

  useEffect(() => {
    if (isScanning) {
      const timer = setTimeout(() => {
        setIsScanning(false);
        setIsConnected(true);
      }, 3000);
      return () => clearTimeout(timer);
    }
  }, [isScanning]);

  useEffect(() => {
    if (isConnected) {
      const timer = setTimeout(() => {
        onComplete();
      }, 1500);
      return () => clearTimeout(timer);
    }
  }, [isConnected, onComplete]);

  return (
    <div className="h-full flex flex-col bg-[#0a0a0a] relative overflow-hidden min-h-0">
      {/* Header */}
      <header className="px-6 pt-8 pb-4 flex items-center justify-between relative shrink-0">
        <button onClick={onBack} className="p-2 -ml-2">
          <ChevronLeft className="w-5 h-5" style={{ color: "#F5F5F7" }} />
        </button>
        <h1 className="text-xl font-light absolute left-1/2 -translate-x-1/2 text-center" style={{ color: "#F5F5F7" }}>
          Connect Device
        </h1>
        <button onClick={onSkip} className="text-sm" style={{ color: "#A1A1A6" }}>
          Skip
        </button>
      </header>

      {/* Content */}
      <div className="flex-1 flex flex-col items-center justify-center px-8 min-h-0 overflow-y-auto">
        {!isConnected ? (
          <>
            {/* QR Code Area */}
            <motion.div
              className="w-56 h-56 rounded-3xl mb-8 relative overflow-hidden"
              style={{
                background: "rgba(255, 255, 255, 0.03)",
                backdropFilter: "blur(40px)",
                border: "1px solid rgba(255, 255, 255, 0.08)",
              }}
              animate={isScanning ? { borderColor: "rgba(212, 175, 55, 0.5)" } : {}}
            >
              {/* Simulated QR Pattern */}
              <div className="absolute inset-6 grid grid-cols-7 gap-1">
                {Array.from({ length: 49 }).map((_, i) => (
                  <motion.div
                    key={i}
                    className="rounded-sm"
                    style={{
                      background: Math.random() > 0.5 ? "#F5F5F7" : "transparent",
                    }}
                    animate={isScanning ? { opacity: [1, 0.5, 1] } : {}}
                    transition={{
                      duration: 0.8,
                      repeat: isScanning ? Infinity : 0,
                      delay: i * 0.02,
                    }}
                  />
                ))}
              </div>

              {/* Scanning Line */}
              {isScanning && (
                <motion.div
                  className="absolute left-0 right-0 h-0.5"
                  style={{ background: "linear-gradient(90deg, transparent, #d4af37, transparent)" }}
                  animate={{ top: ["10%", "90%", "10%"] }}
                  transition={{ duration: 2, repeat: Infinity, ease: "easeInOut" }}
                />
              )}

              {/* Corner Markers */}
              <div className="absolute top-3 left-3 w-8 h-8 border-t-2 border-l-2 rounded-tl" style={{ borderColor: "#d4af37" }} />
              <div className="absolute top-3 right-3 w-8 h-8 border-t-2 border-r-2 rounded-tr" style={{ borderColor: "#d4af37" }} />
              <div className="absolute bottom-3 left-3 w-8 h-8 border-b-2 border-l-2 rounded-bl" style={{ borderColor: "#d4af37" }} />
              <div className="absolute bottom-3 right-3 w-8 h-8 border-b-2 border-r-2 rounded-br" style={{ borderColor: "#d4af37" }} />
            </motion.div>

            <h2 className="text-xl font-light mb-3" style={{ color: "#F5F5F7" }}>
              Scan QR Code
            </h2>
            <p className="text-sm text-center mb-6 max-w-xs" style={{ color: "#A1A1A6" }}>
              Scan the QR code on your Eternal Beam device to connect
            </p>

            {/* Scan Button */}
            <motion.button
              onClick={() => setIsScanning(true)}
              disabled={isScanning}
              className="w-full py-4 rounded-2xl flex items-center justify-center gap-3"
              style={{
                background: isScanning 
                  ? "rgba(212, 175, 55, 0.2)" 
                  : "linear-gradient(135deg, #d4af37 0%, #c9a227 100%)",
                boxShadow: isScanning ? "none" : "0 8px 32px rgba(212, 175, 55, 0.3)",
              }}
              whileHover={!isScanning ? { scale: 1.02 } : {}}
              whileTap={!isScanning ? { scale: 0.98 } : {}}
            >
              <Smartphone className="w-5 h-5" style={{ color: isScanning ? "#d4af37" : "#0a0a0a" }} />
              <span style={{ color: isScanning ? "#d4af37" : "#0a0a0a" }} className="font-medium">
                {isScanning ? "Scanning..." : "Start Scanning"}
              </span>
            </motion.button>
          </>
        ) : (
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
              Device Connected
            </h2>
            <p className="text-sm" style={{ color: "#A1A1A6" }}>
              Eternal Beam Pro is ready
            </p>
          </motion.div>
        )}
      </div>

      {/* 하단 Next 버튼 - 항상 보이도록 고정 */}
      <div className="shrink-0 px-6 pb-8 pt-4">
        <motion.button
          onClick={onComplete}
          className="w-full py-4 rounded-2xl flex items-center justify-center font-medium"
          style={{
            background: "linear-gradient(135deg, #d4af37 0%, #c9a227 100%)",
            boxShadow: "0 8px 32px rgba(212, 175, 55, 0.3)",
            color: "#0a0a0a",
          }}
          whileHover={{ scale: 1.02 }}
          whileTap={{ scale: 0.98 }}
        >
          Next
        </motion.button>
      </div>
    </div>
  );
}
