"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ArrowLeft, Mic } from "lucide-react";
import { memorialT } from "@/components/memorial/memorial-i18n";
import { getWakeNames } from "@/lib/pet-profile";
import {
  createPackedAlphaScratch,
  drawPackedAlphaVideo,
  isPackedAlphaVideo,
} from "@/lib/packed-alpha-canvas";
import { forestDemoAssets } from "@/lib/forest-demo-config";
import {
  resolvePiSseUrl,
  subscribePiSensors,
  triggerForestMachineDemo,
  resolvePiHttpBase,
  discoverPiHttpBase,
} from "@/lib/pi-sensor-bridge";

const DEFAULT_WAKE = ["고야", "고야야"];

type SpeechRecognitionCtor = new () => SpeechRecognitionInstance;

type SpeechRecognitionInstance = {
  lang: string;
  continuous: boolean;
  interimResults: boolean;
  start: () => void;
  stop: () => void;
  onresult: ((event: SpeechRecognitionResultEvent) => void) | null;
  onerror: ((event: { error: string }) => void) | null;
  onend: (() => void) | null;
};

type SpeechRecognitionResultEvent = {
  results: SpeechRecognitionResultList;
};

type SpeechRecognitionResultList = {
  length: number;
  [index: number]: { [index: number]: { transcript: string } };
};

interface ForestExperienceScreenProps {
  language?: string;
  publicDemo?: boolean;
  onBack: () => void;
  onComplete?: () => void;
}

function normalizeSpeech(text: string): string {
  return text.replace(/\s+/g, "").toLowerCase();
}

function matchesWakeWord(spoken: string, wakeNames: string[]): boolean {
  const norm = normalizeSpeech(spoken);
  return wakeNames.some((name) => {
    const n = normalizeSpeech(name);
    return n.length > 0 && norm.includes(n);
  });
}

