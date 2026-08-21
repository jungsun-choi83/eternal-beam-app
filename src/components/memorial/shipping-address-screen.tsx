"use client";

import { useState } from "react";
import { motion } from "framer-motion";
import { ArrowLeft, MapPin, Package } from "lucide-react";
import { memorialT } from "@/components/memorial/memorial-i18n";
import {
  saveShippingAddress,
  type ShippingAddress,
} from "@/lib/finalize-preview-content";

interface ShippingAddressScreenProps {
  language?: string;
  onComplete: () => void;
  onBack: () => void;
  /**
   * 입력된 주소를 호출부로 넘긴다 (선택).
   *
   * 실물 주문은 이 값을 **서버 주문**에 실어야 한다 — localStorage 만으로는
   * 인쇄·배송이 불가능하다. 넘기지 않는 기존 호출부(프리미엄 테마 배송 흐름)는
   * 예전 그대로 localStorage 저장 + onComplete 만 동작한다.
   */
  onSubmitAddress?: (address: ShippingAddress) => void;
  /** 제출 버튼 문구 재정의 (주문 흐름에서는 "주문 확인"). */
  submitLabel?: string;
  /** 초깃값 — 뒤로 갔다 오면 입력이 남아 있어야 한다. */
  initialAddress?: ShippingAddress | null;
}

export function ShippingAddressScreen({
  language = "ko",
  onComplete,
  onBack,
  onSubmitAddress,
  submitLabel,
  initialAddress,
}: ShippingAddressScreenProps) {
  const s = memorialT(language).shipping;
  const [form, setForm] = useState<ShippingAddress>(
    initialAddress ?? {
      recipientName: "",
      phone: "",
      postalCode: "",
      addressLine1: "",
      addressLine2: "",
    }
  );
  const [error, setError] = useState<string | null>(null);

  const update = (key: keyof ShippingAddress, value: string) => {
    setForm((prev) => ({ ...prev, [key]: value }));
    setError(null);
  };

  const handleSubmit = () => {
    if (!form.recipientName.trim()) {
      setError(s.errorName);
      return;
    }
    if (!form.phone.trim()) {
      setError(s.errorPhone);
      return;
    }
    if (!form.postalCode.trim() || !form.addressLine1.trim()) {
      setError(s.errorAddress);
      return;
    }
    const address: ShippingAddress = {
      recipientName: form.recipientName.trim(),
      phone: form.phone.trim(),
      postalCode: form.postalCode.trim(),
      addressLine1: form.addressLine1.trim(),
      addressLine2: form.addressLine2?.trim() || undefined,
    };
    // 기존 동작 보존: 로컬 저장은 그대로 한다(다음 주문의 초깃값으로 쓰인다).
    saveShippingAddress(address);
    onSubmitAddress?.(address);
    onComplete();
  };

  const fieldStyle = {
    background: "rgba(0, 0, 0, 0.35)",
    border: "1px solid rgba(255, 255, 255, 0.12)",
    color: "#F5F5F7",
  } as const;

  return (
    <div className="h-full flex flex-col min-h-0 overflow-hidden">
      <header className="px-6 pt-8 pb-4 flex items-center justify-between relative shrink-0">
        <motion.button
          type="button"
          onClick={onBack}
          className="w-10 h-10 rounded-full flex items-center justify-center"
          style={{ background: "#1C1C1E", border: "1px solid #333333" }}
          whileTap={{ scale: 0.95 }}
        >
          <ArrowLeft className="w-4 h-4" style={{ color: "#F5F5F7" }} strokeWidth={1.5} />
        </motion.button>
        <h1 className="text-xl font-light absolute left-1/2 -translate-x-1/2" style={{ color: "#F5F5F7" }}>
          {s.title}
        </h1>
        <div className="w-10" aria-hidden />
      </header>

      <div className="flex-1 min-h-0 overflow-y-auto overscroll-contain px-8 pb-6">
        <div className="flex items-start gap-3 mb-6 rounded-2xl p-4" style={{ background: "rgba(201, 162, 39, 0.08)", border: "1px solid rgba(201, 162, 39, 0.2)" }}>
          <Package className="w-5 h-5 shrink-0 mt-0.5" style={{ color: "#c9a227" }} />
          <p className="text-sm font-light leading-relaxed" style={{ color: "#C8C8CC" }}>
            {s.hint}
          </p>
        </div>

        <div className="space-y-4">
          <label className="block space-y-2">
            <span className="text-xs tracking-wider" style={{ color: "#A1A1A6" }}>{s.recipientName}</span>
            <input
              value={form.recipientName}
              onChange={(e) => update("recipientName", e.target.value)}
              className="w-full rounded-xl px-4 py-3 text-sm outline-none"
              style={fieldStyle}
              autoComplete="name"
            />
          </label>

          <label className="block space-y-2">
            <span className="text-xs tracking-wider" style={{ color: "#A1A1A6" }}>{s.phone}</span>
            <input
              value={form.phone}
              onChange={(e) => update("phone", e.target.value)}
              className="w-full rounded-xl px-4 py-3 text-sm outline-none"
              style={fieldStyle}
              inputMode="tel"
              autoComplete="tel"
            />
          </label>

          <label className="block space-y-2">
            <span className="text-xs tracking-wider" style={{ color: "#A1A1A6" }}>{s.postalCode}</span>
            <input
              value={form.postalCode}
              onChange={(e) => update("postalCode", e.target.value)}
              className="w-full rounded-xl px-4 py-3 text-sm outline-none"
              style={fieldStyle}
              inputMode="numeric"
              autoComplete="postal-code"
            />
          </label>

          <label className="block space-y-2">
            <span className="text-xs tracking-wider flex items-center gap-1.5" style={{ color: "#A1A1A6" }}>
              <MapPin className="w-3.5 h-3.5" />
              {s.addressLine1}
            </span>
            <input
              value={form.addressLine1}
              onChange={(e) => update("addressLine1", e.target.value)}
              className="w-full rounded-xl px-4 py-3 text-sm outline-none"
              style={fieldStyle}
              autoComplete="street-address"
            />
          </label>

          <label className="block space-y-2">
            <span className="text-xs tracking-wider" style={{ color: "#A1A1A6" }}>{s.addressLine2}</span>
            <input
              value={form.addressLine2 ?? ""}
              onChange={(e) => update("addressLine2", e.target.value)}
              className="w-full rounded-xl px-4 py-3 text-sm outline-none"
              style={fieldStyle}
            />
          </label>
        </div>

        {error ? (
          <p className="mt-4 text-sm text-center" style={{ color: "#e08b8b" }}>{error}</p>
        ) : null}
      </div>

      <div className="px-8 pb-10 shrink-0">
        <motion.button
          type="button"
          onClick={handleSubmit}
          className="w-full py-4 rounded-2xl font-normal text-[15px] tracking-wider"
          style={{
            background: "linear-gradient(135deg, #b8860b 0%, #c9a227 30%, #d4af37 50%, #f5d77a 70%, #d4af37 100%)",
            boxShadow: "0 10px 40px rgba(201, 162, 39, 0.25)",
            color: "#0a0a0a",
          }}
          whileHover={{ scale: 1.02 }}
          whileTap={{ scale: 0.98 }}
        >
          {s.continueNfc}
        </motion.button>
      </div>
    </div>
  );
}
