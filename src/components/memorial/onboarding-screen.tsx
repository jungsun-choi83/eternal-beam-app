"use client";

import { useState, useMemo, useEffect } from "react";
import { motion, AnimatePresence } from "framer-motion";
import { ChevronRight } from "lucide-react";

interface OnboardingScreenProps {
  onComplete: () => void;
}

/** 1. 떠다니는 골드 파티클: createParticles 함수 - 20개, 1-3px, 6-8초, opacity 0→0.6→0 */
const PARTICLE_COUNT = 20;
function createParticles() {
  return Array.from({ length: PARTICLE_COUNT }, (_, i) => ({
    id: i,
    left: Math.random() * 100,
    size: 1 + Math.random() * 2,
    duration: 6 + Math.random() * 2,
    delay: Math.random() * 4,
  }));
}

const slides = [
  {
    title: "Welcome to Eternal Beam",
    subtitle: "Preserve precious memories in light",
    description: "Create stunning holographic displays of your beloved companions",
  },
  {
    title: "Upload Your Memories",
    subtitle: "Photos & Videos",
    description: "Our AI will transform your media into beautiful holographic content",
  },
  {
    title: "Choose Your Environment",
    subtitle: "Premium Themes Available",
    description: "Select from a variety of ambient backgrounds to complement your memories",
  },
  {
    title: "Connect Your Device",
    subtitle: "One-tap NFC Transfer",
    description: "Easily send your creations to your Eternal Beam holographic display",
  },
];

