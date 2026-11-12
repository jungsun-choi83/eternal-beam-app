"use client";

import { useState, useMemo } from "react";
import { motion } from "framer-motion";
import { Wifi, Check } from "lucide-react";
import { PaperBrandHeader, PaperFlowIntro } from "@/components/memorial/paper-brand-header";
import { writeToNFCSlot } from "@/app/services/nfcManager";
import { mapSlotToContent } from "@/app/services/supabaseContentService";
import { memorialT } from "@/components/memorial/memorial-i18n";
import {
  getStoredContentId,
  persistDeviceContentFromPipeline,
} from "@/lib/persist-device-content";
import { readShippingAddress } from "@/lib/finalize-preview-content";

interface NFCPlaybackScreenProps {
  language?: string;
  /** 유료 배경 — NFC 카드 실물 발송 플로우 */
  premiumPhysical?: boolean;
  onComplete: () => void;
  onBack: () => void;
  onGoPreview?: () => void;
}

/**
 * Phase 10 — 이 화면은 기기(NFC 카드 · 재생 표면)를 다루는 미디어 무대라 **어둡게**
 * 유지한다. 셸이 더는 검정을 상속해 주지 않으므로 무대 바탕(inverse)을 스스로
 * 명시하고, 그 위의 크롬(칩·슬롯·CTA)만 토큰 시스템으로 맞춘다.
 */
const STAGE = "nfc-playback-screen eb-paper h-full flex flex-col bg-[#fffaf0] text-[#2f3a28]";

