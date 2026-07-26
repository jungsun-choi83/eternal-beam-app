"use client";

import { useState, useEffect } from "react";
import { motion, AnimatePresence } from "framer-motion";
import { ChevronRight } from "lucide-react";
import { memorialT } from "@/components/memorial/memorial-i18n";
import { LanguageToggle } from "./language-toggle";

interface OnboardingScreenProps {
  language?: string;
  onLanguageChange?: (lang: "ko" | "en") => void;
  onComplete: () => void;
  onTryForest?: () => void;
}

export function OnboardingScreen({
  language = "ko",
  onLanguageChange,
  onComplete,
  onTryForest,
}: OnboardingScreenProps) {
  const t = memorialT(language);
  const ob = t.onboarding;
  const slides = ob.slides;
  const [currentSlide, setCurrentSlide] = useState(0);
  const [shootingStar, setShootingStar] = useState(false);
  const [reducedMotion, setReducedMotion] = useState(false);

  useEffect(() => {
    const m = window.matchMedia("(prefers-reduced-motion: reduce)");
    setReducedMotion(m.matches);
    const on = () => setReducedMotion(m.matches);
    m.addEventListener("change", on);
    return () => m.removeEventListener("change", on);
  }, []);

  useEffect(() => {
    if (reducedMotion) return;
    const timer = setTimeout(() => setShootingStar(true), 2000);
    return () => clearTimeout(timer);
  }, [reducedMotion]);

  useEffect(() => {
    if (!shootingStar) return;
    const hide = setTimeout(() => setShootingStar(false), 1600);
    return () => clearTimeout(hide);
  }, [shootingStar]);

  useEffect(() => {
    if (reducedMotion || shootingStar) return;
    const delay = 7000 + Math.random() * 3000;
    const timer = setTimeout(() => setShootingStar(true), delay);
    return () => clearTimeout(timer);
  }, [reducedMotion, shootingStar]);

  const handleNext = () => {
    if (currentSlide < slides.length - 1) {
      setCurrentSlide(currentSlide + 1);
    } else {
      onComplete();
    }
  };

  const handleSkip = () => {
    onComplete();
  };

  const ringDelays = ["0s", "1.15s", "2.3s"] as const;

  return (
    <div
      data-screen="onboarding"
      className="flex flex-col relative overflow-hidden w-full h-full min-h-full"
    >
      <style>{`
        @keyframes ob-ring-ripple {
          0% { transform: scale(0.55); opacity: 0.75; }
          70% { opacity: 0.15; }
          100% { transform: scale(1.55); opacity: 0; }
        }
        @keyframes ob-glow-breathe {
          0%, 100% {
            box-shadow: 0 0 64px 24px rgba(200,155,42,0.1), 0 0 100px 40px rgba(180,130,30,0.05);
          }
          50% {
            box-shadow: 0 0 96px 36px rgba(244,208,63,0.2), 0 0 140px 56px rgba(201,162,39,0.1);
          }
        }
        @keyframes ob-center-light {
          0%, 100% {
            box-shadow: inset 0 0 20px rgba(212,175,55,0.15), 0 0 28px rgba(201,162,39,0.12);
          }
          50% {
            box-shadow: inset 0 0 36px rgba(244,208,63,0.35), 0 0 44px rgba(201,162,39,0.22);
          }
        }
        @keyframes ob-shooting-star {
          0% { transform: translate(0, 0); opacity: 0; }
          5% { opacity: 1; }
          95% { opacity: 1; }
          100% { transform: translate(-120vw, 70vh); opacity: 0; }
        }
        .ob-onboarding-hero {
          animation: ob-glow-breathe 4s ease-in-out infinite;
        }
        .ob-ring-pulse {
          position: absolute;
          border-radius: 9999px;
          border: 1.5px solid rgba(212,175,55,0.55);
          animation: ob-ring-ripple 3.2s ease-out infinite;
          transform-origin: center center;
          will-change: transform, opacity;
        }
        .ob-center-disc {
          animation: ob-center-light 3.6s ease-in-out infinite;
          will-change: box-shadow;
        }
        .ob-center-num {
          font-family: var(--font-headline);
          font-size: 2rem;
          font-weight: 300;
          letter-spacing: 0.04em;
          color: #d4af37;
        }
        @media (prefers-reduced-motion: reduce) {
          .ob-ring-pulse, .ob-onboarding-hero, .ob-center-disc {
            animation: none !important;
          }
          .ob-shooting-star { animation: none !important; opacity: 0 !important; }
        }
      `}</style>

      <div className="absolute top-6 left-6 right-6 z-20 flex items-center justify-between gap-3 pointer-events-auto">
        <LanguageToggle
          language={language}
          onChange={(code) => onLanguageChange?.(code)}
          className="relative z-20"
        />
        <button type="button" onClick={handleSkip} className="eb-skip-text shrink-0">
          {t.common.skip}
        </button>
      </div>

      {shootingStar && !reducedMotion && (
        <div className="absolute inset-0 z-10 pointer-events-none overflow-hidden">
          <div
            className="ob-shooting-star absolute"
            style={{
              width: 90,
              height: 2,
              top: "20%",
              right: "20%",
              background:
                "linear-gradient(90deg, transparent 0%, rgba(244,208,63,0.95) 35%, rgba(212,175,55,0.5) 100%)",
              boxShadow: "0 0 8px rgba(244,208,63,0.8)",
              transformOrigin: "right center",
              animation: "ob-shooting-star 1.5s ease-out forwards",
            }}
          />
        </div>
      )}

      <div className="flex-1 flex flex-col items-center justify-center px-8 relative z-[3] min-h-0">
        {/* 링·중앙 원 — 슬라이드 전환 밖 (transform 충돌 방지) */}
        <div
          className="relative flex items-center justify-center mx-auto mb-8 ob-onboarding-hero rounded-full shrink-0"
          style={{ width: 220, height: 220 }}
        >
          <div
            className="absolute inset-0 flex items-center justify-center pointer-events-none"
            aria-hidden
          >
            {ringDelays.map((delay, i) => (
              <div
                key={i}
                className="ob-ring-pulse"
                style={{
                  width: 176,
                  height: 176,
                  animationDelay: delay,
                }}
              />
            ))}
          </div>

          <div
            className="ob-center-disc w-32 h-32 rounded-full relative z-10"
            style={{
              background:
                "linear-gradient(135deg, rgba(212, 175, 55, 0.14) 0%, rgba(201, 162, 39, 0.06) 100%)",
              border: "1px solid rgba(212, 175, 55, 0.35)",
            }}
          >
            <div className="absolute inset-0 flex items-center justify-center">
              <span className="ob-center-num">{currentSlide + 1}</span>
            </div>
          </div>
        </div>

        <AnimatePresence mode="wait">
          <motion.div
            key={currentSlide}
            initial={{ opacity: 0, y: 16 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: -12 }}
            transition={{ duration: 0.35 }}
            className="text-center relative z-10 w-full"
          >
            <h1 className="upload-title text-center mb-3 px-2 eb-headline-preline" style={{ color: "#F5F5F7" }}>
              {slides[currentSlide].title}
            </h1>
            <p className="gold-subtitle mb-4 text-center" style={{ color: "#d4af37" }}>
              {slides[currentSlide].subtitle}
            </p>
            <p className="memorial-body text-center max-w-[17rem] mx-auto px-2">
              {slides[currentSlide].description}
            </p>
          </motion.div>
        </AnimatePresence>
      </div>

      <div className="flex justify-center gap-2 mb-8 relative z-[3]">
        {slides.map((_, index) => (
          <button
            key={index}
            type="button"
            onClick={() => setCurrentSlide(index)}
            className={`eb-page-dot ${index === currentSlide ? "eb-page-dot--active" : ""}`}
            aria-label={`Slide ${index + 1}`}
          />
        ))}
      </div>

      <div className="px-8 pb-10 relative z-[3] space-y-3">
        {onTryForest ? (
          <motion.button
            type="button"
            onClick={onTryForest}
            className="w-full py-3.5 rounded-full text-sm font-medium"
            style={{
              background: "rgba(255,255,255,0.08)",
              border: "1px solid rgba(110, 231, 183, 0.35)",
              color: "#a7f3d0",
            }}
            whileHover={{ scale: 1.01 }}
            whileTap={{ scale: 0.98 }}
          >
            {ob.tryForest}
          </motion.button>
        ) : null}
        <motion.button
          type="button"
          onClick={handleNext}
          className="cta-gold w-full py-4 flex items-center justify-center gap-2"
          whileHover={{ scale: 1.01 }}
          whileTap={{ scale: 0.975 }}
        >
          <span className="text-[#0a0a0a] memorial-btn-label">
            {currentSlide === slides.length - 1 ? ob.getStarted : ob.continue}
          </span>
          <ChevronRight className="w-5 h-5 text-[#0a0a0a]" />
        </motion.button>
      </div>
    </div>
  );
}
