"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import {
  createPackedAlphaScratch,
  drawPackedAlphaVideo,
  isPackedAlphaVideo,
} from "@/lib/packed-alpha-canvas";

interface IdleLoopVideoProps {
  src: string;
  className?: string;
  style?: React.CSSProperties;
  /** metadata = 빠른 첫 프레임, auto = 전체 프리로드 */
  preload?: "none" | "metadata" | "auto";
  /** true(기본): packed alpha / Luma 블랙배경 제거 후 투명 합성 */
  transparentComposite?: boolean;
}

type RenderMode = "packed" | "blackkey" | "raw";

const COMPOSITE_FAIL_THRESHOLD = 8;

function removeNearBlackAlpha(
  ctx: CanvasRenderingContext2D,
  x: number,
  y: number,
  w: number,
  h: number,
  threshold = 28
) {
  const ix = Math.round(x);
  const iy = Math.round(y);
  const iw = Math.max(1, Math.round(w));
  const ih = Math.max(1, Math.round(h));
  const imageData = ctx.getImageData(ix, iy, iw, ih);
  const { data } = imageData;
  for (let i = 0; i < data.length; i += 4) {
    if (data[i] <= threshold && data[i + 1] <= threshold && data[i + 2] <= threshold) {
      data[i + 3] = 0;
    }
  }
  ctx.putImageData(imageData, ix, iy);
}

function fitFrameRect(
  cw: number,
  ch: number,
  frameW: number,
  frameH: number,
  alignBottom = true
) {
  const aspect = frameW / frameH;
  let drawW = cw;
  let drawH = drawW / aspect;
  if (drawH > ch) {
    drawH = ch;
    drawW = drawH * aspect;
  }
  const dx = (cw - drawW) / 2;
  const dy = alignBottom ? ch - drawH : (ch - drawH) / 2;
  return { dx, dy, drawW, drawH };
}

function measureWrapSize(wrap: HTMLDivElement): { cw: number; ch: number } {
  let cw = wrap.clientWidth;
  let ch = wrap.clientHeight;
  if (cw > 0 && ch > 0) return { cw, ch };

  const parent = wrap.parentElement;
  if (parent) {
    cw = cw || parent.clientWidth;
    ch = ch || parent.clientHeight;
  }
  return { cw, ch };
}

function videoCrossOrigin(src: string): "" | "anonymous" {
  if (typeof window === "undefined") return "anonymous";
  try {
    const u = new URL(src, window.location.href);
    return u.origin !== window.location.origin ? "anonymous" : "";
  } catch {
    return "";
  }
}

