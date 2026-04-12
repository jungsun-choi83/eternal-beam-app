"use client";

import { useState, useCallback, useRef } from "react";
import { motion, AnimatePresence } from "framer-motion";
import { Upload, ArrowLeft, Check, Image as ImageIcon, Video, Play, Pause } from "lucide-react";
import { HologramEffects } from "./hologram-effects";

interface PhotoUploadScreenProps {
  uploadedImage: string | null;
  onImageUpload: (imageUrl: string) => void;
  onContinue: () => void;
  onBack: () => void;
}

export function PhotoUploadScreen({
  uploadedImage,
  onImageUpload,
  onContinue,
  onBack,
}: PhotoUploadScreenProps) {
  const [isDragging, setIsDragging] = useState(false);
  const [isGlowing, setIsGlowing] = useState(false);
  const [mediaType, setMediaType] = useState<"image" | "video" | null>(null);
  const [isPlaying, setIsPlaying] = useState(false);
  const videoRef = useRef<HTMLVideoElement>(null);

  const handleDragOver = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    setIsDragging(true);
    setIsGlowing(true);
  }, []);

  const handleDragLeave = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    setIsDragging(false);
    setIsGlowing(false);
  }, []);

  const handleDrop = useCallback(
    (e: React.DragEvent) => {
      e.preventDefault();
      setIsDragging(false);
      setIsGlowing(false);

      const file = e.dataTransfer.files[0];
      if (file) {
        if (file.type.startsWith("image/")) {
          setMediaType("image");
          const reader = new FileReader();
          reader.onload = () => {
            onImageUpload(reader.result as string);
          };
          reader.readAsDataURL(file);
        } else if (file.type.startsWith("video/")) {
          setMediaType("video");
          const reader = new FileReader();
          reader.onload = () => {
            onImageUpload(reader.result as string);
          };
          reader.readAsDataURL(file);
        }
      }
    },
    [onImageUpload]
  );

  const handleFileSelect = useCallback(
    (e: React.ChangeEvent<HTMLInputElement>) => {
      const file = e.target.files?.[0];
      if (file) {
        if (file.type.startsWith("image/")) {
          setMediaType("image");
        } else if (file.type.startsWith("video/")) {
          setMediaType("video");
        }
        const reader = new FileReader();
        reader.onload = () => {
          onImageUpload(reader.result as string);
        };
        reader.readAsDataURL(file);
      }
    },
    [onImageUpload]
  );

  const togglePlayPause = () => {
    if (videoRef.current) {
      if (isPlaying) {
        videoRef.current.pause();
      } else {
        videoRef.current.play();
      }
      setIsPlaying(!isPlaying);
    }
  };

  return (
    <div className="h-full flex flex-col bg-[#0a0a0a] relative overflow-hidden">
      <HologramEffects />
      {/* Background Gradient Orbs */}
      <div className="absolute inset-0 pointer-events-none">
        <motion.div
          className="absolute -top-10 right-0 w-48 h-48 rounded-full"
          style={{
            background: "radial-gradient(circle, rgba(244, 114, 106, 0.35) 0%, transparent 70%)",
            filter: "blur(45px)",
          }}
          animate={{
            scale: [1, 1.1, 1],
            x: [0, -10, 0],
          }}
          transition={{ duration: 6, repeat: Infinity, ease: "easeInOut" }}
        />
        <motion.div
          className="absolute bottom-40 -left-10 w-40 h-40 rounded-full"
          style={{
            background: "radial-gradient(circle, rgba(134, 182, 181, 0.3) 0%, transparent 70%)",
            filter: "blur(35px)",
          }}
          animate={{
            scale: [1.1, 1, 1.1],
            y: [0, 15, 0],
          }}
          transition={{ duration: 7, repeat: Infinity, ease: "easeInOut" }}
        />
        <motion.div
          className="absolute top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 w-64 h-64 rounded-full"
          style={{
            background: "radial-gradient(circle, rgba(201, 162, 39, 0.12) 0%, transparent 60%)",
            filter: "blur(50px)",
          }}
          animate={{
            scale: [1, 1.2, 1],
            opacity: [0.3, 0.5, 0.3],
          }}
          transition={{ duration: 8, repeat: Infinity, ease: "easeInOut" }}
        />
      </div>

      {/* Ambient Glow on Drag */}
      <AnimatePresence>
        {isGlowing && (
          <motion.div
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            className="absolute inset-0 pointer-events-none z-20"
            style={{
              background: "radial-gradient(circle at center, rgba(201, 162, 39, 0.2) 0%, transparent 60%)",
            }}
          />
        )}
      </AnimatePresence>

      {/* Header */}
      <header className="px-6 pt-8 pb-4 flex items-center justify-between relative z-10">
        <motion.button
          initial={{ opacity: 0, x: -10 }}
          animate={{ opacity: 1, x: 0 }}
          onClick={onBack}
          className="w-10 h-10 rounded-full flex items-center justify-center"
          style={{
            background: "rgba(255, 255, 255, 0.08)",
            backdropFilter: "blur(20px)",
            border: "1px solid rgba(255, 255, 255, 0.1)",
          }}
          whileHover={{ scale: 1.05, borderColor: "rgba(255, 255, 255, 0.2)" }}
          whileTap={{ scale: 0.95 }}
        >
          <ArrowLeft className="w-4 h-4" style={{ color: "#F5F5F7" }} strokeWidth={1.5} />
        </motion.button>

        <motion.h1
          initial={{ opacity: 0, y: -10 }}
          animate={{ opacity: 1, y: 0 }}
          className="screen-title absolute left-1/2 -translate-x-1/2"
          style={{ color: "#F5F5F7" }}
        >
          Upload Media
        </motion.h1>

        <div className="w-10" />
      </header>

      {/* Main Upload Area */}
      <div className="flex-1 flex flex-col items-center justify-center px-8 relative z-10">
        <motion.div
          initial={{ opacity: 0, scale: 0.95 }}
          animate={{ opacity: 1, scale: 1 }}
          transition={{ delay: 0.2 }}
          className="w-full max-w-[280px]"
        >
          <h2 className="upload-title text-center">Add Media</h2>
          <p className="upload-subtitle text-center">Photo or video of your companion</p>
          <input
            type="file"
            id="media-upload"
            accept="image/*,video/*"
            onChange={handleFileSelect}
            className="hidden"
          />

          <label
            htmlFor="media-upload"
            onDragOver={handleDragOver}
            onDragLeave={handleDragLeave}
            onDrop={handleDrop}
            className="block cursor-pointer"
          >
            <motion.div
              className={`upload-card relative overflow-hidden aspect-square ${
                isDragging ? "drag-over scale-[1.02]" : ""
              } ${uploadedImage ? "rounded-[28px]" : ""}`}
              style={
                uploadedImage
                  ? {
                      background: "transparent",
                      backdropFilter: "blur(40px)",
                      WebkitBackdropFilter: "blur(40px)",
                      border: isDragging
                        ? "2px solid rgba(201, 162, 39, 0.6)"
                        : "1px solid rgba(255, 255, 255, 0.12)",
                      boxShadow: isDragging
                        ? "0 0 60px rgba(201, 162, 39, 0.2)"
                        : "0 8px 32px rgba(0, 0, 0, 0.2)",
                    }
                  : undefined
              }
              whileHover={
                uploadedImage
                  ? { borderColor: "rgba(255, 255, 255, 0.2)" }
                  : undefined
              }
            >
              {/* Inner highlight */}
              <div
                className="absolute top-0 left-4 right-4 h-px"
                style={{
                  background: "linear-gradient(90deg, transparent, rgba(255, 255, 255, 0.15), transparent)",
                }}
              />

              {uploadedImage ? (
                <>
                  {mediaType === "video" ? (
                    <div className="relative w-full h-full">
                      <video
                        ref={videoRef}
                        src={uploadedImage}
                        className="w-full h-full object-cover"
                        loop
                        muted
                        playsInline
                        onEnded={() => setIsPlaying(false)}
                      />
                      {/* Video Controls Overlay */}
                      <button
                        onClick={(e) => {
                          e.preventDefault();
                          togglePlayPause();
                        }}
                        className="absolute inset-0 flex items-center justify-center bg-black/20"
                      >
                        <div
                          className="w-14 h-14 rounded-full flex items-center justify-center"
                          style={{
                            background: "rgba(255, 255, 255, 0.15)",
                            backdropFilter: "blur(20px)",
                            border: "1px solid rgba(255, 255, 255, 0.2)",
                          }}
                        >
                          {isPlaying ? (
                            <Pause className="w-6 h-6 text-white" fill="white" />
                          ) : (
                            <Play className="w-6 h-6 text-white ml-1" fill="white" />
                          )}
                        </div>
                      </button>
                    </div>
                  ) : (
                    <img src={uploadedImage} alt="Uploaded" className="w-full h-full object-cover" />
                  )}

                  {/* Guideline Overlay */}
                  <div className="absolute inset-0 pointer-events-none">
                    {/* Center Circle Guide */}
                    <div
                      className="absolute top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 w-3/4 h-3/4 rounded-full"
                      style={{ border: "1px dashed rgba(201, 162, 39, 0.4)" }}
                    />

                    {/* Corner Markers */}
                    {["top-4 left-4", "top-4 right-4", "bottom-4 left-4", "bottom-4 right-4"].map(
                      (pos, i) => (
                        <div key={i} className={`absolute ${pos}`}>
                          <div className="w-6 h-6">
                            <div
                              className={`absolute ${i < 2 ? "top-0" : "bottom-0"} ${
                                i % 2 === 0 ? "left-0" : "right-0"
                              } w-4 h-[1px]`}
                              style={{ background: "rgba(201, 162, 39, 0.5)" }}
                            />
                            <div
                              className={`absolute ${i < 2 ? "top-0" : "bottom-0"} ${
                                i % 2 === 0 ? "left-0" : "right-0"
                              } h-4 w-[1px]`}
                              style={{ background: "rgba(201, 162, 39, 0.5)" }}
                            />
                          </div>
                        </div>
                      )
                    )}
                  </div>

                  {/* Success Badge */}
                  <motion.div
                    initial={{ scale: 0, opacity: 0 }}
                    animate={{ scale: 1, opacity: 1 }}
                    transition={{ delay: 0.3, type: "spring" }}
                    className="absolute top-4 right-4 w-9 h-9 rounded-full flex items-center justify-center"
                    style={{
                      background: "linear-gradient(135deg, #c9a227 0%, #d4af37 100%)",
                      boxShadow: "0 4px 20px rgba(201, 162, 39, 0.4)",
                    }}
                  >
                    <Check className="w-4 h-4 text-[#0a0a0a]" strokeWidth={2} />
                  </motion.div>

                  {/* Media Type Badge */}
                  <div
                    className="absolute bottom-4 left-4 px-3 py-1 rounded-full flex items-center gap-1.5"
                    style={{
                      background: "rgba(0, 0, 0, 0.5)",
                      backdropFilter: "blur(20px)",
                      border: "1px solid rgba(255, 255, 255, 0.1)",
                    }}
                  >
                    {mediaType === "video" ? (
                      <Video className="w-3 h-3" style={{ color: "#c9a227" }} />
                    ) : (
                      <ImageIcon className="w-3 h-3" style={{ color: "#c9a227" }} />
                    )}
                    <span className="text-[10px] font-light" style={{ color: "#F5F5F7" }}>
                      {mediaType === "video" ? "Video" : "Photo"}
                    </span>
                  </div>
                </>
              ) : (
                <div className="absolute inset-0 flex flex-col items-center justify-center gap-5">
                  <motion.div
                    animate={{ y: isDragging ? -8 : 0 }}
                    transition={{ type: "spring", stiffness: 300 }}
                  >
                    <div
                      className="w-16 h-16 rounded-2xl flex items-center justify-center"
                      style={{
                        background: "rgba(201, 162, 39, 0.1)",
                        border: "1px solid rgba(201, 162, 39, 0.2)",
                      }}
                    >
                      {isDragging ? (
                        <ImageIcon className="w-7 h-7 text-[#c9a227]" strokeWidth={1.5} />
                      ) : (
                        <Upload className="w-7 h-7 text-[#c9a227]/70" strokeWidth={1.5} />
                      )}
                    </div>
                  </motion.div>

                  <div className="text-center">
                    <p className="text-sm font-light" style={{ color: "#F5F5F7" }}>
                      {isDragging ? "Drop to upload" : "Drag media here"}
                    </p>
                    <p className="text-xs mt-1.5 font-light" style={{ color: "#A1A1A6" }}>
                      or tap to browse
                    </p>
                  </div>

                  <p className="upload-hint text-center">JPG, PNG, MP4, MOV supported</p>

                  {/* Supported formats */}
                  <div className="flex items-center gap-2">
                    {["JPG", "PNG", "MP4", "MOV"].map((format) => (
                      <div
                        key={format}
                        className="px-2 py-0.5 rounded-full text-[9px] font-light"
                        style={{
                          background: "rgba(201, 162, 39, 0.08)",
                          border: "1px solid rgba(201, 162, 39, 0.15)",
                          color: "#c9a227",
                        }}
                      >
                        {format}
                      </div>
                    ))}
                  </div>
                </div>
              )}
            </motion.div>
          </label>

          {/* Instruction Text */}
          <motion.p
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            transition={{ delay: 0.4 }}
            className="text-center text-[12px] mt-7 font-light leading-relaxed"
            style={{ color: "#A1A1A6" }}
          >
            Upload a clear photo or video of your beloved companion.
            <br />
            Best results with centered subjects.
          </motion.p>
        </motion.div>
      </div>

      {/* Continue Button */}
      <div className="px-8 pb-10 relative z-10">
        <motion.button
          initial={{ opacity: 0, y: 20 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ delay: 0.5 }}
          onClick={onContinue}
          disabled={!uploadedImage}
          className="w-full py-4 rounded-2xl font-normal text-[15px] tracking-wider transition-all duration-300"
          style={{
            background: uploadedImage
              ? "linear-gradient(135deg, #b8860b 0%, #c9a227 30%, #d4af37 50%, #f5d77a 70%, #d4af37 100%)"
              : "rgba(255, 255, 255, 0.06)",
            backdropFilter: uploadedImage ? "none" : "blur(20px)",
            border: uploadedImage ? "none" : "1px solid rgba(255, 255, 255, 0.1)",
            color: uploadedImage ? "#0a0a0a" : "#A1A1A6",
            boxShadow: uploadedImage ? "0 10px 40px rgba(201, 162, 39, 0.25)" : "none",
            cursor: uploadedImage ? "pointer" : "not-allowed",
          }}
          whileHover={uploadedImage ? { scale: 1.02 } : {}}
          whileTap={uploadedImage ? { scale: 0.98 } : {}}
        >
          Continue
        </motion.button>
      </div>
    </div>
  );
}
