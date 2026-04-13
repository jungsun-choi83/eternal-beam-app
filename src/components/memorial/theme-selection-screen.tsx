"use client";

import { useState, useRef, useEffect } from "react";
import { motion } from "framer-motion";
import { ArrowLeft, Lock, Sparkles, Mic, Square, Play, Pause, Trash2, ChevronDown, ChevronUp } from "lucide-react";
import { mixAudioFiles } from "@/app/services/audioMixer";

interface ThemeSelectionScreenProps {
  cutoutImage: string | null;
  selectedTheme: number | null;
  onSelectTheme: (themeId: number) => void;
  onSelectPremiumTheme: (themeId: number) => void;
  onContinue: () => void;
  onSkip: () => void;
  onBack: () => void;
}

const themes = [
  { id: 1, name: "Celestial", gradient: "from-indigo-900 via-purple-900 to-black", accent: "#8b5cf6", premium: false, price: "", thumb: "/theme-thumbs/celestial.jpg" },
  { id: 2, name: "Golden Meadow", gradient: "from-amber-900 via-yellow-900 to-black", accent: "#f59e0b", premium: false, price: "", thumb: "/theme-thumbs/golden_meadow.jpg" },
  { id: 3, name: "Starlight", gradient: "from-slate-900 via-zinc-800 to-black", accent: "#e4e4e7", premium: false, price: "", thumb: "/theme-thumbs/starlight.jpg" },
  { id: 4, name: "Aurora", gradient: "from-emerald-900 via-teal-900 to-black", accent: "#10b981", premium: true, price: "$2.99", thumb: "/theme-thumbs/aurora.jpg" },
  { id: 5, name: "Sunset", gradient: "from-rose-900 via-orange-900 to-black", accent: "#f43f5e", premium: true, price: "$2.99", thumb: "/theme-thumbs/sunset.jpg" },
  { id: 6, name: "Ocean Deep", gradient: "from-blue-900 via-cyan-900 to-black", accent: "#06b6d4", premium: true, price: "$2.99", thumb: "/theme-thumbs/ocean_deep.jpg" },
];