export function NFCPlaybackScreen({
  language = "ko",
  premiumPhysical = false,
  onComplete,
  onBack,
  onGoPreview,
}: NFCPlaybackScreenProps) {
  const n = memorialT(language).nfc;
  const shipping = readShippingAddress();
  const slots = useMemo(
    () => [1, 2, 3].map((id) => ({ id, name: n.slot(id), occupied: false })),
    [language]
  );

  const [selectedSlot, setSelectedSlot] = useState<number | null>(1);
  const [isSending, setIsSending] = useState(false);
  const [isComplete, setIsComplete] = useState(false);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);

  const handleSend = async () => {
    if (!selectedSlot) return;
    let contentId = getStoredContentId();
    if (!contentId) {
      contentId = persistDeviceContentFromPipeline(null);
    }
    if (!contentId) {
      setErrorMessage(n.needContentId);
      return;
    }

    try {
      setErrorMessage(null);
      setIsSending(true);
      const videoId =
        localStorage.getItem("eternal_beam_hologram_video_id") ||
        localStorage.getItem("eternal_beam_current_video_id") ||
        "video_unknown";

      const payloadForNfc = { content_id: contentId, slot_number: selectedSlot };
      const result = await writeToNFCSlot(contentId, videoId, selectedSlot, payloadForNfc);
      if (!result.success) {
        throw new Error(result.message || n.writeFailed);
      }

      await mapSlotToContent(selectedSlot, contentId);
      setIsComplete(true);
    } catch (e) {
      const msg = e instanceof Error ? e.message : n.writeFailed;
      setErrorMessage(msg);
    } finally {
      setIsSending(false);
    }
  };

  if (isComplete) {
    return (
      <div className={`${STAGE} items-center justify-center px-8`}>
        <motion.div
          initial={{ scale: 0, opacity: 0 }}
          animate={{ scale: 1, opacity: 1 }}
          transition={{ type: "spring", stiffness: 200, damping: 15 }}
          className="relative mb-10"
        >
          <motion.div
            className="absolute -inset-8 rounded-full"
            style={{
              background: "radial-gradient(circle, var(--eb-gold-wash-strong) 0%, transparent 70%)",
            }}
            animate={{ scale: [1, 1.2, 1], opacity: [0.5, 0.8, 0.5] }}
            transition={{ duration: 2, repeat: Infinity }}
          />

          <div
            className="relative w-28 h-28 rounded-full flex items-center justify-center"
            style={{
              background: "var(--eb-gold)",
              boxShadow: "var(--eb-shadow-gold)",
            }}
          >
            <Check className="w-14 h-14 text-[var(--eb-text-on-gold)]" strokeWidth={1.5} />
          </div>
        </motion.div>

        <motion.h2
          initial={{ opacity: 0, y: 20 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ delay: 0.3 }}
          className="text-2xl font-light tracking-wider mb-4"
          style={{ color: "#2f3a28" }}
        >
          {n.completeTitle}
        </motion.h2>

        <motion.p
          initial={{ opacity: 0, y: 20 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ delay: 0.4 }}
          className="text-sm font-light text-center mb-14 max-w-[260px]"
          style={{ color: "#7a6a58" }}
        >
          {premiumPhysical ? n.completeBodyShip : n.completeBody}
        </motion.p>

        <motion.button
          initial={{ opacity: 0, y: 20 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ delay: 0.5 }}
          onClick={onComplete}
          className="eb-btn eb-btn--primary eb-btn--block mem-btn-primary w-full max-w-[420px] rounded-2xl text-[15px] tracking-wider"
          whileTap={{ scale: 0.98 }}
        >
          {n.done}
        </motion.button>
      </div>
    );
  }

  if (isSending) {
    return (
      <div className={`${STAGE} items-center justify-center px-8`}>
        <motion.div className="relative mb-10">
          {[...Array(3)].map((_, i) => (
            <motion.div
              key={i}
              className="absolute inset-0 rounded-full"
              style={{ border: "1px solid var(--eb-gold-line)" }}
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
            className="glass-dark relative w-24 h-24 rounded-full flex items-center justify-center"
            style={{
              border: "1px solid var(--eb-gold-line)",
            }}
            animate={{ scale: [1, 1.05, 1] }}
            transition={{ duration: 1, repeat: Infinity }}
          >
            <Wifi className="w-10 h-10 text-[var(--eb-gold)]" strokeWidth={1.5} />
          </motion.div>
        </motion.div>

        <motion.h2
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          className="text-xl font-light tracking-wider mb-3"
          style={{ color: "#2f3a28" }}
        >
          {n.sending}
        </motion.h2>

        <motion.p
          animate={{ opacity: [0.4, 0.7, 0.4] }}
          transition={{ duration: 2, repeat: Infinity }}
          className="text-xs font-light"
          style={{ color: "#7a6a58" }}
        >
          {n.sendingHint}
        </motion.p>
      </div>
    );
  }

  return (
    <div className={STAGE}>
      <PaperBrandHeader onBack={onBack} backLabel="뒤로" />
      <PaperFlowIntro title={n.title} lead={n.readyLead} />

      <div className="flex-1 min-h-0 overflow-y-auto hide-scrollbar flex flex-col items-center px-8">
        <div className="mx-auto flex w-full max-w-[420px] flex-col items-center">
        <img
          src="/auth/nfc-device.jpg"
          alt=""
          className="eb-paper-art__img mb-6 w-full max-w-[280px]"
          draggable={false}
        />
        <p className="eb-paper-lead mb-6 text-center">
          {premiumPhysical ? n.premiumShipHint : n.insertHint}
        </p>
        {premiumPhysical && shipping ? (
          <motion.p
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            className="text-[11px] font-light text-center max-w-[280px] -mt-6 mb-8 leading-relaxed"
            style={{ color: "#7a6a58" }}
          >
            {n.shippingTo(shipping.recipientName, shipping.addressLine1)}
          </motion.p>
        ) : null}

        {/* 슬롯 선택 — 어두운 칩(.glass-dark). 선택은 골드 링 + 체크 하나만. */}
        <div className="w-full grid grid-cols-3 gap-3" role="radiogroup" aria-label={n.title}>
          {slots.map((slot, index) => (
            <motion.button
              key={slot.id}
              initial={{ opacity: 0, y: 10 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ delay: 0.3 + index * 0.1 }}
              onClick={() => !slot.occupied && setSelectedSlot(slot.id)}
              disabled={slot.occupied}
              role="radio"
              aria-checked={selectedSlot === slot.id}
              className={`glass-dark relative min-h-[var(--eb-touch)] py-6 rounded-2xl ${
                slot.occupied ? "opacity-40" : ""
              }`}
              style={{
                borderColor:
                  selectedSlot === slot.id ? "var(--eb-gold)" : "rgba(255, 255, 255, 0.12)",
                boxShadow: selectedSlot === slot.id ? "0 0 0 1px var(--eb-gold)" : "none",
                transition:
                  "border-color var(--eb-dur) var(--eb-ease), box-shadow var(--eb-dur) var(--eb-ease)",
              }}
              whileTap={!slot.occupied ? { scale: 0.98 } : {}}
            >
              <span
                className="text-sm font-light tracking-wider"
                style={{
                  color: selectedSlot === slot.id ? "#3a452f" : "#2f3a28",
                }}
              >
                {slot.name}
              </span>
              {slot.occupied && (
                <span
                  className="block text-[11px] mt-1"
                  style={{ color: "#7a6a58" }}
                >
                  {n.inUse}
                </span>
              )}
              {selectedSlot === slot.id && (
                <motion.div
                  initial={{ scale: 0 }}
                  animate={{ scale: 1 }}
                  className="eb-check-mark absolute top-2 right-2"
                  aria-hidden
                >
                  <Check className="w-3 h-3" strokeWidth={2.5} />
                </motion.div>
              )}
            </motion.button>
          ))}
        </div>
        </div>
      </div>

      <div className="px-6 pt-3 pb-[var(--eb-footer-bottom)] shrink-0">
        <div className="mx-auto w-full max-w-[420px]">
        <motion.button
          initial={{ opacity: 0, y: 20 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ delay: 0.5 }}
          onClick={handleSend}
          disabled={!selectedSlot}
          className="eb-btn eb-btn--primary eb-btn--forest eb-btn--block w-full"
          whileTap={selectedSlot ? { scale: 0.98 } : {}}
        >
          {n.send}
        </motion.button>
        {errorMessage && (
          <div className="mt-3 space-y-2">
            <p
              className="text-center text-xs"
              role="alert"
              style={{ color: "var(--eb-terracotta)" }}
            >
              {errorMessage}
            </p>
            {onGoPreview ? (
              <button
                type="button"
                onClick={onGoPreview}
                className="eb-btn eb-btn--outline eb-btn--block w-full"
              >
                {n.goPreview}
              </button>
            ) : null}
          </div>
        )}
        </div>
      </div>
    </div>
  );
}