/** Luma idle 루프 — packed alpha·블랙배경 mp4를 투명 PET 레이어로 합성 */
export function IdleLoopVideo({
  src,
  className = "",
  style,
  preload = "metadata",
  transparentComposite = true,
}: IdleLoopVideoProps) {
  const wrapRef = useRef<HTMLDivElement>(null);
  const videoRef = useRef<HTMLVideoElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const scratchRef = useRef(createPackedAlphaScratch());
  const blackkeyScratchRef = useRef<HTMLCanvasElement | null>(null);
  const rafRef = useRef(0);
  const modeRef = useRef<RenderMode>("raw");
  const failCountRef = useRef(0);
  const [useRawFallback, setUseRawFallback] = useState(false);
  const crossOrigin = videoCrossOrigin(src);

  const triggerRawFallback = useCallback(() => {
    modeRef.current = "raw";
    setUseRawFallback(true);
    if (import.meta.env.DEV) {
      console.warn("[IdleLoopVideo] canvas composite failed — falling back to raw video", src);
    }
  }, [src]);

  const renderFrame = useCallback(() => {
    const video = videoRef.current;
    const canvas = canvasRef.current;
    const wrap = wrapRef.current;
    if (!video || !canvas || !wrap || !transparentComposite || modeRef.current === "raw") {
      rafRef.current = requestAnimationFrame(renderFrame);
      return;
    }

    if (video.readyState < 2) {
      rafRef.current = requestAnimationFrame(renderFrame);
      return;
    }

    const { cw, ch } = measureWrapSize(wrap);
    if (cw <= 0 || ch <= 0) {
      rafRef.current = requestAnimationFrame(renderFrame);
      return;
    }

    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    const pw = Math.round(cw * dpr);
    const ph = Math.round(ch * dpr);
    if (canvas.width !== pw || canvas.height !== ph) {
      canvas.width = pw;
      canvas.height = ph;
    }

    const ctx = canvas.getContext("2d", { alpha: true });
    if (!ctx) {
      rafRef.current = requestAnimationFrame(renderFrame);
      return;
    }

    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, cw, ch);

    const vw = video.videoWidth;
    const vh = video.videoHeight;
    if (!vw || !vh) {
      rafRef.current = requestAnimationFrame(renderFrame);
      return;
    }

    try {
      if (modeRef.current === "packed") {
        const frameH = Math.floor(vh / 2);
        const { dx, dy, drawW, drawH } = fitFrameRect(cw, ch, vw, frameH);
        drawPackedAlphaVideo(ctx, video, dx, dy, drawW, drawH, scratchRef.current);
      } else {
        const { dx, dy, drawW, drawH } = fitFrameRect(cw, ch, vw, vh);
        const iw = Math.max(1, Math.round(drawW));
        const ih = Math.max(1, Math.round(drawH));
        let scratch = blackkeyScratchRef.current;
        if (!scratch) {
          scratch = document.createElement("canvas");
          blackkeyScratchRef.current = scratch;
        }
        if (scratch.width !== iw || scratch.height !== ih) {
          scratch.width = iw;
          scratch.height = ih;
        }
        const sctx = scratch.getContext("2d", { willReadFrequently: true });
        if (!sctx) throw new Error("blackkey scratch context unavailable");
        sctx.clearRect(0, 0, iw, ih);
        sctx.drawImage(video, 0, 0, iw, ih);
        removeNearBlackAlpha(sctx, 0, 0, iw, ih);
        ctx.drawImage(scratch, dx, dy, drawW, drawH);
      }
      failCountRef.current = 0;
    } catch (err) {
      failCountRef.current += 1;
      if (import.meta.env.DEV) {
        console.warn("[IdleLoopVideo] frame composite error", err);
      }
      if (failCountRef.current >= COMPOSITE_FAIL_THRESHOLD) {
        triggerRawFallback();
        return;
      }
    }

    rafRef.current = requestAnimationFrame(renderFrame);
  }, [transparentComposite, triggerRawFallback]);

  useEffect(() => {
    failCountRef.current = 0;
    setUseRawFallback(false);
  }, [src, transparentComposite]);

  useEffect(() => {
    const el = videoRef.current;
    if (!el) return;

    el.muted = true;
    el.playsInline = true;
    if (crossOrigin) el.crossOrigin = crossOrigin;
    else el.removeAttribute("crossorigin");
    modeRef.current = transparentComposite ? "blackkey" : "raw";

    const play = () => {
      void el.play().catch(() => {
        /* autoplay policy */
      });
    };

    const detectMode = () => {
      if (!transparentComposite) {
        modeRef.current = "raw";
        return;
      }
      modeRef.current = isPackedAlphaVideo(el) ? "packed" : "blackkey";

      const vw = el.videoWidth;
      const vh = el.videoHeight;
      const wrap = wrapRef.current;
      if (vw && vh && wrap) {
        const frameH = modeRef.current === "packed" ? Math.floor(vh / 2) : vh;
        wrap.style.setProperty("--idle-aspect", `${vw} / ${frameH}`);
      }
    };

    const onVideoError = () => {
      if (import.meta.env.DEV) {
        console.warn("[IdleLoopVideo] video error — falling back to raw", src, el.error);
      }
      triggerRawFallback();
    };

    play();
    el.addEventListener("loadeddata", play);
    el.addEventListener("loadedmetadata", detectMode);
    el.addEventListener("error", onVideoError);
    if (el.readyState >= 1) detectMode();

    return () => {
      el.removeEventListener("loadeddata", play);
      el.removeEventListener("loadedmetadata", detectMode);
      el.removeEventListener("error", onVideoError);
    };
  }, [src, transparentComposite, triggerRawFallback, crossOrigin]);

  useEffect(() => {
    if (!transparentComposite || useRawFallback) return;

    const wrap = wrapRef.current;
    if (!wrap) return;

    const ro = new ResizeObserver(() => {
      /* renderFrame reads latest clientWidth/Height each tick */
    });
    ro.observe(wrap);
    if (wrap.parentElement) ro.observe(wrap.parentElement);

    rafRef.current = requestAnimationFrame(renderFrame);
    return () => {
      ro.disconnect();
      cancelAnimationFrame(rafRef.current);
    };
  }, [src, transparentComposite, useRawFallback, renderFrame]);

  if (!transparentComposite || useRawFallback) {
    return (
      <video
        ref={videoRef}
        src={src}
        className={className}
        style={style}
        autoPlay
        loop
        muted
        playsInline
        preload={preload}
        {...(crossOrigin ? { crossOrigin } : {})}
      />
    );
  }

  return (
    <div ref={wrapRef} className={`idle-loop-video ${className}`} style={style}>
      <video
        ref={videoRef}
        src={src}
        className="idle-loop-video__source"
        autoPlay
        loop
        muted
        playsInline
        preload={preload}
        {...(crossOrigin ? { crossOrigin } : {})}
        aria-hidden
      />
      <canvas ref={canvasRef} className="idle-loop-video__canvas" aria-hidden />
    </div>
  );
}