export function OnboardingScreen({ onComplete }: OnboardingScreenProps) {
  const [currentSlide, setCurrentSlide] = useState(0);
  const [shootingStar, setShootingStar] = useState(false);
  const [reducedMotion, setReducedMotion] = useState(false);

  const particles = useMemo(() => createParticles(), []);

  useEffect(() => {
    const m = window.matchMedia("(prefers-reduced-motion: reduce)");
    setReducedMotion(m.matches);
    const on = () => setReducedMotion(m.matches);
    m.addEventListener("change", on);
    return () => m.removeEventListener("change", on);
  }, []);

  /* 3. 별똥별: 첫 표시 2초 후, 이후 7-10초마다 랜덤, 1.5초 애니메이션 */
  useEffect(() => {
    if (reducedMotion) return;
    const t = setTimeout(() => setShootingStar(true), 2000);
    return () => clearTimeout(t);
  }, [reducedMotion]);

  useEffect(() => {
    if (!shootingStar) return;
    const hide = setTimeout(() => setShootingStar(false), 1600);
    return () => clearTimeout(hide);
  }, [shootingStar]);

  useEffect(() => {
    if (reducedMotion || shootingStar) return;
    const delay = 7000 + Math.random() * 3000;
    const t = setTimeout(() => setShootingStar(true), delay);
    return () => clearTimeout(t);
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

  return (
    <div
      data-screen="onboarding"
      className="flex flex-col relative overflow-hidden w-full"
      style={{
        height: "100%",
        minHeight: "100%",
        position: "relative",
        background: "#0a0a0a",
      }}
    >
      {/* 키프레임 인라인 주입 */}
      <style>{`
        @keyframes ob-particle-float {
          0% { transform: translateY(0); opacity: 0; }
          8% { opacity: 0.7; }
          92% { opacity: 0.7; }
          100% { transform: translateY(-100vh); opacity: 0; }
        }
        @keyframes ob-pulse-ring {
          0% { transform: scale(1); opacity: 0.7; }
          100% { transform: scale(2.5); opacity: 0; }
        }
        /* '1' 원 주변만: 링이 작게 퍼져서 텍스트 영역까지 안 내려감 */
        @keyframes ob-pulse-ring-circle {
          0% { transform: scale(1); opacity: 0.65; }
          100% { transform: scale(1.7); opacity: 0; }
        }
        @keyframes ob-shooting-star {
          0% { transform: translate(0, 0); opacity: 0; }
          5% { opacity: 1; }
          95% { opacity: 1; }
          100% { transform: translate(-120vw, 70vh); opacity: 0; }
        }
        /* 동작 줄이기 설정이 있어도 파티클/링은 매우 은은하게만 표시 */
        @media (prefers-reduced-motion: reduce) {
          .ob-particle { animation-duration: 20s !important; opacity: 0.35 !important; }
          .ob-pulse-ring { animation-duration: 6s !important; opacity: 0.25 !important; }
          .ob-shooting-star { animation: none !important; opacity: 0 !important; }
        }
        @keyframes ob-aurora {
          0%, 100% { opacity: 0.8; transform: scale(1); }
          50% { opacity: 1; transform: scale(1.08); }
        }
        .ob-aurora-layer { animation: ob-aurora 20s ease-in-out infinite; }
      `}</style>

      {/* 4. 오로라 배경 - 더 잘 보이도록 opacity 상향 */}
      <div
        className="absolute top-0 left-0 right-0 bottom-0 pointer-events-none overflow-hidden"
        style={{ zIndex: 0, width: "100%", height: "100%", minHeight: "100%" }}
      >
        <div
          className="ob-aurora-layer absolute"
          style={{
            inset: "-33%",
            background:
              "radial-gradient(ellipse 80% 50% at 50% 50%, rgba(212, 175, 55, 0.18) 0%, transparent 60%)",
          }}
        />
        <div
          className="ob-aurora-layer absolute"
          style={{
            inset: "-33%",
            background:
              "radial-gradient(ellipse 60% 70% at 70% 30%, rgba(244, 208, 63, 0.12) 0%, transparent 55%)",
          }}
        />
      </div>

      {/* 기존 배경 오브 - 위쪽만 유지, 아래쪽 오브 제거해 텍스트 영역 정리 */}
      <div className="absolute inset-0 pointer-events-none z-0">
        <motion.div
          className="absolute top-20 left-10 w-40 h-40 rounded-full"
          style={{
            background: "radial-gradient(circle, rgba(212, 175, 55, 0.2) 0%, transparent 70%)",
            filter: "blur(40px)",
          }}
          animate={{ scale: [1, 1.3, 1], x: [0, 20, 0] }}
          transition={{ duration: 6, repeat: Infinity, ease: "easeInOut" }}
        />
      </div>

      {/* 효과 오버레이: 콘텐츠 위에 그리기, 레이어 확실히 보이도록 */}
      <div
        role="presentation"
        className="absolute pointer-events-none overflow-visible"
        style={{
          top: 0,
          left: 0,
          width: "100%",
          height: "100%",
          bottom: 0,
          right: 0,
          zIndex: 10,
          minHeight: "100%",
          isolation: "isolate",
        }}
      >
        {/* 1. 파티클 - 화면에 확실히 보이도록 */}
        <div className="absolute" style={{ top: 0, left: 0, right: 0, bottom: 0, overflow: "hidden" }}>
          {particles.map((p) => {
            const size = Math.max(4, Math.min(8, p.size + 3));
            return (
              <div
                key={p.id}
                className="ob-particle absolute rounded-full"
                style={{
                  left: `${p.left}%`,
                  bottom: 0,
                  width: `${size}px`,
                  height: `${size}px`,
                  background: "#D4AF37",
                  boxShadow: "0 0 10px rgba(212,175,55,0.9)",
                  animation: `ob-particle-float ${p.duration}s linear ${p.delay}s infinite`,
                  opacity: 0.8,
                }}
              />
            );
          })}
        </div>
        {/* 2. 펄스 링 - 효과 오버레이에서는 제거하고, '1' 원과 같은 wrapper 안에서 렌더 */}
        {/* 3. 별똥별 */}
        {shootingStar && (
          <div className="absolute overflow-hidden" style={{ inset: 0 }}>
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
      </div>

      {/* Skip Button - 클릭 가능하도록 최상단 */}
      <div className="absolute top-6 right-6 z-20">
        <button
          onClick={handleSkip}
          className="text-sm text-[#A1A1A6] hover:text-[#F5F5F7] transition-colors"
        >
          Skip
        </button>
      </div>

      {/* Content */}
      <div className="flex-1 flex flex-col items-center justify-center px-8 relative z-[2]">
        <AnimatePresence mode="wait">
          <motion.div
            key={currentSlide}
            initial={{ opacity: 0, y: 20 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: -20 }}
            transition={{ duration: 0.4 }}
            className="text-center relative z-10"
          >
            {/* Icon/Visual - '1' 원을 중앙에 두고, 펄스 링이 원과 동심으로 퍼지게 */}
            <div
              className="relative flex items-center justify-center mx-auto mb-10"
              style={{ width: 240, height: 240 }}
            >
              {/* 펄스 링 - '1' 원과 완전 동일한 중심에서 동심원으로 퍼짐 */}
              <div
                className="absolute inset-0 flex items-center justify-center pointer-events-none"
                style={{ width: "100%", height: "100%" }}
              >
                {[0, 1, 2].map((i) => (
                  <div
                    key={i}
                    className="ob-pulse-ring absolute rounded-full"
                    style={{
                      width: 128,
                      height: 128,
                      border: "2px solid rgba(212,175,55,0.8)",
                      animation: `ob-pulse-ring-circle 3s ease-out ${i * 1}s infinite`,
                      transformOrigin: "center center",
                    }}
                  />
                ))}
              </div>
              {/* '1' 원 주변 글로우 */}
              <div
                className="absolute rounded-full pointer-events-none"
                style={{
                  width: 200,
                  height: 200,
                  background: "radial-gradient(circle, rgba(212, 175, 55, 0.15) 0%, transparent 65%)",
                  filter: "blur(20px)",
                }}
              />
              {/* '1' 원 */}
              <motion.div
                className="w-32 h-32 rounded-full relative z-10"
                style={{
                  background:
                    "linear-gradient(135deg, rgba(212, 175, 55, 0.1) 0%, rgba(201, 162, 39, 0.05) 100%)",
                  border: "1px solid rgba(212, 175, 55, 0.2)",
                }}
              >
                <motion.div
                  className="absolute inset-4 rounded-full"
                  style={{
                    background: "linear-gradient(135deg, #d4af37 0%, #c9a227 100%)",
                    opacity: 0.2,
                  }}
                  animate={{
                    scale: [0.8, 1, 0.8],
                    opacity: [0.15, 0.25, 0.15],
                  }}
                  transition={{ duration: 3, repeat: Infinity, ease: "easeInOut" }}
                />
                <div className="absolute inset-0 flex items-center justify-center">
                  <span className="text-4xl font-extralight" style={{ color: "#d4af37" }}>
                    {currentSlide + 1}
                  </span>
                </div>
              </motion.div>
            </div>

            <h1 className="text-2xl font-light mb-3" style={{ color: "#F5F5F7" }}>
              {slides[currentSlide].title}
            </h1>
            <p className="text-sm font-medium mb-4 tracking-wide" style={{ color: "#d4af37" }}>
              {slides[currentSlide].subtitle}
            </p>
            <p className="text-sm leading-relaxed max-w-xs mx-auto" style={{ color: "#A1A1A6" }}>
              {slides[currentSlide].description}
            </p>
          </motion.div>
        </AnimatePresence>
      </div>

      {/* Progress Dots */}
      <div className="flex justify-center gap-2 mb-8 relative z-[2]">
        {slides.map((_, index) => (
          <button
            key={index}
            onClick={() => setCurrentSlide(index)}
            className="w-2 h-2 rounded-full transition-all duration-300"
            style={{
              background: index === currentSlide ? "#d4af37" : "rgba(161, 161, 166, 0.3)",
              width: index === currentSlide ? "24px" : "8px",
            }}
          />
        ))}
      </div>

      {/* Next Button */}
      <div className="px-8 pb-10 relative z-[2]">
        <motion.button
          onClick={handleNext}
          className="w-full py-4 rounded-2xl flex items-center justify-center gap-2 transition-all duration-300"
          style={{
            background: "linear-gradient(135deg, #d4af37 0%, #c9a227 100%)",
            boxShadow: "0 8px 32px rgba(212, 175, 55, 0.3)",
          }}
          whileHover={{ scale: 1.02 }}
          whileTap={{ scale: 0.98 }}
        >
          <span className="text-[#0a0a0a] font-medium">
            {currentSlide === slides.length - 1 ? "Get Started" : "Continue"}
          </span>
          <ChevronRight className="w-5 h-5 text-[#0a0a0a]" />
        </motion.button>
      </div>
    </div>
  );
}