export function ThemeSelectionScreen({ 
  cutoutImage,
  selectedTheme, 
  onSelectTheme, 
  onSelectPremiumTheme,
  onContinue, 
  onSkip,
  onBack 
}: ThemeSelectionScreenProps) {
  const currentTheme = themes.find(t => t.id === selectedTheme);

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

  useEffect(() => {
    return () => {
      if (timerRef.current) clearInterval(timerRef.current);
      if (audioPreviewUrl) URL.revokeObjectURL(audioPreviewUrl);
    };
  }, [audioPreviewUrl]);

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
      alert("마이크 권한이 필요합니다.");
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

  const handleThemeClick = (theme: typeof themes[0]) => {
    if (theme.premium) {
      onSelectPremiumTheme(theme.id);
    } else {
      onSelectTheme(theme.id);
    }
  };

  return (
    <div className="h-full flex flex-col bg-[#0a0a0a]">
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

        <motion.h1
          initial={{ opacity: 0, y: -10 }}
          animate={{ opacity: 1, y: 0 }}
          className="text-xl font-light absolute left-1/2 -translate-x-1/2"
          style={{ color: "#F1E5D1" }}
        >
          {/* Bloom effect */}
          <span className="absolute inset-0 blur-[8px] opacity-30" style={{ color: "#F1E5D1" }}>Theme</span>
          <span className="relative">Theme</span>
        </motion.h1>

        <div className="w-10" />
      </header>

      {/* Preview Area */}
      <div className="px-8 py-6">
        <motion.div
          initial={{ opacity: 0, scale: 0.95 }}
          animate={{ opacity: 1, scale: 1 }}
          className="relative aspect-[4/3] rounded-3xl overflow-hidden"
          style={{
            background: "rgba(28, 28, 30, 0.6)",
          }}
        >
          {/* Glass Border */}
          <div className="absolute top-0 left-4 right-4 h-px bg-gradient-to-r from-white/20 via-white/15 to-transparent" />
          <div className="absolute top-4 bottom-4 left-0 w-px bg-gradient-to-b from-white/20 via-white/15 to-transparent" />
          
          {currentTheme && (
            <div className={`absolute inset-0 bg-gradient-to-b ${currentTheme.gradient} opacity-80`} />
          )}
          
          {currentTheme && (
            <motion.div
              className="absolute inset-0"
              style={{
                background: `radial-gradient(circle at center, ${currentTheme.accent}20 0%, transparent 60%)`,
              }}
              animate={{ opacity: [0.3, 0.6, 0.3] }}
              transition={{ duration: 3, repeat: Infinity }}
            />
          )}

          <div className="absolute inset-0 flex flex-col items-center justify-center">
            {cutoutImage ? (
              <motion.div
                initial={{ opacity: 0, y: 20 }}
                animate={{ opacity: 1, y: 0 }}
                className="relative w-24 h-24 rounded-full overflow-hidden"
                style={{
                  boxShadow: currentTheme 
                    ? `0 0 40px ${currentTheme.accent}40, 0 0 80px ${currentTheme.accent}20`
                    : "0 0 40px rgba(201, 162, 39, 0.2)",
                }}
              >
                <img src={cutoutImage} alt="Subject" className="w-full h-full object-cover" />
              </motion.div>
            ) : (
              <p className="text-sm font-light text-center" style={{ color: "#E2E2E2" }}>Subject</p>
            )}
          </div>

          {/* Hologram Effect Lines */}
          <div className="absolute inset-0 opacity-20 pointer-events-none">
            {[...Array(8)].map((_, i) => (
              <motion.div
                key={i}
                className="absolute left-0 right-0 h-[1px]"
                style={{
                  top: `${12 + i * 12}%`,
                  background: "linear-gradient(90deg, transparent, rgba(255,255,255,0.3), transparent)",
                }}
                animate={{
                  opacity: [0.1, 0.4, 0.1],
                  scaleX: [0.8, 1, 0.8],
                }}
                transition={{
                  duration: 2,
                  delay: i * 0.2,
                  repeat: Infinity,
                }}
              />
            ))}
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
                녹음한 목소리는 기기에서 재생됩니다 (최대 60초)
              </p>
            </div>
          </motion.div>
        )}
      </div>

      {/* Theme Grid */}
      <div className="flex-1 px-6 overflow-auto">
        <p 
          className="text-[11px] uppercase font-light mb-4 px-2 relative"
          style={{ color: "#E2E2E2", letterSpacing: "0.2em" }}
        >
          {/* Bloom effect */}
          <span className="absolute inset-0 blur-[4px] opacity-30">Select Environment</span>
          <span className="relative">Select Environment</span>
        </p>
        
        <div className="grid grid-cols-3 gap-3">
          {themes.map((theme, index) => (
            <motion.button
              key={theme.id}
              initial={{ opacity: 0, scale: 0.9 }}
              animate={{ opacity: 1, scale: 1 }}
              transition={{ delay: 0.2 + index * 0.05 }}
              onClick={() => handleThemeClick(theme)}
              className={`relative aspect-[3/4] rounded-2xl overflow-hidden transition-all duration-300 ${
                selectedTheme === theme.id 
                  ? "ring-2 ring-[#c9a227] ring-offset-2 ring-offset-[#0a0a0a]" 
                  : ""
              }`}
              style={{
                background: "rgba(28, 28, 30, 0.6)",
              }}
              whileHover={{ scale: 1.03, y: -2 }}
              whileTap={{ scale: 0.97 }}
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
                  {theme.name}
                </span>
              </div>
            </motion.button>
          ))}
        </div>
      </div>

      {/* Actions */}
      <div className="px-8 py-6 space-y-3">
        <motion.button
          initial={{ opacity: 0, y: 20 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ delay: 0.4 }}
          onClick={handleContinue}
          disabled={!selectedTheme}
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
          Preview
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
            <span className="absolute inset-0 blur-[4px] opacity-30">Skip theme selection</span>
            <span className="relative">Skip theme selection</span>
          </span>
        </motion.button>
      </div>
    </div>
  );
}
