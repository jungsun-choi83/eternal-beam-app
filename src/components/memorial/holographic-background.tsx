"use client";

import { useEffect, useState } from "react";
import { motion } from "framer-motion";

interface Particle {
  id: number;
  x: number;
  y: number;
  size: number;
  opacity: number;
  duration: number;
  delay: number;
}

export function HolographicBackground() {
  const [particles, setParticles] = useState<Particle[]>([]);

  useEffect(() => {
    // Generate random floating particles
    const generateParticles = () => {
      const newParticles: Particle[] = [];
      for (let i = 0; i < 30; i++) {
        newParticles.push({
          id: i,
          x: Math.random() * 100,
          y: Math.random() * 100,
          size: 1 + Math.random() * 2, // 1-3px
          opacity: 0.2 + Math.random() * 0.3, // 0.2-0.5
          duration: 3 + Math.random() * 3, // 3-6 seconds
          delay: Math.random() * 4,
        });
      }
      setParticles(newParticles);
    };

    generateParticles();
  }, []);

  return (
    <div className="absolute inset-0 overflow-hidden pointer-events-none">
      {/* Layer 1: Base Black */}
      <div className="absolute inset-0 bg-[#000000]" />

      {/* Layer 2: Animated Gold Radial Gradient */}
      <motion.div
        className="absolute inset-0"
        style={{
          background: `radial-gradient(
            circle at 30% 50%,
            rgba(212, 175, 55, 0.1) 0%,
            transparent 50%
          )`,
        }}
        animate={{
          background: [
            `radial-gradient(circle at 30% 50%, rgba(212, 175, 55, 0.1) 0%, transparent 50%)`,
            `radial-gradient(circle at 50% 30%, rgba(212, 175, 55, 0.12) 0%, transparent 50%)`,
            `radial-gradient(circle at 70% 60%, rgba(212, 175, 55, 0.08) 0%, transparent 50%)`,
            `radial-gradient(circle at 40% 70%, rgba(212, 175, 55, 0.1) 0%, transparent 50%)`,
            `radial-gradient(circle at 30% 50%, rgba(212, 175, 55, 0.1) 0%, transparent 50%)`,
          ],
        }}
        transition={{
          duration: 8,
          repeat: Infinity,
          ease: "easeInOut",
        }}
      />

      {/* Secondary moving light */}
      <motion.div
        className="absolute inset-0"
        style={{
          background: `radial-gradient(
            ellipse at 70% 30%,
            rgba(184, 134, 11, 0.06) 0%,
            transparent 40%
          )`,
        }}
        animate={{
          background: [
            `radial-gradient(ellipse at 70% 30%, rgba(184, 134, 11, 0.06) 0%, transparent 40%)`,
            `radial-gradient(ellipse at 50% 50%, rgba(184, 134, 11, 0.08) 0%, transparent 40%)`,
            `radial-gradient(ellipse at 30% 70%, rgba(184, 134, 11, 0.05) 0%, transparent 40%)`,
            `radial-gradient(ellipse at 60% 40%, rgba(184, 134, 11, 0.07) 0%, transparent 40%)`,
            `radial-gradient(ellipse at 70% 30%, rgba(184, 134, 11, 0.06) 0%, transparent 40%)`,
          ],
        }}
        transition={{
          duration: 5,
          repeat: Infinity,
          ease: "easeInOut",
        }}
      />

      {/* Layer 3: Subtle Noise Pattern */}
      <div
        className="absolute inset-0 opacity-[0.03]"
        style={{
          backgroundImage: `url("data:image/svg+xml,%3Csvg viewBox='0 0 200 200' xmlns='http://www.w3.org/2000/svg'%3E%3Cfilter id='noiseFilter'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='0.9' numOctaves='4' stitchTiles='stitch'/%3E%3C/filter%3E%3Crect width='100%25' height='100%25' filter='url(%23noiseFilter)'/%3E%3C/svg%3E")`,
        }}
      />

      {/* Floating Gold Particles */}
      {particles.map((particle) => (
        <motion.div
          key={particle.id}
          className="absolute rounded-full"
          style={{
            left: `${particle.x}%`,
            bottom: `-${particle.size}px`,
            width: `${particle.size}px`,
            height: `${particle.size}px`,
            background: `rgba(212, 175, 55, ${particle.opacity})`,
            boxShadow: `0 0 ${particle.size * 2}px rgba(212, 175, 55, ${particle.opacity * 0.5})`,
          }}
          animate={{
            y: [0, -800],
            opacity: [0, particle.opacity, particle.opacity, 0],
          }}
          transition={{
            duration: particle.duration,
            repeat: Infinity,
            delay: particle.delay,
            ease: "linear",
          }}
        />
      ))}

      {/* Ambient Light Sweep */}
      <motion.div
        className="absolute inset-0"
        style={{
          background: "linear-gradient(135deg, transparent 0%, rgba(212, 175, 55, 0.03) 50%, transparent 100%)",
        }}
        animate={{
          opacity: [0, 1, 0],
        }}
        transition={{
          duration: 4,
          repeat: Infinity,
          ease: "easeInOut",
        }}
      />
    </div>
  );
}
