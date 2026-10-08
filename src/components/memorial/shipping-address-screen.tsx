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

  // Phase 10 — 입력은 .eb-input(오프화이트 · 헤어라인 · 골드 포커스 링) 하나로 통일.
  const fieldClass = "eb-input w-full rounded-xl px-4 py-3 text-sm outline-none";
  const labelClass = "eb-caption text-xs tracking-wider text-[var(--eb-text-2)]";

  return (
    <div className="shipping-address-screen h-full flex flex-col min-h-0 overflow-hidden bg-[var(--eb-bg)] text-[var(--eb-text)]">
      <header className="eb-screen-header">
        <div className="eb-screen-header__leading">
          <motion.button
            type="button"
            onClick={onBack}
            className="mem-icon-btn eb-back-btn w-10 h-10 rounded-full flex items-center justify-center"
            whileTap={{ scale: 0.95 }}
            aria-label="뒤로"
          >
            <ArrowLeft className="w-5 h-5" strokeWidth={1.5} />
          </motion.button>
        </div>
        <h1 className="eb-screen-header__title screen-title text-xl font-light">
          {s.title}
        </h1>
        <div className="eb-screen-header__trailing" aria-hidden />
      </header>

      <div className="flex-1 min-h-0 overflow-y-auto overscroll-contain px-6 pb-6">
        <div className="eb-container mx-auto w-full max-w-[520px]">
          <div className="eb-notice eb-notice--premium flex items-start gap-3 mb-6 rounded-2xl p-4">
            <Package className="w-5 h-5 shrink-0 mt-0.5 text-[var(--eb-gold-text)]" />
            <p className="text-sm leading-relaxed text-[var(--eb-text)]">
              {s.hint}
            </p>
          </div>

          <div className="space-y-4">
            <label className="block space-y-2">
              <span className={labelClass}>{s.recipientName}</span>
              <input
                value={form.recipientName}
                onChange={(e) => update("recipientName", e.target.value)}
                className={fieldClass}
                autoComplete="name"
              />
            </label>

            <label className="block space-y-2">
              <span className={labelClass}>{s.phone}</span>
              <input
                value={form.phone}
                onChange={(e) => update("phone", e.target.value)}
                className={fieldClass}
                inputMode="tel"
                autoComplete="tel"
              />
            </label>

            <label className="block space-y-2">
              <span className={labelClass}>{s.postalCode}</span>
              <input
                value={form.postalCode}
                onChange={(e) => update("postalCode", e.target.value)}
                className={fieldClass}
                inputMode="numeric"
                autoComplete="postal-code"
              />
            </label>

            <label className="block space-y-2">
              <span className={`${labelClass} flex items-center gap-1.5`}>
                <MapPin className="w-3.5 h-3.5" />
                {s.addressLine1}
              </span>
              <input
                value={form.addressLine1}
                onChange={(e) => update("addressLine1", e.target.value)}
                className={fieldClass}
                autoComplete="street-address"
              />
            </label>

            <label className="block space-y-2">
              <span className={labelClass}>{s.addressLine2}</span>
              <input
                value={form.addressLine2 ?? ""}
                onChange={(e) => update("addressLine2", e.target.value)}
                className={fieldClass}
              />
            </label>
          </div>

          {error ? (
            <p className="eb-field-error mt-4 text-sm text-center" role="alert">{error}</p>
          ) : null}
        </div>
      </div>

      <div className="eb-screen-footer shrink-0">
        <div className="eb-container mx-auto w-full max-w-[520px]">
          <motion.button
            type="button"
            onClick={handleSubmit}
            className="eb-btn eb-btn--primary eb-btn--block mem-btn-primary w-full rounded-2xl text-[15px] tracking-wider"
            whileTap={{ scale: 0.98 }}
          >
            {s.continueNfc}
          </motion.button>
        </div>
      </div>
    </div>
  );
}
