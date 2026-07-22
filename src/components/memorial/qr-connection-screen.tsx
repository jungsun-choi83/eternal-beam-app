"use client";

import { useState, useEffect } from "react";
import { motion } from "framer-motion";
import { ChevronLeft, Smartphone, CheckCircle2 } from "lucide-react";
import { memorialT } from "@/components/memorial/memorial-i18n";

interface QRConnectionScreenProps {
  language?: string;
  showBack?: boolean;
  onComplete: () => void;
  onBack: () => void;
  onSkip: () => void;
}

export function QRConnectionScreen({
  language = "ko",
  showBack = true,
  onComplete,
  onBack,
  onSkip,
}: QRConnectionScreenProps) {
  const q = memorialT(language).qr;
  const c = memorialT(language).common;
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
    <div
      data-screen="qrConnection"
      className="h-full flex flex-col relative overflow-hidden min-h-0"
    >
      <header className="px-6 pt-8 pb-4 flex items-center justify-between relative shrink-0">
        {showBack ? (
          <button type="button" onClick={onBack} className="p-2 -ml-2" aria-label={c.back}>
            <ChevronLeft className="w-5 h-5" style={{ color: "#F5F5F7" }} />
          </button>
        ) : (
          <div className="w-9" aria-hidden />
        )}
        <h1 className="screen-title absolute left-1/2 -translate-x-1/2" style={{ color: "#F5F5F7" }}>
          {q.title}
        </h1>
        <button type="button" onClick={onSkip} className="text-sm memorial-caption" style={{ color: "#A1A1A6" }}>
          {q.skip}
        </button>
      </header>

      <div className="flex-1 flex flex-col items-center justify-center px-8 min-h-0 overflow-y-auto">
        {!isConnected ? (
          <>
            <motion.div
              className="w-56 h-56 rounded-3xl mb-8 relative overflow-hidden"
              style={{
                background: "rgba(255, 255, 255, 0.03)",
                backdropFilter: "blur(40px)",
                border: "1px solid rgba(255, 255, 255, 0.08)",
              }}
              animate={isScanning ? { borderColor: "rgba(212, 175, 55, 0.5)" } : {}}
            >
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

              {isScanning && (
                <motion.div
                  className="absolute left-0 right-0 h-0.5"
                  style={{ background: "linear-gradient(90deg, transparent, #d4af37, transparent)" }}
                  animate={{ top: ["10%", "90%", "10%"] }}
                  transition={{ duration: 2, repeat: Infinity, ease: "easeInOut" }}
                />
              )}

              <div className="absolute top-3 left-3 w-8 h-8 border-t-2 border-l-2 rounded-tl" style={{ borderColor: "#d4af37" }} />
              <div className="absolute top-3 right-3 w-8 h-8 border-t-2 border-r-2 rounded-tr" style={{ borderColor: "#d4af37" }} />
              <div className="absolute bottom-3 left-3 w-8 h-8 border-b-2 border-l-2 rounded-bl" style={{ borderColor: "#d4af37" }} />
              <div className="absolute bottom-3 right-3 w-8 h-8 border-b-2 border-r-2 rounded-br" style={{ borderColor: "#d4af37" }} />
            </motion.div>

            <h2 className="upload-title text-center mb-3">{q.scanTitle}</h2>
            <p className="memorial-body text-center mb-6 max-w-[17rem]">{q.scanHint}</p>

            <motion.button
              type="button"
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
              <span className="memorial-btn-label" style={{ color: isScanning ? "#d4af37" : "#0a0a0a" }}>
                {isScanning ? q.scanning : q.startScan}
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
            <h2 className="upload-title mb-2">{q.connected}</h2>
            <p className="memorial-body">{q.connectedHint}</p>
          </motion.div>
        )}
      </div>

      <div className="shrink-0 px-6 pb-8 pt-4">
        <motion.button
          type="button"
          onClick={onComplete}
          className="w-full py-4 rounded-2xl flex items-center justify-center memorial-btn-label"
          style={{
            background: "linear-gradient(135deg, #d4af37 0%, #c9a227 100%)",
            boxShadow: "0 8px 32px rgba(212, 175, 55, 0.3)",
            color: "#0a0a0a",
          }}
          whileHover={{ scale: 1.02 }}
          whileTap={{ scale: 0.98 }}
        >
          {q.next}
        </motion.button>
      </div>
    </div>
  );
}
