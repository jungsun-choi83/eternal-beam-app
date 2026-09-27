"use client";

import { useState } from "react";
import { motion } from "framer-motion";
import { Plus, Play, MoreHorizontal, Trash2 } from "lucide-react";
import { memorialT } from "@/components/memorial/memorial-i18n";
import { ScreenHeader } from "@/components/ui/screen-header";

interface GalleryScreenProps {
  language?: string;
  onSelectItem: (id: number) => void;
  onAddNew: () => void;
  onBack: () => void;
}

interface GalleryItem {
  id: number;
  thumbnail: string;
  name: string;
  date: string;
  isVideo: boolean;
  theme: string;
}

const mockGalleryItems: GalleryItem[] = [
  { id: 1, thumbnail: "", name: "Luna", date: "Dec 15, 2024", isVideo: false, theme: "Celestial" },
  { id: 2, thumbnail: "", name: "Max", date: "Nov 28, 2024", isVideo: true, theme: "Golden Meadow" },
  { id: 3, thumbnail: "", name: "Bella", date: "Oct 10, 2024", isVideo: false, theme: "Starlight" },
];

// Phase 10 — chips that sit on top of a thumbnail (media) stay dark and
// translucent so they read over any artwork; everything else is token-driven.
const MEDIA_CHIP_STYLE = {
  background: "rgba(31, 27, 23, 0.62)",
  color: "var(--eb-text-on-inverse)",
  backdropFilter: "blur(10px)",
} as const;

export function GalleryScreen({
  language = "ko",
  onSelectItem,
  onAddNew,
  onBack,
}: GalleryScreenProps) {
  const g = memorialT(language).gallery;
  const common = memorialT(language).common;
  const [selectedItem, setSelectedItem] = useState<number | null>(null);
  const [showOptions, setShowOptions] = useState<number | null>(null);

  return (
    <div className="memorial-screen-shell h-full flex flex-col relative overflow-hidden min-h-0">
      <ScreenHeader
        title={g.title}
        onBack={onBack}
        backLabel={common.back}
        right={
          <button
            type="button"
            onClick={onAddNew}
            className="eb-icon-btn"
            aria-label={g.title}
            title={g.title}
          >
            <Plus className="w-5 h-5" style={{ color: "var(--eb-gold-text)" }} aria-hidden />
          </button>
        }
      />

      {/* Gallery Grid */}
      <div className="flex-1 min-h-0 px-6 pb-[var(--eb-footer-bottom)] overflow-y-auto hide-scrollbar">
        {mockGalleryItems.length > 0 ? (
          <div className="grid grid-cols-2 gap-3">
            {mockGalleryItems.map((item, index) => (
              <motion.div
                key={item.id}
                initial={{ opacity: 0, y: 12 }}
                animate={{ opacity: 1, y: 0 }}
                transition={{ delay: index * 0.06, duration: 0.32 }}
                className="relative"
              >
                <motion.button
                  type="button"
                  onClick={() => onSelectItem(item.id)}
                  aria-pressed={selectedItem === item.id}
                  className={`eb-card eb-card--interactive w-full aspect-square overflow-hidden relative${
                    selectedItem === item.id ? " eb-card--selected" : ""
                  }`}
                  whileTap={{ scale: 0.98 }}
                >
                  {/* Thumbnail Placeholder */}
                  <div className="absolute inset-0 flex items-center justify-center">
                    <div
                      className="w-16 h-16 rounded-full"
                      style={{
                        background: "var(--eb-gold-wash)",
                        border: "1px solid var(--eb-gold-line)",
                      }}
                    />
                  </div>

                  {/* Video Indicator */}
                  {item.isVideo && (
                    <div
                      className="absolute top-2 right-2 w-6 h-6 rounded-full flex items-center justify-center"
                      style={MEDIA_CHIP_STYLE}
                    >
                      <Play className="w-3 h-3" aria-hidden />
                    </div>
                  )}

                  {/* Theme Badge */}
                  <div className="absolute bottom-2 left-2 px-2 py-1 rounded-full" style={MEDIA_CHIP_STYLE}>
                    <span className="text-[11px] font-medium" style={{ color: "var(--eb-gold-soft)" }}>
                      {item.theme}
                    </span>
                  </div>
                </motion.button>

                {/* Options Button — 44px hit area around a 28px chip */}
                <button
                  type="button"
                  onClick={() => setShowOptions(showOptions === item.id ? null : item.id)}
                  className="absolute top-0 left-0 w-11 h-11 flex items-center justify-center rounded-full"
                  aria-haspopup="menu"
                  aria-expanded={showOptions === item.id}
                  aria-label={g.delete}
                >
                  <span
                    className="w-7 h-7 rounded-full flex items-center justify-center"
                    style={MEDIA_CHIP_STYLE}
                  >
                    <MoreHorizontal className="w-4 h-4" aria-hidden />
                  </span>
                </button>

                {/* Options Menu */}
                {showOptions === item.id && (
                  <motion.div
                    initial={{ opacity: 0, scale: 0.96 }}
                    animate={{ opacity: 1, scale: 1 }}
                    transition={{ duration: 0.2 }}
                    role="menu"
                    className="eb-card absolute top-11 left-2 overflow-hidden z-10"
                    style={{ boxShadow: "var(--eb-shadow-2)" }}
                  >
                    <button
                      type="button"
                      role="menuitem"
                      className="flex items-center gap-2 px-4 min-h-[44px] w-full text-sm font-medium"
                      style={{ color: "var(--eb-terracotta-text)" }}
                      onClick={() => setShowOptions(null)}
                    >
                      <Trash2 className="w-4 h-4" aria-hidden />
                      <span>{g.delete}</span>
                    </button>
                  </motion.div>
                )}

                {/* Item Info */}
                <div className="mt-2 px-1">
                  <p className="text-sm font-medium" style={{ color: "var(--eb-text)" }}>
                    {item.name}
                  </p>
                  <p className="eb-caption">{item.date}</p>
                </div>
              </motion.div>
            ))}
          </div>
        ) : (
          <div className="eb-feedback-state h-full">
            <div
              className="w-20 h-20 rounded-full flex items-center justify-center mb-1"
              style={{ background: "var(--eb-surface-2)", border: "1px solid var(--eb-hairline)" }}
            >
              <Plus className="w-8 h-8" style={{ color: "var(--eb-text-3)" }} aria-hidden />
            </div>
            <p className="eb-feedback-state__title">{g.emptyTitle}</p>
            <p className="eb-feedback-state__description">{g.emptyHint}</p>
          </div>
        )}
      </div>
    </div>
  );
}
