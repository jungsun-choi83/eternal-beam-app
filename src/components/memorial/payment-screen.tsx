"use client";

import { useEffect, useRef, useState } from "react";
import { motion } from "framer-motion";
import { ArrowLeft, Check, PawPrint } from "lucide-react";
import { EternalBeamLogoIcon } from "@/components/memorial/eternal-beam-brand-mark";
import { memorialT } from "@/components/memorial/memorial-i18n";
import { loadPaypalSdk, isPaypalConfigured, type PaypalButtonsInstance } from "@/lib/paypal-sdk";
import { createPaypalOrder, capturePaypalOrder } from "@/lib/paypal-api";

interface PaymentScreenProps {
  language?: string;
  selectedTheme: {
    id: number;
    themeKey: string;
    name: string;
    /** "" = 무료, "$2.99" 같은 형태 = 유료 */
    price: string;
    thumb?: string;
  };
  onComplete: () => void;
  onSkip: () => void;
  onBack: () => void;
}

type PaypalStatus = "loading" | "ready" | "error" | "processing" | "failed";

export function PaymentScreen({
  language = "ko",
  selectedTheme,
  onComplete,
  onSkip,
  onBack,
}: PaymentScreenProps) {
  const pay = memorialT(language).payment;
  const isFree = !selectedTheme.price;
  const [paypalStatus, setPaypalStatus] = useState<PaypalStatus>(isFree ? "ready" : "loading");
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const buttonsRef = useRef<PaypalButtonsInstance | null>(null);
  const containerRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (isFree) return;
    let cancelled = false;

    if (!isPaypalConfigured()) {
      setPaypalStatus("error");
      setErrorMessage(pay.paypalUnavailable);
      return;
    }

    loadPaypalSdk("USD")
      .then((paypal) => {
        if (cancelled || !containerRef.current) return;
        const buttons = paypal.Buttons({
          style: { layout: "vertical", color: "gold", shape: "pill", label: "paypal" },
          createOrder: async () => {
            setErrorMessage(null);
            try {
              const order = await createPaypalOrder(selectedTheme.themeKey);
              return order.order_id;
            } catch {
              setErrorMessage(pay.orderFailed);
              throw new Error(pay.orderFailed);
            }
          },
          onApprove: async (data) => {
            setPaypalStatus("processing");
            try {
              const result = await capturePaypalOrder(selectedTheme.themeKey, data.orderID);
              if (result.success) {
                onComplete();
              } else {
                setPaypalStatus("failed");
                setErrorMessage(pay.captureFailed);
              }
            } catch {
              setPaypalStatus("failed");
              setErrorMessage(pay.captureFailed);
            }
          },
          onCancel: () => {
            setPaypalStatus("ready");
          },
          onError: () => {
            setPaypalStatus("failed");
            setErrorMessage(pay.captureFailed);
          },
        });
        buttonsRef.current = buttons;
        return buttons.render(containerRef.current);
      })
      .then(() => {
        if (!cancelled) setPaypalStatus("ready");
      })
      .catch(() => {
        if (cancelled) return;
        setPaypalStatus("error");
        setErrorMessage(pay.paypalUnavailable);
      });

    return () => {
      cancelled = true;
      buttonsRef.current?.close?.();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isFree, selectedTheme.themeKey]);

  return (
    <div className="h-full flex flex-col relative overflow-hidden">
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
          {isFree ? pay.freeTitle : pay.title}
        </motion.h1>

        <div className="w-10 h-10" />
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
          {selectedTheme.thumb ? (
            <>
              <img
                src={selectedTheme.thumb}
                alt=""
                className="absolute inset-0 h-full w-full object-cover"
                draggable={false}
              />
              <div className="absolute inset-0 bg-gradient-to-t from-[#0a0a0a]/90 via-[#0a0a0a]/45 to-[#0a0a0a]/20" />
            </>
          ) : null}
          <div className="absolute top-4 right-4">
            <EternalBeamLogoIcon size={22} />
          </div>
          <div className="absolute inset-0 flex items-end justify-center pb-8 px-6">
            <div className="text-center">
              {!selectedTheme.thumb ? (
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
                  <PawPrint className="w-8 h-8 text-[#0a0a0a]" strokeWidth={1.75} />
                </motion.div>
              ) : null}
              <h3 className="text-xl font-light mb-1" style={{ color: "#F5F5F7" }}>
                {selectedTheme.name}
              </h3>
              <p className="text-sm font-light" style={{ color: "#A1A1A6" }}>
                {isFree ? pay.freeLabel : pay.premiumEnv}
              </p>
            </div>
          </div>

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
        <motion.div initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: 0.2 }}>
          <span className="text-4xl font-light" style={{ color: "#F5F5F7" }}>
            {isFree ? pay.freeLabel : selectedTheme.price}
          </span>
          <p className="text-sm font-light mt-2" style={{ color: "#A1A1A6" }}>
            {isFree ? pay.freeDesc : pay.oneTime}
          </p>
        </motion.div>
      </div>

      {/* Payment area */}
      <div className="flex-1 px-8 py-2 flex flex-col">
        {!isFree && (
          <>
            <p
              className="text-[11px] uppercase font-light mb-4"
              style={{ color: "#A1A1A6", letterSpacing: "0.2em" }}
            >
              {pay.method}
            </p>

            <div className="min-h-[52px]">
              {paypalStatus === "loading" && (
                <div
                  className="w-full py-4 rounded-2xl flex items-center justify-center gap-2"
                  style={{ background: "#1C1C1E", border: "1px solid #333333" }}
                >
                  <motion.div
                    className="w-4 h-4 border-2 border-[#A1A1A6] border-t-transparent rounded-full"
                    animate={{ rotate: 360 }}
                    transition={{ duration: 1, repeat: Infinity, ease: "linear" }}
                  />
                  <span className="text-sm font-light" style={{ color: "#A1A1A6" }}>
                    {pay.loadingPaypal}
                  </span>
                </div>
              )}

              {paypalStatus === "processing" && (
                <div
                  className="w-full py-4 rounded-2xl flex items-center justify-center gap-2"
                  style={{ background: "rgba(201, 162, 39, 0.1)", border: "1px solid rgba(201, 162, 39, 0.4)" }}
                >
                  <motion.div
                    className="w-4 h-4 border-2 border-[#c9a227] border-t-transparent rounded-full"
                    animate={{ rotate: 360 }}
                    transition={{ duration: 1, repeat: Infinity, ease: "linear" }}
                  />
                  <span className="text-sm font-light" style={{ color: "#F5F5F7" }}>
                    {pay.processing}
                  </span>
                </div>
              )}

              {/* PayPal SDK renders its own button(s) inside this div */}
              <div
                ref={containerRef}
                style={{ display: paypalStatus === "loading" || paypalStatus === "processing" ? "none" : "block" }}
              />

              {(paypalStatus === "error" || paypalStatus === "failed") && errorMessage && (
                <p className="text-xs font-light mt-3 text-center" style={{ color: "#f43f5e" }}>
                  {errorMessage}
                </p>
              )}
            </div>

            <p className="text-[11px] font-light mt-4 text-center" style={{ color: "#A1A1A6" }}>
              {pay.securedByPaypal}
            </p>
          </>
        )}
      </div>

      {/* Actions */}
      <div className="px-8 py-6 space-y-3">
        {isFree ? (
          <motion.button
            initial={{ opacity: 0, y: 20 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ delay: 0.3 }}
            onClick={onComplete}
            className="w-full py-4 rounded-2xl font-normal text-[15px] transition-all duration-300 flex items-center justify-center gap-2"
            style={{
              letterSpacing: "0.1em",
              background: "linear-gradient(135deg, #b8860b 0%, #c9a227 30%, #d4af37 50%, #f5d77a 70%, #d4af37 100%)",
              color: "#0a0a0a",
              boxShadow: "0 10px 40px rgba(201, 162, 39, 0.25)",
            }}
            whileHover={{ scale: 1.02 }}
            whileTap={{ scale: 0.98 }}
          >
            <Check className="w-4 h-4" />
            <span>{pay.continueFree}</span>
          </motion.button>
        ) : (
          (paypalStatus === "error" || paypalStatus === "failed") && (
            <motion.button
              initial={{ opacity: 0, y: 20 }}
              animate={{ opacity: 1, y: 0 }}
              onClick={onSkip}
              className="w-full py-3 text-center"
            >
              <span className="text-sm font-light" style={{ color: "#A1A1A6", letterSpacing: "0.05em" }}>
                {pay.skipForNow}
              </span>
            </motion.button>
          )
        )}
      </div>
    </div>
  );
}
