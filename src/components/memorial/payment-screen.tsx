"use client";

import { useState } from "react";
import { motion } from "framer-motion";
import { ArrowLeft, Check, Sparkles, X } from "lucide-react";

interface PaymentScreenProps {
  selectedTheme: {
    id: number;
    name: string;
    price: string;
  };
  onComplete: () => void;
  onSkip: () => void;
  onBack: () => void;
}

export function PaymentScreen({ 
  selectedTheme, 
  onComplete, 
  onSkip, 
  onBack 
}: PaymentScreenProps) {
  const [isProcessing, setIsProcessing] = useState(false);
  const [paymentMethod, setPaymentMethod] = useState<"card" | "apple" | null>(null);

  const handlePayment = async () => {
    if (!paymentMethod) return;
    setIsProcessing(true);
    // Simulate payment processing
    await new Promise(resolve => setTimeout(resolve, 2000));
    setIsProcessing(false);
    onComplete();
  };

  return (
    <div className="h-full flex flex-col bg-[#0a0a0a]">
      {/* Header */}
      <header className="px-6 pt-8 pb-4 flex items-center justify-between relative shrink-0">
        <motion.button
          initial={{ opacity: 0, x: -10 }}
          animate={{ opacity: 1, x: 0 }}
          onClick={onBack}
          className="w-10 h-10 rounded-full flex items-center justify-center"
          style={{
            background: "#1C1C1E",
            border: "1px solid #333333",
          }}
          whileHover={{ scale: 1.05 }}
          whileTap={{ scale: 0.95 }}
        >
          <ArrowLeft className="w-4 h-4" style={{ color: "#F5F5F7" }} strokeWidth={1.5} />
        </motion.button>

        <motion.h1
          initial={{ opacity: 0, y: -10 }}
          animate={{ opacity: 1, y: 0 }}
          className="text-xl font-light absolute left-1/2 -translate-x-1/2 text-center"
          style={{ color: "#F5F5F7" }}
        >
          Premium Theme
        </motion.h1>

        <motion.button
          initial={{ opacity: 0, x: 10 }}
          animate={{ opacity: 1, x: 0 }}
          onClick={onSkip}
          className="w-10 h-10 rounded-full flex items-center justify-center"
          style={{
            background: "#1C1C1E",
            border: "1px solid #333333",
          }}
          whileHover={{ scale: 1.05 }}
          whileTap={{ scale: 0.95 }}
        >
          <X className="w-4 h-4" style={{ color: "#A1A1A6" }} strokeWidth={1.5} />
        </motion.button>
      </header>

      {/* Theme Preview */}
      <div className="px-8 py-8">
        <motion.div
          initial={{ opacity: 0, scale: 0.95 }}
          animate={{ opacity: 1, scale: 1 }}
          className="relative aspect-video rounded-3xl overflow-hidden"
          style={{
            background: "rgba(28, 28, 30, 0.8)",
            backdropFilter: "blur(40px)",
            border: "1px solid rgba(255, 255, 255, 0.1)",
          }}
        >
          {/* Theme Preview Content */}
          <div className="absolute inset-0 flex items-center justify-center">
            <div className="text-center">
              <motion.div
                className="w-16 h-16 mx-auto mb-4 rounded-full flex items-center justify-center"
                style={{
                  background: "linear-gradient(135deg, #c9a227, #f5d77a)",
                  boxShadow: "0 0 40px rgba(201, 162, 39, 0.4)",
                }}
                animate={{
                  boxShadow: [
                    "0 0 40px rgba(201, 162, 39, 0.4)",
                    "0 0 60px rgba(201, 162, 39, 0.6)",
                    "0 0 40px rgba(201, 162, 39, 0.4)",
                  ],
                }}
                transition={{ duration: 2, repeat: Infinity }}
              >
                <Sparkles className="w-8 h-8 text-[#0a0a0a]" />
              </motion.div>
              <h3 className="text-xl font-light mb-1" style={{ color: "#F5F5F7" }}>
                {selectedTheme.name}
              </h3>
              <p className="text-sm font-light" style={{ color: "#A1A1A6" }}>
                Premium Environment
              </p>
            </div>
          </div>

          {/* Decorative Elements */}
          <motion.div
            className="absolute top-4 left-4 w-20 h-20 rounded-full"
            style={{
              background: "radial-gradient(circle, rgba(201, 162, 39, 0.3), transparent)",
              filter: "blur(20px)",
            }}
            animate={{ scale: [1, 1.2, 1], opacity: [0.3, 0.5, 0.3] }}
            transition={{ duration: 3, repeat: Infinity }}
          />
          <motion.div
            className="absolute bottom-4 right-4 w-24 h-24 rounded-full"
            style={{
              background: "radial-gradient(circle, rgba(212, 175, 55, 0.2), transparent)",
              filter: "blur(25px)",
            }}
            animate={{ scale: [1.2, 1, 1.2], opacity: [0.2, 0.4, 0.2] }}
            transition={{ duration: 4, repeat: Infinity }}
          />
        </motion.div>
      </div>

      {/* Price */}
      <div className="px-8 py-4 text-center">
        <motion.div
          initial={{ opacity: 0, y: 10 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ delay: 0.2 }}
        >
          <span className="text-4xl font-light" style={{ color: "#F5F5F7" }}>
            {selectedTheme.price}
          </span>
          <p className="text-sm font-light mt-2" style={{ color: "#A1A1A6" }}>
            One-time purchase
          </p>
        </motion.div>
      </div>

      {/* Payment Methods */}
      <div className="flex-1 px-8 py-4">
        <p
          className="text-[11px] uppercase font-light mb-4"
          style={{ color: "#A1A1A6", letterSpacing: "0.2em" }}
        >
          Payment Method
        </p>

        <div className="space-y-3">
          {/* Card Payment */}
          <motion.button
            initial={{ opacity: 0, x: -20 }}
            animate={{ opacity: 1, x: 0 }}
            transition={{ delay: 0.3 }}
            onClick={() => setPaymentMethod("card")}
            className="w-full p-4 rounded-2xl flex items-center justify-between transition-all"
            style={{
              background: paymentMethod === "card" ? "rgba(201, 162, 39, 0.1)" : "#1C1C1E",
              border: paymentMethod === "card" ? "1px solid rgba(201, 162, 39, 0.4)" : "1px solid #333333",
            }}
          >
            <div className="flex items-center gap-4 flex-1 justify-center">
              <div
                className="w-10 h-10 rounded-xl flex items-center justify-center shrink-0"
                style={{ background: "#2C2C2E" }}
              >
                <span className="text-lg">💳</span>
              </div>
              <div className="text-center">
                <p className="text-sm font-light" style={{ color: "#F5F5F7" }}>
                  Credit / Debit Card
                </p>
                <p className="text-xs font-light" style={{ color: "#A1A1A6" }}>
                  Visa, Mastercard, AMEX
                </p>
              </div>
            </div>
            {paymentMethod === "card" && (
              <motion.div
                initial={{ scale: 0 }}
                animate={{ scale: 1 }}
                className="w-6 h-6 rounded-full flex items-center justify-center"
                style={{ background: "#c9a227" }}
              >
                <Check className="w-4 h-4 text-[#0a0a0a]" />
              </motion.div>
            )}
          </motion.button>

          {/* Apple Pay */}
          <motion.button
            initial={{ opacity: 0, x: -20 }}
            animate={{ opacity: 1, x: 0 }}
            transition={{ delay: 0.4 }}
            onClick={() => setPaymentMethod("apple")}
            className="w-full p-4 rounded-2xl flex items-center justify-between transition-all"
            style={{
              background: paymentMethod === "apple" ? "rgba(201, 162, 39, 0.1)" : "#1C1C1E",
              border: paymentMethod === "apple" ? "1px solid rgba(201, 162, 39, 0.4)" : "1px solid #333333",
            }}
          >
            <div className="flex items-center gap-4 flex-1 justify-center">
              <div
                className="w-10 h-10 rounded-xl flex items-center justify-center shrink-0"
                style={{ background: "#2C2C2E" }}
              >
                <span className="text-lg"></span>
              </div>
              <div className="text-center">
                <p className="text-sm font-light" style={{ color: "#F5F5F7" }}>
                  Apple Pay
                </p>
                <p className="text-xs font-light" style={{ color: "#A1A1A6" }}>
                  Fast & Secure
                </p>
              </div>
            </div>
            {paymentMethod === "apple" && (
              <motion.div
                initial={{ scale: 0 }}
                animate={{ scale: 1 }}
                className="w-6 h-6 rounded-full flex items-center justify-center"
                style={{ background: "#c9a227" }}
              >
                <Check className="w-4 h-4 text-[#0a0a0a]" />
              </motion.div>
            )}
          </motion.button>
        </div>
      </div>

      {/* Actions */}
      <div className="px-8 py-6 space-y-3">
        <motion.button
          initial={{ opacity: 0, y: 20 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ delay: 0.5 }}
          onClick={handlePayment}
          disabled={!paymentMethod || isProcessing}
          className="w-full py-4 rounded-2xl font-normal text-[15px] transition-all duration-300 flex items-center justify-center gap-2"
          style={{
            letterSpacing: "0.1em",
            background: paymentMethod
              ? "linear-gradient(135deg, #b8860b 0%, #c9a227 30%, #d4af37 50%, #f5d77a 70%, #d4af37 100%)"
              : "#1C1C1E",
            border: paymentMethod ? "none" : "1px solid #333333",
            color: paymentMethod ? "#0a0a0a" : "#A1A1A6",
            boxShadow: paymentMethod ? "0 10px 40px rgba(201, 162, 39, 0.25)" : "none",
            cursor: paymentMethod ? "pointer" : "not-allowed",
          }}
          whileHover={paymentMethod ? { scale: 1.02 } : {}}
          whileTap={paymentMethod ? { scale: 0.98 } : {}}
        >
          {isProcessing ? (
            <>
              <motion.div
                className="w-5 h-5 border-2 border-[#0a0a0a] border-t-transparent rounded-full"
                animate={{ rotate: 360 }}
                transition={{ duration: 1, repeat: Infinity, ease: "linear" }}
              />
              <span>Processing...</span>
            </>
          ) : (
            <span>Purchase {selectedTheme.price}</span>
          )}
        </motion.button>

        <motion.button
          initial={{ opacity: 0, y: 20 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ delay: 0.6 }}
          onClick={onSkip}
          className="w-full py-3 text-center"
        >
          <span
            className="text-sm font-light"
            style={{ color: "#A1A1A6", letterSpacing: "0.05em" }}
          >
            Skip for now
          </span>
        </motion.button>
      </div>
    </div>
  );
}
