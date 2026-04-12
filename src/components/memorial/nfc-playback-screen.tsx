"use client";

import { useState } from "react";
import { motion } from "framer-motion";
import { ArrowLeft, Wifi, Check, Sparkles } from "lucide-react";

interface NFCPlaybackScreenProps {
  onComplete: () => void;
  onBack: () => void;
}

const slots = [
  { id: 1, name: "Slot 1", occupied: false },
  { id: 2, name: "Slot 2", occupied: false },
  { id: 3, name: "Slot 3", occupied: false },
  { id: 4, name: "Slot 4", occupied: false },
];

export function NFCPlaybackScreen({ onComplete, onBack }: NFCPlaybackScreenProps) {
  const [selectedSlot, setSelectedSlot] = useState<number | null>(null);
  const [isSending, setIsSending] = useState(false);
  const [isComplete, setIsComplete] = useState(false);

  const handleSend = () => {
    if (!selectedSlot) return;
    
    setIsSending(true);
    setTimeout(() => {
      setIsSending(false);
      setIsComplete(true);
    }, 3000);
  };

  if (isComplete) {
    return (
      <div className="h-full flex flex-col bg-[#0a0a0a] items-center justify-center px-8">
        {/* Success Animation */}
        <motion.div
          initial={{ scale: 0, opacity: 0 }}
          animate={{ scale: 1, opacity: 1 }}
          transition={{ type: "spring", stiffness: 200, damping: 15 }}
          className="relative mb-10"
        >
          {/* Glow Ring */}
          <motion.div
            className="absolute -inset-8 rounded-full"
            style={{
              background: "radial-gradient(circle, rgba(201, 162, 39, 0.15) 0%, transparent 70%)",
            }}
            animate={{ scale: [1, 1.2, 1], opacity: [0.5, 0.8, 0.5] }}
            transition={{ duration: 2, repeat: Infinity }}
          />
          
          <div 
            className="relative w-28 h-28 rounded-full flex items-center justify-center"
            style={{
              background: "linear-gradient(135deg, #b8860b 0%, #c9a227 30%, #d4af37 50%, #f5d77a 70%, #d4af37 100%)",
              boxShadow: "0 0 60px rgba(201, 162, 39, 0.35)",
            }}
          >
            <Check className="w-14 h-14 text-[#0a0a0a]" strokeWidth={1.5} />
          </div>
        </motion.div>

        <motion.h2
          initial={{ opacity: 0, y: 20 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ delay: 0.3 }}
          className="text-2xl font-light tracking-wider mb-4"
          style={{ color: "#F5F5F7" }}
        >
          Transfer Complete
        </motion.h2>

        <motion.p
          initial={{ opacity: 0, y: 20 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ delay: 0.4 }}
          className="text-sm font-light text-center mb-14 max-w-[260px]"
          style={{ color: "#A1A1A6" }}
        >
          Your holographic memory has been saved to the device
        </motion.p>

        <motion.button
          initial={{ opacity: 0, y: 20 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ delay: 0.5 }}
          onClick={onComplete}
          className="w-full py-4 rounded-2xl font-normal text-[15px] tracking-wider"
          style={{
            background: "linear-gradient(135deg, #b8860b 0%, #c9a227 30%, #d4af37 50%, #f5d77a 70%, #d4af37 100%)",
            boxShadow: "0 10px 40px rgba(201, 162, 39, 0.25)",
            color: "#0a0a0a",
          }}
          whileHover={{ scale: 1.02 }}
          whileTap={{ scale: 0.98 }}
        >
          Return Home
        </motion.button>
      </div>
    );
  }

  if (isSending) {
    return (
      <div className="h-full flex flex-col bg-[#0a0a0a] items-center justify-center px-8">
        {/* Sending Animation */}
        <motion.div className="relative mb-10">
          {/* Pulse Rings */}
          {[...Array(3)].map((_, i) => (
            <motion.div
              key={i}
              className="absolute inset-0 rounded-full"
              style={{ border: "1px solid rgba(201, 162, 39, 0.25)" }}
              initial={{ scale: 1, opacity: 0.6 }}
              animate={{ scale: 2 + i * 0.5, opacity: 0 }}
              transition={{
                duration: 2,
                repeat: Infinity,
                delay: i * 0.5,
                ease: "easeOut",
              }}
            />
          ))}
          
          <motion.div
            className="relative w-24 h-24 rounded-full flex items-center justify-center"
            style={{
              background: "#1C1C1E",
              border: "1px solid rgba(201, 162, 39, 0.25)",
            }}
            animate={{ scale: [1, 1.05, 1] }}
            transition={{ duration: 1, repeat: Infinity }}
          >
            <Wifi className="w-10 h-10 text-[#c9a227]" strokeWidth={1.5} />
          </motion.div>
        </motion.div>

        <motion.h2
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          className="text-xl font-light tracking-wider mb-3"
          style={{ color: "#F5F5F7" }}
        >
          Sending to Device
        </motion.h2>

        <motion.p
          animate={{ opacity: [0.4, 0.7, 0.4] }}
          transition={{ duration: 2, repeat: Infinity }}
          className="text-xs font-light"
          style={{ color: "#A1A1A6" }}
        >
          Please keep device nearby...
        </motion.p>
      </div>
    );
  }

  return (
    <div className="h-full flex flex-col bg-[#0a0a0a]">
      {/* Header */}
      <header className="px-6 pt-14 pb-4 flex items-center justify-between relative">
        <motion.button
          initial={{ opacity: 0, x: -10 }}
          animate={{ opacity: 1, x: 0 }}
          onClick={onBack}
          className="w-10 h-10 rounded-full flex items-center justify-center"
          style={{
            background: "#1C1C1E",
            border: "1px solid #333333",
          }}
          whileHover={{ scale: 1.05, borderColor: "#444444" }}
          whileTap={{ scale: 0.95 }}
        >
          <ArrowLeft className="w-4 h-4" style={{ color: "#F5F5F7" }} strokeWidth={1.5} />
        </motion.button>

        <motion.h1
          initial={{ opacity: 0, y: -10 }}
          animate={{ opacity: 1, y: 0 }}
          className="text-xl font-light absolute left-1/2 -translate-x-1/2"
          style={{ color: "#F5F5F7" }}
        >
          Save
        </motion.h1>

        <div className="w-10" />
      </header>

      {/* NFC Animation Area */}
      <div className="flex-1 flex flex-col items-center justify-center px-8">

        {/* 상단 삽입 안내 일러스트 */}
        <motion.div
          initial={{ opacity: 0, y: -10 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ delay: 0.1 }}
          className="relative mb-8 flex flex-col items-center"
        >
          {/* 카드 아이콘 — 아래로 내려오는 애니메이션 */}
          <motion.div
            animate={{ y: [0, 10, 0] }}
            transition={{ duration: 1.6, repeat: Infinity, ease: "easeInOut" }}
            className="mb-1"
          >
            <div
              className="w-12 h-7 rounded-md flex items-center justify-center"
              style={{
                background: "linear-gradient(135deg, #b8860b 0%, #d4af37 50%, #f5d77a 100%)",
                boxShadow: "0 4px 16px rgba(201,162,39,0.25)",
              }}
            >
              <Sparkles className="w-4 h-4 text-[#0a0a0a]" strokeWidth={1.5} />
            </div>
          </motion.div>

          {/* 아래 화살표 */}
          <motion.div
            animate={{ opacity: [0.3, 1, 0.3] }}
            transition={{ duration: 1.6, repeat: Infinity, ease: "easeInOut" }}
          >
            <svg width="16" height="12" viewBox="0 0 16 12" fill="none">
              <path d="M8 0 L8 8 M4 5 L8 10 L12 5" stroke="#c9a227" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"/>
            </svg>
          </motion.div>

          {/* 기기 상단 슬롯 표시 */}
          <div
            className="mt-1 w-16 h-2 rounded-full"
            style={{
              background: "#1C1C1E",
              border: "1px solid rgba(201,162,39,0.3)",
              boxShadow: "0 0 8px rgba(201,162,39,0.15)",
            }}
          />
          <div
            className="w-20 h-5 rounded-b-lg"
            style={{
              background: "#141414",
              border: "1px solid #2a2a2a",
              borderTop: "none",
            }}
          />
        </motion.div>

        <motion.p
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          transition={{ delay: 0.2 }}
          className="text-sm font-light text-center max-w-[240px] mb-1"
          style={{ color: "#F5F5F7" }}
        >
          Insert card into top slot
        </motion.p>
        <motion.p
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          transition={{ delay: 0.25 }}
          className="text-xs font-light text-center max-w-[240px] mb-8"
          style={{ color: "#666666" }}
        >
          기기 상단 슬롯에 카드를 삽입하세요
        </motion.p>

        {/* Slot Selection */}
        <div className="w-full grid grid-cols-2 gap-3">
          {slots.map((slot, index) => (
            <motion.button
              key={slot.id}
              initial={{ opacity: 0, y: 10 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ delay: 0.3 + index * 0.1 }}
              onClick={() => !slot.occupied && setSelectedSlot(slot.id)}
              disabled={slot.occupied}
              className={`relative py-6 rounded-2xl transition-all duration-300 ${
                selectedSlot === slot.id
                  ? "ring-2 ring-[#c9a227] ring-offset-2 ring-offset-[#0a0a0a]"
                  : ""
              } ${slot.occupied ? "opacity-40" : ""}`}
              style={{
                background: selectedSlot === slot.id ? "rgba(201, 162, 39, 0.08)" : "#1C1C1E",
                border: selectedSlot === slot.id
                  ? "1px solid rgba(201, 162, 39, 0.25)"
                  : "1px solid #333333",
              }}
              whileHover={!slot.occupied ? { scale: 1.02, borderColor: "#444444" } : {}}
              whileTap={!slot.occupied ? { scale: 0.98 } : {}}
            >
              <span 
                className="text-sm font-light tracking-wider"
                style={{ color: selectedSlot === slot.id ? "#c9a227" : "#F5F5F7" }}
              >
                {slot.name}
              </span>
              {slot.occupied && (
                <span 
                  className="block text-[11px] mt-1"
                  style={{ color: "#666666" }}
                >
                  In use
                </span>
              )}
              {selectedSlot === slot.id && (
                <motion.div
                  initial={{ scale: 0 }}
                  animate={{ scale: 1 }}
                  className="absolute top-2 right-2 w-5 h-5 rounded-full flex items-center justify-center"
                  style={{
                    background: "linear-gradient(135deg, #c9a227, #f5d77a)",
                  }}
                >
                  <Check className="w-3 h-3 text-[#0a0a0a]" strokeWidth={2} />
                </motion.div>
              )}
            </motion.button>
          ))}
        </div>
      </div>

      {/* Send Button */}
      <div className="px-8 pb-10">
        <motion.button
          initial={{ opacity: 0, y: 20 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ delay: 0.5 }}
          onClick={handleSend}
          disabled={!selectedSlot}
          className="w-full py-4 rounded-2xl font-normal text-[15px] tracking-wider transition-all duration-300"
          style={{
            background: selectedSlot
              ? "linear-gradient(135deg, #b8860b 0%, #c9a227 30%, #d4af37 50%, #f5d77a 70%, #d4af37 100%)"
              : "#1C1C1E",
            border: selectedSlot ? "none" : "1px solid #333333",
            color: selectedSlot ? "#0a0a0a" : "#A1A1A6",
            boxShadow: selectedSlot
              ? "0 10px 40px rgba(201, 162, 39, 0.25)"
              : "none",
            cursor: selectedSlot ? "pointer" : "not-allowed",
          }}
          whileHover={selectedSlot ? { scale: 1.02 } : {}}
          whileTap={selectedSlot ? { scale: 0.98 } : {}}
        >
          Send to Device
        </motion.button>
      </div>
    </div>
  );
}