export function ForestExperienceScreen({
  language = "ko",
  publicDemo = false,
  onBack,
  onComplete,
}: ForestExperienceScreenProps) {
  const t = memorialT(language).experience;

  const containerRef = useRef<HTMLDivElement>(null);
  const bgRef = useRef<HTMLVideoElement>(null);
  const idleRef = useRef<HTMLVideoElement>(null);
  const actionRef = useRef<HTMLVideoElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const scratchRef = useRef(createPackedAlphaScratch());
  const rafRef = useRef(0);
  const actionPlayingRef = useRef(false);
  const recognitionRef = useRef<SpeechRecognitionInstance | null>(null);

  const [voiceReady, setVoiceReady] = useState(false);
  const [voiceHint, setVoiceHint] = useState<string | null>(null);
  const piSseUrl = useMemo(() => resolvePiSseUrl(), []);

  const wakeNames = useMemo(() => {
    const parsed = getWakeNames();
    const merged = [...DEFAULT_WAKE, ...parsed];
    return [...new Set(merged)];
  }, []);

  const primaryWake = wakeNames.find((n) => n !== "고야" && n !== "고야야") ?? "고야야";

  const resizeCanvas = useCallback(() => {
    const container = containerRef.current;
    const canvas = canvasRef.current;
    if (!container || !canvas) return;

    const rect = container.getBoundingClientRect();
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    canvas.width = Math.round(rect.width * dpr);
    canvas.height = Math.round(rect.height * dpr);
    canvas.style.width = `${rect.width}px`;
    canvas.style.height = `${rect.height}px`;

    const ctx = canvas.getContext("2d");
    if (ctx) ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  }, []);

  const startIdle = useCallback(() => {
    actionPlayingRef.current = false;
    const action = actionRef.current;
    const idle = idleRef.current;
    if (action) {
      action.pause();
      action.currentTime = 0;
    }
    void idle?.play().catch(() => {});
  }, []);

  const beginAction = useCallback(() => {
    if (actionPlayingRef.current) return;
    const idle = idleRef.current;
    const action = actionRef.current;
    if (!action || !idle) return;

    actionPlayingRef.current = true;
    idle.pause();
    action.currentTime = 0;
    void action.play().catch(() => {
      actionPlayingRef.current = false;
      startIdle();
    });
  }, [startIdle]);

  const renderDogFrame = useCallback(() => {
    const canvas = canvasRef.current;
    const idle = idleRef.current;
    const action = actionRef.current;
    if (!canvas || !idle || !action) {
      rafRef.current = requestAnimationFrame(renderDogFrame);
      return;
    }

    const src = actionPlayingRef.current ? action : idle;
    const ctx = canvas.getContext("2d");
    if (!ctx || src.readyState < 2) {
      rafRef.current = requestAnimationFrame(renderDogFrame);
      return;
    }

    const cw = canvas.width / (Math.min(window.devicePixelRatio || 1, 2));
    const ch = canvas.height / (Math.min(window.devicePixelRatio || 1, 2));

    ctx.clearRect(0, 0, cw, ch);

    if (!isPackedAlphaVideo(src)) {
      rafRef.current = requestAnimationFrame(renderDogFrame);
      return;
    }

    const vw = src.videoWidth;
    const frameH = Math.floor(src.videoHeight / 2);
    const aspect = vw / frameH;
    const maxW = cw * 0.72;
    const maxH = ch * 0.62;
    let drawW = maxW;
    let drawH = drawW / aspect;
    if (drawH > maxH) {
      drawH = maxH;
      drawW = drawH * aspect;
    }
    const dx = (cw - drawW) / 2;
    const dy = ch - drawH - ch * 0.06;

    drawPackedAlphaVideo(ctx, src, dx, dy, drawW, drawH, scratchRef.current);
    rafRef.current = requestAnimationFrame(renderDogFrame);
  }, []);

  useEffect(() => {
    resizeCanvas();
    window.addEventListener("resize", resizeCanvas);
    return () => window.removeEventListener("resize", resizeCanvas);
  }, [resizeCanvas]);

  useEffect(() => {
    void discoverPiHttpBase().then((base) => {
      if (base) return;
      if (!resolvePiHttpBase()) return;
      setVoiceHint(
        language === "ko"
          ? "Pi 자동 탐색 중… 안 되면 ?pi=라즈베리IP"
          : "Scanning for Pi… or add ?pi=RASPBERRY_IP",
      );
    });
    void triggerForestMachineDemo().then((ok) => {
      if (ok) {
        setVoiceHint(
          language === "ko"
            ? "기계 연결됨 — S23 idle + 터치·음성 대기"
            : "Machine linked — idle on display, touch or voice",
        );
      } else {
        setVoiceHint(
          language === "ko"
            ? "Pi 연결 실패 — URL에 ?pi=라즈베리IP 추가"
            : "Pi connect failed — add ?pi=RASPBERRY_IP to URL",
        );
      }
    });
  }, [language]);

  useEffect(() => {
    const bg = bgRef.current;
    const idle = idleRef.current;
    const action = actionRef.current;
    if (!bg || !idle || !action) return;

    bg.src = forestDemoAssets.background;
    idle.src = forestDemoAssets.idle;
    action.src = forestDemoAssets.action;
    idle.loop = true;
    action.loop = false;

    const onIdleReady = () => startIdle();
    idle.addEventListener("loadeddata", onIdleReady);

    const onActionEnded = () => startIdle();
    action.addEventListener("ended", onActionEnded);

    void bg.play().catch(() => {});

    rafRef.current = requestAnimationFrame(renderDogFrame);

    return () => {
      idle.removeEventListener("loadeddata", onIdleReady);
      action.removeEventListener("ended", onActionEnded);
      cancelAnimationFrame(rafRef.current);
      idle.pause();
      action.pause();
      bg.pause();
    };
  }, [renderDogFrame, startIdle]);

  useEffect(() => {
    if (piSseUrl) {
      return subscribePiSensors(beginAction, (msg) => {
        setVoiceHint(msg);
        if (msg.includes('연결됨')) setVoiceReady(true);
      });
    }

    const win = window as Window & {
      SpeechRecognition?: SpeechRecognitionCtor;
      webkitSpeechRecognition?: SpeechRecognitionCtor;
    };
    const Ctor = win.SpeechRecognition ?? win.webkitSpeechRecognition;
    if (!Ctor) {
      setVoiceHint(t.voiceUnavailable);
      return;
    }

    const recognition = new Ctor();
    recognition.lang = language === "en" ? "en-US" : "ko-KR";
    recognition.continuous = true;
    recognition.interimResults = false;

    recognition.onresult = (event) => {
      for (let i = event.results.length - 1; i >= 0; i--) {
        const transcript = event.results[i]?.[0]?.transcript ?? "";
        if (matchesWakeWord(transcript, wakeNames)) {
          beginAction();
          break;
        }
      }
    };

    recognition.onerror = (event) => {
      if (event.error === "not-allowed") {
        setVoiceHint(t.micDenied);
      }
    };

    recognition.onend = () => {
      try {
        recognition.start();
      } catch {
        /* ignore restart race */
      }
    };

    recognitionRef.current = recognition;

    try {
      recognition.start();
      setVoiceReady(true);
    } catch {
      setVoiceHint(t.voiceUnavailable);
    }

    return () => {
      recognition.onresult = null;
      recognition.onerror = null;
      recognition.onend = null;
      try {
        recognition.stop();
      } catch {
        /* ignore */
      }
      recognitionRef.current = null;
    };
  }, [beginAction, language, piSseUrl, t.micDenied, t.voiceUnavailable, wakeNames]);

  return (
    <div
      ref={containerRef}
      // Phase 10 — 재생 표면은 어둡게 유지한다. 셸이 더는 검정을 상속해 주지
      // 않으므로 미디어 무대가 자기 바탕(inverse)을 명시한다.
      className="forest-experience-screen relative w-full h-full bg-[var(--eb-surface-inverse)] text-[var(--eb-text-on-inverse)] overflow-hidden select-none"
      onPointerDown={(e) => {
        e.preventDefault();
        beginAction();
      }}
    >
      <video
        ref={bgRef}
        className="absolute inset-0 w-full h-full object-cover"
        playsInline
        muted
        loop
        autoPlay
      />

      <canvas ref={canvasRef} className="absolute inset-0 z-[2] pointer-events-none" />

      <video ref={idleRef} className="hidden" playsInline muted preload="auto" />
      <video ref={actionRef} className="hidden" playsInline muted preload="auto" />

      {/* 미디어 위 크롬 — 칩은 .glass-dark, 기본 CTA 만 골드. */}
      <div className="absolute inset-x-0 top-0 z-[3] flex items-center justify-between gap-2 px-4 pt-[var(--eb-header-top)]">
        <button
          type="button"
          onClick={(e) => {
            e.stopPropagation();
            onBack();
          }}
          className="glass-dark flex min-h-[var(--eb-touch)] items-center gap-1.5 rounded-full px-4 py-2 text-sm text-[var(--eb-text-on-inverse)]"
        >
          <ArrowLeft className="h-4 w-4" />
          {t.back}
        </button>

        {publicDemo ? (
          <span className="glass-dark inline-flex items-center gap-1.5 rounded-full px-3 py-1.5 text-xs text-[var(--eb-text-on-inverse)]">
            <span className="eb-status-dot eb-status-dot--success" aria-hidden />
            {t.publicBadge}
          </span>
        ) : null}

        {onComplete && !publicDemo ? (
          <button
            type="button"
            onClick={(e) => {
              e.stopPropagation();
              onComplete();
            }}
            className="eb-btn eb-btn--primary rounded-full px-5 text-sm font-medium"
          >
            {t.done}
          </button>
        ) : null}
      </div>

      <div className="absolute inset-x-0 bottom-0 z-[3] px-6 pb-[var(--eb-footer-bottom)] text-center pointer-events-none">
        <p className="text-sm text-white/85 drop-shadow-md">
          {piSseUrl
            ? language === "ko"
              ? "손을 가까이 대거나 말하면 고야가 반응합니다"
              : "Move close or speak — Goya reacts"
            : t.hintWake(primaryWake)}
        </p>
        <p className="mt-1 flex items-center justify-center gap-1.5 text-xs text-white/55">
          <Mic
            className={`h-3.5 w-3.5 ${voiceReady ? "" : "opacity-50"}`}
            style={voiceReady ? { color: "var(--eb-sage)" } : undefined}
          />
          {voiceHint ?? (voiceReady ? t.listening : t.voiceUnavailable)}
        </p>
      </div>
    </div>
  );
}
