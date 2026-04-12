"use client";

import { useState } from "react";
import { motion } from "framer-motion";
import { ChevronLeft, Plus, Play, MoreHorizontal, Trash2 } from "lucide-react";

interface GalleryScreenProps {
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

export function GalleryScreen({ onSelectItem, onAddNew, onBack }: GalleryScreenProps) {
  const [selectedItem, setSelectedItem] = useState<number | null>(null);
  const [showOptions, setShowOptions] = useState<number | null>(null);

  return (
    <div className="h-full flex flex-col bg-[#0a0a0a] relative overflow-hidden">
      {/* Header */}
      <header className="px-6 pt-14 pb-4 flex items-center justify-between relative">
        <button onClick={onBack} className="p-2 -ml-2">
          <ChevronLeft className="w-5 h-5" style={{ color: "#F5F5F7" }} />
        </button>
        <h1 className="text-xl font-light absolute left-1/2 -translate-x-1/2" style={{ color: "#F5F5F7" }}>
          Gallery
        </h1>
        <button onClick={onAddNew} className="p-2 -mr-2">
          <Plus className="w-5 h-5" style={{ color: "#d4af37" }} />
        </button>
      </header>

      {/* Gallery Grid */}
      <div className="flex-1 px-4 pb-6 overflow-y-auto">
        {mockGalleryItems.length > 0 ? (
          <div className="grid grid-cols-2 gap-3">
            {mockGalleryItems.map((item, index) => (
              <motion.div
                key={item.id}
                initial={{ opacity: 0, y: 20 }}
                animate={{ opacity: 1, y: 0 }}
                transition={{ delay: index * 0.1 }}
                className="relative"
              >
                <motion.button
                  onClick={() => onSelectItem(item.id)}
                  className="w-full aspect-square rounded-2xl overflow-hidden relative"
                  style={{
                    background: "rgba(255, 255, 255, 0.03)",
                    border: selectedItem === item.id 
                      ? "2px solid #d4af37" 
                      : "1px solid rgba(255, 255, 255, 0.06)",
                  }}
                  whileTap={{ scale: 0.95 }}
                >
                  {/* Thumbnail Placeholder */}
                  <div className="absolute inset-0 flex items-center justify-center">
                    <div 
                      className="w-16 h-16 rounded-full"
                      style={{
                        background: "linear-gradient(135deg, rgba(212, 175, 55, 0.2) 0%, rgba(201, 162, 39, 0.1) 100%)",
                        border: "1px solid rgba(212, 175, 55, 0.2)",
                      }}
                    />
                  </div>

                  {/* Video Indicator */}
                  {item.isVideo && (
                    <div 
                      className="absolute top-2 right-2 w-6 h-6 rounded-full flex items-center justify-center"
                      style={{ background: "rgba(0, 0, 0, 0.6)" }}
                    >
                      <Play className="w-3 h-3" style={{ color: "#F5F5F7" }} />
                    </div>
                  )}

                  {/* Theme Badge */}
                  <div 
                    className="absolute bottom-2 left-2 px-2 py-1 rounded-full"
                    style={{ 
                      background: "rgba(0, 0, 0, 0.6)",
                      backdropFilter: "blur(10px)",
                    }}
                  >
                    <span className="text-[10px]" style={{ color: "#d4af37" }}>
                      {item.theme}
                    </span>
                  </div>
                </motion.button>

                {/* Options Button */}
                <button
                  onClick={() => setShowOptions(showOptions === item.id ? null : item.id)}
                  className="absolute top-2 left-2 w-6 h-6 rounded-full flex items-center justify-center"
                  style={{ background: "rgba(0, 0, 0, 0.6)" }}
                >
                  <MoreHorizontal className="w-4 h-4" style={{ color: "#F5F5F7" }} />
                </button>

                {/* Options Menu */}
                {showOptions === item.id && (
                  <motion.div
                    initial={{ opacity: 0, scale: 0.9 }}
                    animate={{ opacity: 1, scale: 1 }}
                    className="absolute top-10 left-2 rounded-xl overflow-hidden z-10"
                    style={{
                      background: "rgba(28, 28, 30, 0.95)",
                      backdropFilter: "blur(20px)",
                      border: "1px solid rgba(255, 255, 255, 0.1)",
                    }}
                  >
                    <button 
                      className="flex items-center gap-2 px-4 py-3 w-full hover:bg-white/5"
                      onClick={() => setShowOptions(null)}
                    >
                      <Trash2 className="w-4 h-4 text-red-400" />
                      <span className="text-sm text-red-400">Delete</span>
                    </button>
                  </motion.div>
                )}

                {/* Item Info */}
                <div className="mt-2 px-1">
                  <p className="text-sm" style={{ color: "#F5F5F7" }}>{item.name}</p>
                  <p className="text-xs" style={{ color: "#A1A1A6" }}>{item.date}</p>
                </div>
              </motion.div>
            ))}
          </div>
        ) : (
          <div className="flex-1 flex flex-col items-center justify-center h-full">
            <div 
              className="w-20 h-20 rounded-full flex items-center justify-center mb-4"
              style={{ background: "rgba(255, 255, 255, 0.03)" }}
            >
              <Plus className="w-8 h-8" style={{ color: "#A1A1A6" }} />
            </div>
            <p className="text-sm mb-1" style={{ color: "#F5F5F7" }}>No memories yet</p>
            <p className="text-xs" style={{ color: "#A1A1A6" }}>
              Create your first holographic memory
            </p>
          </div>
        )}
      </div>
    </div>
  );
}
