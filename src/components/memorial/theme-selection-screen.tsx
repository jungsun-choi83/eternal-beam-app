"use client";

import { useState, useRef, useEffect } from "react";
import { motion } from "framer-motion";
import { ArrowLeft, ArrowRight, Lock, Sparkles, Mic, Square, Play, Pause, Trash2, ChevronDown, ChevronUp } from "lucide-react";
import { mixAudioFiles } from "@/app/services/audioMixer";
import { memorialT, themeDisplayName } from "@/components/memorial/memorial-i18n";
import { memorialThemes, type MemorialTheme } from "@/components/memorial/themes";
import { ThemeBackgroundVideo } from "@/components/memorial/theme-background-video";

interface ThemeSelectionScreenProps {
  cutoutImage: string | null;
  selectedTheme: number | null;
  language?: string;
  walletCredits?: number | null;
  creditCost?: number;
  creditBusy?: boolean;
  creditPackBusy?: boolean;
  onBuyCreditsMock?: () => void;
  onSelectTheme: (themeId: number) => void;
  onSelectPremiumTheme: (themeId: number) => void;
  onContinue: () => void;
  onSkip: () => void;
  onBack: () => void;
}

const themes = memorialThemes;

export function ThemeSelectionScreen({ 
  cutoutImage,
  selectedTheme,
  language = "ko",
  walletCredits = null,
  creditCost = 4,
  creditBusy = false,
  creditPackBusy = false,
  onBuyCreditsMock,
  onSelectTheme, 
  onSelectPremiumTheme,
  onContinue, 
  onSkip,
  onBack 
}: ThemeSelectionScreenProps) {
  const tc = memorialT(language).theme;
  const currentTheme = themes.find(t => t.id === selectedTheme);
  const themeLabel = (th: MemorialTheme) => themeDisplayName(language === "ko" ? "ko" : "en", th);

  /* 음성 녹음 — 하드웨어(블루투스) 재생용 mixed_audio 저장 */
  const [showVoiceSection, setShowVoiceSection] = useState(false);
  const [voiceBlob, setVoiceBlob] = useState<Blob | null>(null);
  const [isRecording, setIsRecording] = useState(false);
  const [recordingTime, setRecordingTime] = useState(0);
  const [isPlaying, setIsPlaying] = useState(false);
  const [audioPreviewUrl, setAudioPreviewUrl] = useState<string | null>(null);
  const mediaRecorderRef = useRef<MediaRecorder | null>(null);
  const chunksRef = useRef<Blob[]>([]);
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const carouselRef = useRef<HTMLDivElement | null>(null);
  const draggingRef = useRef(false);
  const dragStartXRef = useRef(0);
  const dragStartScrollLeftRef = useRef(0);

  useEffect(() => {
    return () => {
      if (timerRef.current) clearInterval(timerRef.current);
      if (audioPreviewUrl) URL.revokeObjectURL(audioPreviewUrl);
    };
  }, [audioPreviewUrl]);

  useEffect(() => {
    const el = carouselRef.current;
    if (!el) return;
    const onWheel = (e: WheelEvent) => {
      if (Math.abs(e.deltaY) <= Math.abs(e.deltaX)) return;
      el.scrollLeft += e.deltaY;
      e.preventDefault();
    };
    el.addEventListener("wheel", onWheel, { passive: false });
    return () => el.removeEventListener("wheel", onWheel);
  }, []);

  const startRecording = async () => {
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const mr = new MediaRecorder(stream);
      mediaRecorderRef.current = mr;
      chunksRef.current = [];
      mr.ondataavailable = (e) => { if (e.data.size > 0) chunksRef.current.push(e.data); };
      mr.onstop = () => {
        const blob = new Blob(chunksRef.current, { type: "audio/webm" });
        setVoiceBlob(blob);
        setAudioPreviewUrl(URL.createObjectURL(blob));
        stream.getTracks().forEach((t) => t.stop());
      };
      mr.start();
      setIsRecording(true);
      setRecordingTime(0);
      timerRef.current = setInterval(() => setRecordingTime((s) => Math.min(s + 1, 60)), 1000);
    } catch {
      alert(tc.micPermission);
    }
  };

  const stopRecording = () => {
    if (mediaRecorderRef.current && isRecording) {
      mediaRecorderRef.current.stop();
      setIsRecording(false);
      if (timerRef.current) clearInterval(timerRef.current);
    }
  };

  const playPreview = () => {
    if (!audioPreviewUrl) return;
    if (!audioRef.current) {
      audioRef.current = new Audio(audioPreviewUrl);
      audioRef.current.onended = () => setIsPlaying(false);
    }
    if (isPlaying) {
      audioRef.current.pause();
      setIsPlaying(false);
    } else {
      audioRef.current.play();
      setIsPlaying(true);
    }
  };

  const deleteRecording = () => {
    if (audioPreviewUrl) URL.revokeObjectURL(audioPreviewUrl);
    setAudioPreviewUrl(null);
    setVoiceBlob(null);
    setRecordingTime(0);
    audioRef.current?.pause();
    audioRef.current = null;
    setIsPlaying(false);
  };

  const handleContinue = async () => {
    if (voiceBlob) {
      try {
        const mixed = await mixAudioFiles(voiceBlob);
        const buf = await mixed.arrayBuffer();
        const base64 = btoa(String.fromCharCode(...new Uint8Array(buf)));
        localStorage.setItem("eternal_beam_mixed_audio", base64);
      } catch {
        const buf = await voiceBlob.arrayBuffer();
        const base64 = btoa(String.fromCharCode(...new Uint8Array(buf)));
        localStorage.setItem("eternal_beam_mixed_audio", base64);
      }
    } else {
      localStorage.removeItem("eternal_beam_mixed_audio");
    }
    onContinue();
  };

  const handleThemeClick = (theme: MemorialTheme) => {
    try {
      localStorage.setItem("eternal_beam_theme_key", theme.themeKey);
      localStorage.setItem("eternal_beam_theme_id", String(theme.id));
    } catch {
      /* ignore */
    }
    if (theme.premium) {
      onSelectPremiumTheme(theme.id);
    } else {
      onSelectTheme(theme.id);
    }
  };

  const onCarouselPointerDown = (e: React.PointerEvent<HTMLDivElement>) => {
    const el = carouselRef.current;
    if (!el) return;
    draggingRef.current = true;
    dragStartXRef.current = e.clientX;
    dragStartScrollLeftRef.current = el.scrollLeft;
    el.setPointerCapture(e.pointerId);
  };

  const onCarouselPointerMove = (e: React.PointerEvent<HTMLDivElement>) => {
    const el = carouselRef.current;
    if (!el || !draggingRef.current) return;
    const dx = e.clientX - dragStartXRef.current;
    el.scrollLeft = dragStartScrollLeftRef.current - dx;
  };

  const onCarouselPointerUp = (e: React.PointerEvent<HTMLDivElement>) => {
    const el = carouselRef.current;
    draggingRef.current = false;
    if (el?.hasPointerCapture(e.pointerId)) el.releasePointerCapture(e.pointerId);
  };

  const scrollCarouselByCards = (direction: -1 | 1) => {
    const el = carouselRef.current;
    if (!el) return;
    const cardStep = Math.max(160, Math.round(el.clientWidth * 0.72));
    el.scrollBy({ left: direction * cardStep, behavior: "smooth" });
  };

  return (
    <div className="flex h-full flex-col overflow-hidden bg-[#0a0a0a]">
      {/* 스크롤은 테마 줄 옆이 아니라 화면 전체 한 줄로만 (중첩 세로 스크롤바 방지) */}
      <div className="hide-scrollbar min-h-0 flex-1 overflow-y-auto">
      {/* Header */}
      <header className="px-6 pt-14 pb-4 flex items-center relative">
        <motion.button
          initial={{ opacity: 0, x: -10 }}
          animate={{ opacity: 1, x: 0 }}
          onClick={onBack}
          className="w-10 h-10 rounded-full flex items-center justify-center relative"
          style={{
            background: "rgba(28, 28, 30, 0.8)",
          }}
          whileHover={{ scale: 1.05 }}
          whileTap={{ scale: 0.95 }}
        >
          {/* Glass Border */}
          <div className="absolute top-0 left-0 right-0 h-px rounded-t-full bg-gradient-to-r from-white/15 via-white/10 to-transparent" />
          <div className="absolute top-0 left-0 bottom-0 w-px rounded-l-full bg-gradient-to-b from-white/15 via-white/10 to-transparent" />
          <ArrowLeft className="w-4 h-4" style={{ color: "#E2E2E2" }} strokeWidth={1.5} />
        </motion.button>

        <div className="absolute left-1/2 -translate-x-1/2 text-center">
          <motion.h1
            initial={{ opacity: 0, y: -10 }}
            animate={{ opacity: 1, y: 0 }}
            className="text-xl font-light"
            style={{ color: "#F1E5D1" }}
          >
            <span className="absolute inset-0 blur-[8px] opacity-30" style={{ color: "#F1E5D1" }}>
              {tc.title}
            </span>
            <span className="relative">{tc.title}</span>
          </motion.h1>
          {walletCredits !== null ? (
            <p className="text-[11px] mt-1 font-light relative" style={{ color: "#c9a227" }}>
              {tc.creditsBalance(walletCredits)}
            </p>
          ) : null}
        </div>

        <div className="w-10" />
      </header>

      {!cutoutImage ? (
        <div className="mx-6 mb-2 px-4 py-3 rounded-xl text-[11px] leading-relaxed" style={{ background: "rgba(120, 80, 20, 0.25)", color: "#e8c97a", border: "1px solid rgba(201,162,39,0.35)" }}>
          {tc.cutoutMissing}
        </div>
      ) : null}

      {/* Preview Area */}
      <div className="px-8 py-6">
        <motion.div
          initial={{ opacity: 0, scale: 0.95 }}
          animate={{ opacity: 1, scale: 1 }}
          className="theme-preview-frame relative aspect-[4/3]"
        >
          <div className="memory-cta-card__shine" />
          
          {currentTheme && (
            <>
              {currentTheme.bgVideo ? (
                <ThemeBackgroundVideo
                  src={currentTheme.bgVideo}
                  poster={currentTheme.thumb}
                />
              ) : (
                <div
                  className="absolute inset-0 bg-center bg-cover"
                  style={{ backgroundImage: `url(${currentTheme.thumb})` }}
                />
              )}
              <div className={`absolute inset-0 bg-gradient-to-b ${currentTheme.gradient} opacity-30`} />
            </>
          )}
          {currentTheme && (
            <div
              className="absolute inset-0 pointer-events-none opacity-40"
              style={{
                background: `radial-gradient(circle at center, ${currentTheme.accent}20 0%, transparent 60%)`,
              }}
            />
          )}

          <div className="absolute inset-0 flex flex-col items-center justify-center p-4">
            {cutoutImage ? (
              <motion.img
                key={cutoutImage}
                initial={{ opacity: 0, scale: 0.9 }}
                animate={{ opacity: 1, scale: 1 }}
                src={cutoutImage}
                alt=""
                className="cutout-stage__subject max-h-[58%] max-w-[88%] pointer-events-none"
                style={{
                  filter: currentTheme
                    ? `drop-shadow(0 12px 28px ${currentTheme.accent}aa)`
                    : "drop-shadow(0 12px 28px rgba(201,162,39,0.5))",
                }}
              />
            ) : (
              <p className="text-sm font-light text-center" style={{ color: "#E2E2E2" }}>{tc.subject}</p>
            )}
          </div>

        </motion.div>
      </div>

      {/* 음성 메시지 (선택) — 기기 블루투스 재생용 */}
      <div className="px-6 pb-2">
        <button
          type="button"
          onClick={() => setShowVoiceSection((v) => !v)}
          className="w-full py-3 flex items-center justify-between rounded-xl transition-colors"
          style={{
            background: "rgba(28, 28, 30, 0.6)",
            border: "1px solid rgba(201, 162, 39, 0.2)",
            color: "#F1E5D1",
          }}
        >
          <span className="text-sm font-light flex items-center gap-2">
            <Mic className="w-4 h-4" style={{ color: "#c9a227" }} />
            음성 메시지 녹음 (선택)
            {voiceBlob && (
              <span className="text-[10px] font-normal" style={{ color: "#c9a227" }}>· 녹음됨</span>
            )}
          </span>
          {showVoiceSection ? <ChevronUp className="w-4 h-4" /> : <ChevronDown className="w-4 h-4" />}
        </button>
        {showVoiceSection && (
          <motion.div
            initial={{ opacity: 0, height: 0 }}
            animate={{ opacity: 1, height: "auto" }}
            className="mt-3 rounded-xl overflow-hidden"
            style={{
              background: "rgba(28, 28, 30, 0.5)",
              border: "1px solid rgba(201, 162, 39, 0.15)",
            }}
          >
            <div className="p-4 flex flex-col items-center gap-3">
              <div className="text-lg font-mono tabular-nums" style={{ color: "#c9a227" }}>
                {Math.floor(recordingTime / 60)}:{(recordingTime % 60).toString().padStart(2, "0")}
              </div>
              <div className="flex items-center gap-3">
                {!voiceBlob && !isRecording && (
                  <motion.button
                    type="button"
                    onClick={startRecording}
                    className="w-14 h-14 rounded-full flex items-center justify-center"
                    style={{
                      background: "linear-gradient(135deg, #b8860b 0%, #c9a227 100%)",
                      boxShadow: "0 4px 20px rgba(201, 162, 39, 0.35)",
                    }}
                    whileTap={{ scale: 0.95 }}
                  >
                    <Mic className="w-6 h-6 text-[#0a0a0a]" />
                  </motion.button>
                )}
                {isRecording && (
                  <motion.button
                    type="button"
                    onClick={stopRecording}
                    className="w-14 h-14 rounded-full flex items-center justify-center"
                    style={{
                      background: "rgba(200, 80, 80, 0.9)",
                      boxShadow: "0 4px 20px rgba(200, 80, 80, 0.35)",
                    }}
                    whileTap={{ scale: 0.95 }}
                  >
                    <Square className="w-6 h-6 text-white" fill="currentColor" />
                  </motion.button>
                )}
                {voiceBlob && !isRecording && (
                  <>
                    <motion.button
                      type="button"
                      onClick={playPreview}
                      className="w-12 h-12 rounded-full flex items-center justify-center"
                      style={{
                        background: "rgba(201, 162, 39, 0.2)",
                        border: "1px solid rgba(201, 162, 39, 0.4)",
                      }}
                      whileTap={{ scale: 0.95 }}
                    >
                      {isPlaying ? (
                        <Pause className="w-5 h-5" style={{ color: "#c9a227" }} />
                      ) : (
                        <Play className="w-5 h-5 ml-0.5" style={{ color: "#c9a227" }} fill="currentColor" />
                      )}
                    </motion.button>
                    <motion.button
                      type="button"
                      onClick={deleteRecording}
                      className="w-12 h-12 rounded-full flex items-center justify-center"
                      style={{
                        background: "rgba(80, 80, 80, 0.6)",
                        border: "1px solid rgba(255,255,255,0.1)",
                      }}
                      whileTap={{ scale: 0.95 }}
                    >
                      <Trash2 className="w-4 h-4" style={{ color: "#A1A1A6" }} />
                    </motion.button>
                  </>
                )}
              </div>
              <p className="text-[11px] font-light" style={{ color: "#A1A1A6" }}>
                {tc.voiceHint}
              </p>
            </div>
          </motion.div>
        )}
      </div>

      {/* Theme Carousel (Swipe) */}
      <div className="px-6 pb-2">
        <p 
          className="text-[11px] uppercase font-light mb-4 px-2 relative"
          style={{ color: "#E2E2E2", letterSpacing: "0.2em" }}
        >
          {/* Bloom effect */}
          <span className="absolute inset-0 blur-[4px] opacity-30">{tc.subtitle}</span>
          <span className="relative">{tc.subtitle}</span>
        </p>
        
        <div className="relative">
          <button
            type="button"
            aria-label={tc.prevTheme}
            onClick={() => scrollCarouselByCards(-1)}
            className="absolute left-1 top-1/2 z-10 -translate-y-1/2 rounded-full p-2"
            style={{
              background: "rgba(255,255,255,0.18)",
              backdropFilter: "blur(6px)",
              border: "1px solid rgba(255,255,255,0.35)",
            }}
          >
            <ArrowLeft className="h-4 w-4 text-white" />
          </button>

          <button
            type="button"
            aria-label={tc.nextTheme}
            onClick={() => scrollCarouselByCards(1)}
            className="absolute right-1 top-1/2 z-10 -translate-y-1/2 rounded-full p-2"
            style={{
              background: "rgba(255,255,255,0.18)",
              backdropFilter: "blur(6px)",
              border: "1px solid rgba(255,255,255,0.35)",
            }}
          >
            <ArrowRight className="h-4 w-4 text-white" />
          </button>

          <div
            ref={carouselRef}
            className="hide-scrollbar flex snap-x snap-proximity gap-4 overflow-x-auto overflow-y-hidden px-9 pb-2 cursor-grab active:cursor-grabbing [scroll-behavior:smooth] [-webkit-overflow-scrolling:touch] [overscroll-behavior-x:contain]"
            style={{ touchAction: "pan-x" }}
            onPointerDown={onCarouselPointerDown}
            onPointerMove={onCarouselPointerMove}
            onPointerUp={onCarouselPointerUp}
            onPointerCancel={onCarouselPointerUp}
          >
          {themes.map((theme, index) => (
            <motion.button
              key={theme.id}
              initial={{ opacity: 0, scale: 0.9 }}
              animate={{ opacity: 1, scale: 1 }}
              transition={{ delay: 0.2 + index * 0.05 }}
              onClick={() => handleThemeClick(theme)}
              className={`relative aspect-[3/4] w-[42vw] min-w-[140px] max-w-[220px] shrink-0 snap-center rounded-2xl overflow-hidden transition-all duration-300 ${
                selectedTheme === theme.id 
                  ? "ring-2 ring-[#c9a227] ring-offset-2 ring-offset-[#0a0a0a]" 
                  : ""
              }`}
              style={{
                background: "rgba(28, 28, 30, 0.6)",
              }}
              whileTap={{ scale: 0.98 }}
            >
              {/* Glass Border */}
              <div className="absolute top-0 left-2 right-2 h-px bg-gradient-to-r from-white/15 via-white/10 to-transparent" />
              <div className="absolute top-2 bottom-2 left-0 w-px bg-gradient-to-b from-white/15 via-white/10 to-transparent" />
              
              <div
                className="absolute inset-0 bg-center bg-cover"
                style={{ backgroundImage: `url(${theme.thumb})` }}
              />
              <div className={`absolute inset-0 bg-gradient-to-b ${theme.gradient} opacity-45`} />
              
              <div 
                className="absolute bottom-0 left-0 right-0 h-1/2"
                style={{ background: `linear-gradient(to top, ${theme.accent}40, transparent)` }}
              />

              {theme.premium && (
                <div className="absolute inset-0 flex items-center justify-center bg-black/40">
                  <div 
                    className="w-8 h-8 rounded-full flex items-center justify-center relative"
                    style={{
                      background: "rgba(255,255,255,0.06)",
                      backdropFilter: "blur(10px)",
                    }}
                  >
                    {/* Glass Border */}
                    <div className="absolute top-0 left-0 right-0 h-px rounded-t-full bg-gradient-to-r from-white/15 via-white/10 to-transparent" />
                    <Lock className="w-3.5 h-3.5" style={{ color: "#E2E2E2" }} strokeWidth={1.5} />
                  </div>
                </div>
              )}

              {selectedTheme === theme.id && !theme.premium && (
                <motion.div
                  initial={{ scale: 0 }}
                  animate={{ scale: 1 }}
                  className="absolute top-2 right-2 w-5 h-5 rounded-full flex items-center justify-center"
                  style={{
                    background: "linear-gradient(135deg, #c9a227, #f5d77a)",
                    boxShadow: "0 2px 10px rgba(201, 162, 39, 0.5)",
                  }}
                >
                  <Sparkles className="w-3 h-3 text-[#0a0a0a]" />
                </motion.div>
              )}

              {/* Price Tag for Premium */}
              {theme.premium && (
                <div 
                  className="absolute top-2 left-2 px-1.5 py-0.5 rounded-md text-[8px] font-medium"
                  style={{
                    background: "rgba(201, 162, 39, 0.9)",
                    color: "#0a0a0a",
                  }}
                >
                  {theme.price}
                </div>
              )}

              <div className="absolute bottom-2 left-0 right-0 text-center">
                <span 
                  className="text-[9px] font-light"
                  style={{ color: "#F1E5D1", letterSpacing: "0.05em" }}
                >
                  {themeLabel(theme)}
                </span>
              </div>
            </motion.button>
          ))}
          </div>
        </div>
        <p className="mt-2 px-1 text-[10px] font-light" style={{ color: "#A1A1A6" }}>
          {tc.swipeHint}
        </p>
      </div>
      </div>

      {/* Actions */}
      <div className="px-8 py-6 space-y-3">
        {selectedTheme && walletCredits !== null ? (
          <p className="text-center text-[11px] font-light" style={{ color: "#A1A1A6" }}>
            {walletCredits < creditCost
              ? tc.needMoreCredits(walletCredits, creditCost)
              : tc.creditsCost(creditCost)}
          </p>
        ) : null}
        {onBuyCreditsMock &&
        (walletCredits === null || walletCredits < creditCost) ? (
          <motion.button
            type="button"
            initial={{ opacity: 0, y: 8 }}
            animate={{ opacity: 1, y: 0 }}
            onClick={onBuyCreditsMock}
            disabled={creditPackBusy || creditBusy}
            className="w-full py-3 rounded-2xl text-[13px] font-light"
            style={{
              background: "rgba(28, 28, 30, 0.9)",
              border: "1px solid rgba(201, 162, 39, 0.35)",
              color: "#d4af37",
              cursor: creditPackBusy ? "wait" : "pointer",
            }}
          >
            {creditPackBusy ? tc.buyCreditsBusy : tc.buyCreditsMock}
          </motion.button>
        ) : null}
        <motion.button
          initial={{ opacity: 0, y: 20 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ delay: 0.4 }}
          onClick={handleContinue}
          disabled={!selectedTheme || creditBusy}
          className="w-full py-4 rounded-2xl font-normal text-[15px] transition-all duration-300 relative overflow-hidden"
          style={{
            background: selectedTheme
              ? "linear-gradient(135deg, #b8860b 0%, #c9a227 30%, #d4af37 50%, #f5d77a 70%, #d4af37 100%)"
              : "rgba(28, 28, 30, 0.8)",
            color: selectedTheme ? "#0a0a0a" : "#E2E2E2",
            boxShadow: selectedTheme
              ? "0 10px 40px rgba(201, 162, 39, 0.25)"
              : "none",
            cursor: selectedTheme ? "pointer" : "not-allowed",
          }}
          whileHover={selectedTheme ? { scale: 1.02 } : {}}
          whileTap={selectedTheme ? { scale: 0.98 } : {}}
        >
          {/* Glass Border for disabled state */}
          {!selectedTheme && (
            <>
              <div className="absolute top-0 left-4 right-4 h-px bg-gradient-to-r from-white/10 via-white/05 to-transparent" />
              <div className="absolute top-4 bottom-4 left-0 w-px bg-gradient-to-b from-white/10 via-white/05 to-transparent" />
            </>
          )}
          {/* Top shine for enabled state */}
          {selectedTheme && (
            <div className="absolute top-0 left-8 right-8 h-px bg-gradient-to-r from-transparent via-white/40 to-transparent" />
          )}
          {creditBusy
            ? tc.generatingMotions
            : selectedTheme
              ? tc.continue
              : tc.selectFirst}
        </motion.button>

        <motion.button
          initial={{ opacity: 0, y: 20 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ delay: 0.5 }}
          onClick={onSkip}
          className="w-full py-3 text-center"
        >
          <span
            className="text-sm font-light relative"
            style={{ color: "#E2E2E2" }}
          >
            {/* Bloom effect */}
            <span className="absolute inset-0 blur-[4px] opacity-30">{tc.skip}</span>
            <span className="relative">{tc.skip}</span>
          </span>
        </motion.button>
      </div>
      <style>{`
        .hide-scrollbar {
          -ms-overflow-style: none;
          scrollbar-width: none;
        }
        .hide-scrollbar::-webkit-scrollbar {
          display: none;
          width: 0;
          height: 0;
        }
      `}</style>
    </div>
  );
}
