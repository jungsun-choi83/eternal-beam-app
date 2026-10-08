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
      {/* Phase 10 — ambience recoloured to brand gold (184,150,62) on ivory:
          glow ≤ 0.14 alpha, rings a gold hairline, shooting star ≤ 0.4.
          Class names / keyframes unchanged; shared colours live in
          onboarding-effects.css, this block only adds the ripple geometry. */}
      <style>{`
        @keyframes ob-ring-ripple {
          0% { transform: scale(0.55); opacity: 0.55; }
          70% { opacity: 0.12; }
          100% { transform: scale(1.55); opacity: 0; }
        }
        @keyframes ob-glow-breathe {
          0%, 100% {
            box-shadow: 0 0 64px 24px rgba(184,150,62,0.06), 0 0 100px 40px rgba(184,150,62,0.03);
          }
          50% {
            box-shadow: 0 0 96px 36px rgba(184,150,62,0.12), 0 0 140px 56px rgba(184,150,62,0.06);
          }
        }
        @keyframes ob-center-light {
          0%, 100% {
            box-shadow: inset 0 0 20px rgba(184,150,62,0.1), 0 0 28px rgba(184,150,62,0.06);
          }
          50% {
            box-shadow: inset 0 0 36px rgba(184,150,62,0.14), 0 0 44px rgba(184,150,62,0.12);
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
          border: 1px solid var(--eb-gold-line);
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
          color: var(--eb-gold-text);
        }
        @media (prefers-reduced-motion: reduce) {
          .ob-ring-pulse, .ob-onboarding-hero, .ob-center-disc {
            animation: none !important;
          }
          .ob-shooting-star { animation: none !important; opacity: 0 !important; }
        }
      `}</style>

      <div
        className="absolute left-6 right-6 z-20 flex items-center justify-between gap-3 pointer-events-auto"
        style={{ top: "var(--eb-header-top)" }}
      >
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
                "linear-gradient(90deg, transparent 0%, rgba(184,150,62,0.4) 35%, rgba(184,150,62,0.18) 100%)",
              boxShadow: "0 0 8px rgba(184,150,62,0.3)",
              transformOrigin: "right center",
              animation: "ob-shooting-star 1.5s ease-out forwards",
            }}
          />
        </div>
      )}

      <div className="flex-1 flex flex-col items-center justify-center px-8 pt-16 relative z-[3] min-h-0 w-full max-w-[420px] mx-auto">
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
                "linear-gradient(135deg, var(--eb-gold-wash) 0%, rgba(184, 150, 62, 0.05) 100%)",
              border: "1px solid var(--eb-gold-line)",
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
            <h1 className="upload-title text-center mb-3 px-2 eb-headline-preline">
              {slides[currentSlide].title}
            </h1>
            <p className="gold-subtitle mb-4 text-center">{slides[currentSlide].subtitle}</p>
            <p className="memorial-body text-center max-w-[17rem] mx-auto px-2">
              {slides[currentSlide].description}
            </p>
          </motion.div>
        </AnimatePresence>
      </div>

      <div className="flex justify-center gap-0 mb-2 relative z-[3] shrink-0">
        {slides.map((_, index) => (
          <button
            key={index}
            type="button"
            onClick={() => setCurrentSlide(index)}
            className={`eb-page-dot ${index === currentSlide ? "eb-page-dot--active" : ""}`}
            aria-label={`Slide ${index + 1}`}
            aria-current={index === currentSlide ? "step" : undefined}
          />
        ))}
      </div>

      <div className="eb-screen-footer relative z-[3] shrink-0">
        <div className="w-full max-w-[420px] mx-auto space-y-3">
          {onTryForest ? (
            <motion.button
              type="button"
              onClick={onTryForest}
              className="eb-btn eb-btn--secondary mem-btn-secondary w-full"
              whileTap={{ scale: 0.98 }}
            >
              {ob.tryForest}
            </motion.button>
          ) : null}
          <motion.button
            type="button"
            onClick={handleNext}
            className="eb-btn eb-btn--primary cta-gold w-full flex items-center justify-center gap-2"
            whileTap={{ scale: 0.98 }}
          >
            <span className="memorial-btn-label">
              {currentSlide === slides.length - 1 ? ob.getStarted : ob.continue}
            </span>
            <ChevronRight className="w-5 h-5" aria-hidden />
          </motion.button>
        </div>
      </div>
    </div>
  );
}
