"use client";

import { useState } from "react";
import { motion, AnimatePresence } from "framer-motion";
import { Mail, Lock, User, Eye, EyeOff, ArrowRight } from "lucide-react";
import { HolographicBackground } from "./holographic-background";
import { HologramEffects } from "./hologram-effects";

interface AuthScreenProps {
  initialMode?: "login" | "signup";
  onAuthComplete: (userName?: string) => void;
}

export function AuthScreen({ initialMode = "login", onAuthComplete }: AuthScreenProps) {
  const [mode, setMode] = useState<"login" | "signup">(initialMode);
  const [showPassword, setShowPassword] = useState(false);
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [name, setName] = useState("");
  const [isLoading, setIsLoading] = useState(false);
  const [focusedField, setFocusedField] = useState<string | null>(null);

  const handleSubmit = async () => {
    setIsLoading(true);
    await new Promise((resolve) => setTimeout(resolve, 1500));
    setIsLoading(false);
    onAuthComplete(name || email.split("@")[0]);
  };

  // Stagger animation for form elements
  const containerVariants = {
    hidden: { opacity: 0 },
    visible: {
      opacity: 1,
      transition: {
        staggerChildren: 0.1,
        delayChildren: 0.2,
      },
    },
  };

  const itemVariants = {
    hidden: { opacity: 0, y: 20 },
    visible: {
      opacity: 1,
      y: 0,
      transition: { duration: 0.5, ease: [0.25, 0.46, 0.45, 0.94] },
    },
  };

  return (
    <div className="hologram-bg-active h-full flex flex-col bg-[#000000] relative overflow-hidden min-h-0">
      {/* Holographic Background */}
      <HolographicBackground />
      <HologramEffects />

      {/* Header with Logo - Fade in up */}
      <motion.header 
        className="pt-8 pb-4 px-8 shrink-0"
        initial={{ opacity: 0, y: -30 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.7, ease: [0.25, 0.46, 0.45, 0.94] }}
      >
        <div className="text-center relative overflow-hidden">
          {/* Enhanced Gold Glow */}
          <motion.div
            className="absolute inset-0 blur-[60px]"
            style={{
              background: "radial-gradient(ellipse at center, #c9a227 0%, transparent 60%)",
            }}
            animate={{ opacity: [0.3, 0.5, 0.3] }}
            transition={{ duration: 3, repeat: Infinity, ease: "easeInOut" }}
          />
          <h1 className="logo-title logo-title--auth relative">
            Eternal Beam
          </h1>
          <p className="logo-subtitle">
            Holographic Memorial
          </p>
        </div>
      </motion.header>

      {/* Auth Form Container - Enhanced Glassmorphism */}
      <motion.div 
        className="flex-1 px-6 flex flex-col justify-center min-h-0 overflow-y-auto"
        variants={containerVariants}
        initial="hidden"
        animate="visible"
      >
        <motion.div variants={itemVariants} className="relative">
          {/* Double Glass Layer - Back layer */}
          <div
            className="absolute -inset-3 rounded-[28px]"
            style={{
              background: "linear-gradient(145deg, rgba(255, 255, 255, 0.06) 0%, rgba(255, 255, 255, 0.01) 100%)",
              backdropFilter: "blur(25px)",
              WebkitBackdropFilter: "blur(25px)",
              border: "1px solid rgba(255, 255, 255, 0.06)",
            }}
          />
          
          {/* Glass Card - Front layer - Strong Glassmorphism */}
          <div
            className="rounded-3xl p-8 relative overflow-hidden"
            style={{
              background: "linear-gradient(145deg, rgba(60, 60, 65, 0.65) 0%, rgba(45, 45, 50, 0.7) 30%, rgba(28, 28, 30, 0.85) 100%)",
              backdropFilter: "blur(60px)",
              WebkitBackdropFilter: "blur(60px)",
              border: "1px solid rgba(255, 255, 255, 0.12)",
              boxShadow: `
                0 8px 32px rgba(0, 0, 0, 0.5),
                0 0 0 1px rgba(255, 255, 255, 0.08) inset,
                0 40px 80px -16px rgba(0, 0, 0, 0.6),
                inset 0 1px 0 rgba(255, 255, 255, 0.1)
              `,
            }}
          >
            {/* Top Glass Highlight - Bright edge for depth */}
            <div
              className="absolute top-0 left-6 right-6 h-px"
              style={{
                background: "linear-gradient(90deg, transparent 0%, rgba(255,255,255,0.35) 20%, rgba(255,255,255,0.5) 50%, rgba(255,255,255,0.35) 80%, transparent 100%)",
              }}
            />
            {/* Left Glass Highlight */}
            <div
              className="absolute top-6 bottom-6 left-0 w-px"
              style={{
                background: "linear-gradient(180deg, rgba(255,255,255,0.35), rgba(255,255,255,0.15) 50%, transparent)",
              }}
            />
            {/* Inner glow - top left corner */}
            <div
              className="absolute inset-0 rounded-3xl pointer-events-none"
              style={{
                background: "radial-gradient(ellipse at 20% 10%, rgba(255,255,255,0.1) 0%, transparent 40%)",
              }}
            />

            {/* Mode Toggle */}
            <motion.div
              variants={itemVariants}
              className="flex rounded-2xl p-1.5 mb-8"
              style={{
                background: "rgba(0, 0, 0, 0.4)",
                border: "1px solid rgba(255, 255, 255, 0.06)",
                boxShadow: "inset 0 2px 4px rgba(0,0,0,0.3)",
              }}
            >
              {["login", "signup"].map((m) => (
                <button
                  key={m}
                  onClick={() => setMode(m as "login" | "signup")}
                  className="flex-1 py-3.5 rounded-xl text-sm font-semibold tracking-wide transition-all duration-300 relative"
                  style={{
                    color: mode === m ? "#F5F5F7" : "#6B6B6B",
                  }}
                >
                  {mode === m && (
                    <motion.div
                      layoutId="activeTab"
                      className="absolute inset-0 rounded-xl"
                      style={{
                        background: "linear-gradient(135deg, rgba(201, 162, 39, 0.2) 0%, rgba(184, 134, 11, 0.15) 100%)",
                        border: "1px solid rgba(201, 162, 39, 0.3)",
                        boxShadow: "0 4px 12px rgba(201, 162, 39, 0.15)",
                      }}
                      transition={{ type: "spring", bounce: 0.15, duration: 0.5 }}
                    />
                  )}
                  <span className="relative z-10">{m === "login" ? "Sign In" : "Sign Up"}</span>
                </button>
              ))}
            </motion.div>

            {/* Form Fields with Stagger Animation */}
            <AnimatePresence mode="wait">
              <motion.div
                key={mode}
                initial={{ opacity: 0, x: mode === "signup" ? 30 : -30 }}
                animate={{ opacity: 1, x: 0 }}
                exit={{ opacity: 0, x: mode === "signup" ? -30 : 30 }}
                transition={{ duration: 0.35, ease: [0.25, 0.46, 0.45, 0.94] }}
                className="space-y-4"
              >
                {mode === "signup" && (
                  <motion.div 
                    className="relative"
                    initial={{ opacity: 0, y: 20 }}
                    animate={{ opacity: 1, y: 0 }}
                    transition={{ delay: 0.1 }}
                  >
                    <User
                      className="absolute left-4 top-1/2 -translate-y-1/2 w-5 h-5 transition-colors duration-300"
                      style={{ color: focusedField === "name" ? "#c9a227" : "#6B6B6B" }}
                      strokeWidth={1.5}
                    />
                    <input
                      type="text"
                      placeholder="Full Name"
                      value={name}
                      onChange={(e) => setName(e.target.value)}
                      onFocus={() => setFocusedField("name")}
                      onBlur={() => setFocusedField(null)}
                      className="w-full py-4 pl-12 pr-4 rounded-xl text-sm font-medium outline-none transition-all duration-300 placeholder:text-[#4A4A4A]"
                      style={{
                        background: "rgba(0, 0, 0, 0.4)",
                        border: focusedField === "name" 
                          ? "1px solid rgba(201, 162, 39, 0.5)" 
                          : "1px solid rgba(255, 255, 255, 0.08)",
                        color: "#F5F5F7",
                        boxShadow: focusedField === "name" 
                          ? "0 0 20px rgba(201, 162, 39, 0.2), inset 0 0 20px rgba(201, 162, 39, 0.05)" 
                          : "none",
                      }}
                    />
                  </motion.div>
                )}

                <motion.div 
                  className="relative"
                  initial={{ opacity: 0, y: 20 }}
                  animate={{ opacity: 1, y: 0 }}
                  transition={{ delay: mode === "signup" ? 0.2 : 0.1 }}
                >
                  <Mail
                    className="absolute left-4 top-1/2 -translate-y-1/2 w-5 h-5 transition-colors duration-300"
                    style={{ color: focusedField === "email" ? "#c9a227" : "#6B6B6B" }}
                    strokeWidth={1.5}
                  />
                  <input
                    type="email"
                    placeholder="Email"
                    value={email}
                    onChange={(e) => setEmail(e.target.value)}
                    onFocus={() => setFocusedField("email")}
                    onBlur={() => setFocusedField(null)}
                    className="w-full py-4 pl-12 pr-4 rounded-xl text-sm font-medium outline-none transition-all duration-300 placeholder:text-[#4A4A4A]"
                    style={{
                      background: "rgba(0, 0, 0, 0.4)",
                      border: focusedField === "email" 
                        ? "1px solid rgba(201, 162, 39, 0.5)" 
                        : "1px solid rgba(255, 255, 255, 0.08)",
                      color: "#F5F5F7",
                      boxShadow: focusedField === "email" 
                        ? "0 0 20px rgba(201, 162, 39, 0.2), inset 0 0 20px rgba(201, 162, 39, 0.05)" 
                        : "none",
                    }}
                  />
                </motion.div>

                <motion.div 
                  className="relative"
                  initial={{ opacity: 0, y: 20 }}
                  animate={{ opacity: 1, y: 0 }}
                  transition={{ delay: mode === "signup" ? 0.3 : 0.2 }}
                >
                  <Lock
                    className="absolute left-4 top-1/2 -translate-y-1/2 w-5 h-5 transition-colors duration-300"
                    style={{ color: focusedField === "password" ? "#c9a227" : "#6B6B6B" }}
                    strokeWidth={1.5}
                  />
                  <input
                    type={showPassword ? "text" : "password"}
                    placeholder="Password"
                    value={password}
                    onChange={(e) => setPassword(e.target.value)}
                    onFocus={() => setFocusedField("password")}
                    onBlur={() => setFocusedField(null)}
                    className="w-full py-4 pl-12 pr-12 rounded-xl text-sm font-medium outline-none transition-all duration-300 placeholder:text-[#4A4A4A]"
                    style={{
                      background: "rgba(0, 0, 0, 0.4)",
                      border: focusedField === "password" 
                        ? "1px solid rgba(201, 162, 39, 0.5)" 
                        : "1px solid rgba(255, 255, 255, 0.08)",
                      color: "#F5F5F7",
                      boxShadow: focusedField === "password" 
                        ? "0 0 20px rgba(201, 162, 39, 0.2), inset 0 0 20px rgba(201, 162, 39, 0.05)" 
                        : "none",
                    }}
                  />
                  <button
                    type="button"
                    onClick={() => setShowPassword(!showPassword)}
                    className="absolute right-4 top-1/2 -translate-y-1/2 transition-colors duration-300"
                  >
                    {showPassword ? (
                      <EyeOff className="w-5 h-5" style={{ color: "#6B6B6B" }} strokeWidth={1.5} />
                    ) : (
                      <Eye className="w-5 h-5" style={{ color: "#6B6B6B" }} strokeWidth={1.5} />
                    )}
                  </button>
                </motion.div>
              </motion.div>
            </AnimatePresence>

            {/* Forgot Password */}
            {mode === "login" && (
              <motion.button
                initial={{ opacity: 0 }}
                animate={{ opacity: 1 }}
                transition={{ delay: 0.4 }}
                className="text-xs mt-4 font-medium tracking-wide transition-colors duration-300 hover:text-[#f5d77a]"
                style={{ color: "#c9a227" }}
              >
                Forgot password?
              </motion.button>
            )}

          </div>
        </motion.div>
      </motion.div>

      {/* Next 버튼 - 하단 고정 (항상 보임) */}
      <motion.div 
        className="px-8 pt-4 pb-6 shrink-0"
        initial={{ opacity: 0, y: 20 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ delay: 0.5, duration: 0.4 }}
      >
        <motion.button
          onClick={handleSubmit}
          disabled={isLoading}
          className="w-full py-4 rounded-2xl font-bold text-base tracking-wide flex items-center justify-center gap-2 relative overflow-hidden"
          style={{
            background: "linear-gradient(135deg, #b8860b 0%, #c9a227 30%, #d4af37 50%, #f5d77a 70%, #d4af37 100%)",
            boxShadow: "0 8px 32px rgba(201, 162, 39, 0.3), 0 4px 12px rgba(201, 162, 39, 0.2)",
          }}
          whileHover={{ scale: 1.02, boxShadow: "0 12px 40px rgba(201, 162, 39, 0.4)" }}
          whileTap={{ scale: 0.95 }}
        >
          {isLoading ? (
            <motion.div
              className="w-5 h-5 border-2 border-[#0a0a0a]/30 border-t-[#0a0a0a] rounded-full"
              animate={{ rotate: 360 }}
              transition={{ duration: 0.8, repeat: Infinity, ease: "linear" }}
            />
          ) : (
            <>
              <span className="text-[#0a0a0a] font-bold">{mode === "login" ? "Sign In" : "Create Account"}</span>
              <ArrowRight className="w-5 h-5 text-[#0a0a0a]" strokeWidth={2.5} />
            </>
          )}
        </motion.button>
      </motion.div>

      {/* Terms - 하단 고정 */}
      <motion.div 
        className="px-8 pb-8 shrink-0"
        initial={{ opacity: 0 }}
        animate={{ opacity: 1 }}
        transition={{ delay: 0.7, duration: 0.5 }}
      >
        <p className="text-center text-[11px] font-medium leading-relaxed" style={{ color: "#6B6B6B" }}>
          By continuing, you agree to our{" "}
          <span className="transition-colors duration-300 hover:text-[#f5d77a]" style={{ color: "#c9a227" }}>Terms of Service</span> and{" "}
          <span className="transition-colors duration-300 hover:text-[#f5d77a]" style={{ color: "#c9a227" }}>Privacy Policy</span>
        </p>
      </motion.div>
    </div>
  );
}
