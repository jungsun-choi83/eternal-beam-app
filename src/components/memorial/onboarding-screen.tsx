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

  const ringSizes = [168, 140, 112] as const;

  return (
    <div
      data-screen="onboarding"
      className="flex flex-col relative overflow-hidden w-full h-full min-h-full"
    >
      <style>{`
        @keyframes ob-shooting-star {
          0% { transform: translate(0, 0); opacity: 0; }
          5% { opacity: 1; }
          95% { opacity: 1; }
          100% { transform: translate(-120vw, 70vh); opacity: 0; }
        }
        @media (prefers-reduced-motion: reduce) {
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

      <div className="flex-1 flex flex-col items-center justify-center px-8 relative z-[3]">
        <AnimatePresence mode="wait">
          <motion.div
            key={currentSlide}
            initial={{ opacity: 0, y: 20 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: -20 }}
            transition={{ duration: 0.4 }}
            className="text-center relative z-10"
          >
            <div
              className="relative flex items-center justify-center mx-auto mb-10 ob-onboarding-glow rounded-full"
              style={{ width: 240, height: 240 }}
            >
              <div
                className="absolute inset-0 flex items-center justify-center pointer-events-none"
                aria-hidden
              >
                {ringSizes.map((size, i) => (
                  <div
                    key={size}
                    className={`ob-ring-pulse absolute rounded-full border border-[rgba(212,175,55,0.45)] ${
                      i === 1 ? "ob-ring-pulse--d1" : i === 2 ? "ob-ring-pulse--d2" : ""
                    }`}
                    style={{ width: size, height: size }}
                  />
                ))}
              </div>

              <motion.div
                className="w-32 h-32 rounded-full relative z-10"
                style={{
                  background:
                    "linear-gradient(135deg, rgba(212, 175, 55, 0.1) 0%, rgba(201, 162, 39, 0.05) 100%)",
                  border: "1px solid rgba(212, 175, 55, 0.2)",
                }}
              >
                <div className="absolute inset-0 flex items-center justify-center">
                  <span className="ob-center-num">{currentSlide + 1}</span>
                </div>
              </motion.div>
            </div>

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
