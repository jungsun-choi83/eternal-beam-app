"use client";

import { useEffect, useRef, useState } from "react";
import { Wind } from "lucide-react";
import {
  createPackedAlphaScratch,
  drawPackedAlphaVideo,
  drawPackedRgbHalfOnly,
  type PackedAlphaScratch,
} from "@/lib/packed-alpha-canvas";
import {
  fitPosterFrame,
  posterCanvasSize,
  posterRenderMode,
  posterSeekTime,
  type PosterRenderMode,
} from "@/lib/library-card-preview";

export type PosterState = "loading" | "ready" | "error";

interface LibraryMotionPosterProps {
  src: string;
  deliveryFormat: string | null;
  /** true 면 인라인 재생(루프) — 매 프레임을 같은 합성기로 캔버스에 그린다. */
  playing?: boolean;
  onDuration?: (seconds: number) => void;
  onStateChange?: (state: PosterState) => void;
}

function videoCrossOrigin(src: string): "anonymous" | undefined {
  try {
    return new URL(src, window.location.href).origin !== window.location.origin ? "anonymous" : undefined;
  } catch {
    return undefined;
  }
}

function attachSource(video: HTMLVideoElement, src: string) {
  const cors = videoCrossOrigin(src);
  if (cors) video.crossOrigin = cors;
  else video.removeAttribute("crossorigin");
  video.src = src;
  video.load();
}

function releaseSource(video: HTMLVideoElement) {
  video.pause();
  video.removeAttribute("src");
  video.load();
}

/**
 * 현재 비디오 프레임 하나를 캔버스에 합성한다. packed_alpha 는 Preview/Composer 와
 * 같은 drawPackedAlphaVideo (RGB 절반 + 알파 절반 → 투명 펫). 위/아래로 쌓인 원본을
 * 그대로 그리는 경로는 없다 — plain(레거시) 만 전체 프레임을 그린다.
 */
function paintFrame(
  canvas: HTMLCanvasElement,
  video: HTMLVideoElement,
  mode: PosterRenderMode,
  scratch: PackedAlphaScratch,
): boolean {
  if (video.readyState < 2 || !video.videoWidth || !video.videoHeight) return false;
  const ctx = canvas.getContext("2d");
  if (!ctx) return false;

  const rect = canvas.getBoundingClientRect();
  const { w, h } = posterCanvasSize(rect.width || canvas.clientWidth, rect.height || canvas.clientHeight, window.devicePixelRatio);
  if (canvas.width !== w || canvas.height !== h) {
    canvas.width = w;
    canvas.height = h;
  }
  ctx.clearRect(0, 0, w, h);

  const frameH = mode === "packed" ? Math.floor(video.videoHeight / 2) : video.videoHeight;
  const { dx, dy, drawW, drawH } = fitPosterFrame(w, h, video.videoWidth, frameH);

  if (mode === "packed") {
    try {
      drawPackedAlphaVideo(ctx, video, dx, dy, drawW, drawH, scratch);
    } catch {
      // cross-origin taint — 알파를 읽을 수 없으면 RGB 절반만 (매트 절반은 절대 노출하지 않는다).
      drawPackedRgbHalfOnly(ctx, video, dx, dy, drawW, drawH, scratch);
    }
  } else {
    ctx.drawImage(video, dx, dy, drawW, drawH);
  }
  return true;
}

/**
 * 카드 미리보기 — 발행 자산의 프레임 하나를 캔버스에 합성해 두고 비디오는 놓아준다.
 *
 * playing 이 켜지면 같은 비디오 요소로 루프 재생하며 프레임마다 합성해 그리고, 꺼지면
 * 재생을 멈추고 정지 프레임을 다시 그린다. 그리드가 카드마다 <video> 를 붙들고 있지
 * 않도록, 정지 상태에서는 프레임을 그린 뒤 src 를 비우고 디코더를 해제한다.
 */
export function LibraryMotionPoster({
  src,
  deliveryFormat,
  playing = false,
  onDuration,
  onStateChange,
}: LibraryMotionPosterProps) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const videoRef = useRef<HTMLVideoElement | null>(null);
  const [state, setState] = useState<PosterState>("loading");
  const mode = posterRenderMode(deliveryFormat, src);

  // ── 정지 포스터 ────────────────────────────────────────────────────────────
  useEffect(() => {
    if (playing) return;
    const video = videoRef.current;
    const canvas = canvasRef.current;
    if (!video || !canvas) return;

    let disposed = false;
    const scratch = createPackedAlphaScratch();
    const update = (next: PosterState) => {
      if (disposed) return;
      setState(next);
      onStateChange?.(next);
    };

    const finish = () => {
      if (disposed) return;
      if (paintFrame(canvas, video, mode, scratch)) {
        update("ready");
        releaseSource(video);
      }
    };
    const onLoadedMetadata = () => {
      if (Number.isFinite(video.duration) && video.duration > 0) onDuration?.(video.duration);
      const at = posterSeekTime(video.duration);
      if (at > 0) video.currentTime = at;
      else if (video.readyState >= 2) finish();
    };
    const onLoadedData = () => {
      if (!video.seeking) finish();
    };
    const onError = () => update("error");

    video.addEventListener("loadedmetadata", onLoadedMetadata);
    video.addEventListener("loadeddata", onLoadedData);
    video.addEventListener("seeked", finish);
    video.addEventListener("error", onError);

    update("loading");
    attachSource(video, src);

    return () => {
      disposed = true;
      video.removeEventListener("loadedmetadata", onLoadedMetadata);
      video.removeEventListener("loadeddata", onLoadedData);
      video.removeEventListener("seeked", finish);
      video.removeEventListener("error", onError);
      releaseSource(video);
    };
    // onDuration/onStateChange 는 카드가 매 렌더마다 새로 만드는 콜백일 수 있다 —
    // src/mode/playing 이 바뀔 때만 다시 디코드한다.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [src, mode, playing]);

  // ── 인라인 재생 ────────────────────────────────────────────────────────────
  useEffect(() => {
    if (!playing) return;
    const video = videoRef.current;
    const canvas = canvasRef.current;
    if (!video || !canvas) return;

    let disposed = false;
    let raf = 0;
    const scratch = createPackedAlphaScratch();
    const update = (next: PosterState) => {
      if (disposed) return;
      setState(next);
      onStateChange?.(next);
    };

    const draw = () => {
      if (disposed) return;
      paintFrame(canvas, video, mode, scratch);
      raf = requestAnimationFrame(draw);
    };
    const onPlaying = () => {
      update("ready");
      cancelAnimationFrame(raf);
      draw();
    };
    const onError = () => update("error");

    video.addEventListener("playing", onPlaying);
    video.addEventListener("error", onError);

    update("loading");
    video.loop = true;
    video.muted = true;
    attachSource(video, src);
    video.play().catch(() => update("error"));

    return () => {
      disposed = true;
      cancelAnimationFrame(raf);
      video.removeEventListener("playing", onPlaying);
      video.removeEventListener("error", onError);
      video.loop = false;
      releaseSource(video);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [src, mode, playing]);

  return (
    <>
      <canvas
        ref={canvasRef}
        className={`my-library__card-poster${state === "loading" ? " my-library__card-poster--loading" : ""}`}
        aria-hidden
      />
      {state === "error" ? (
        <span className="my-library__card-media-fallback" aria-hidden>
          <Wind className="w-6 h-6" />
        </span>
      ) : null}
      <video
        ref={videoRef}
        className="my-library__card-poster-video"
        muted
        playsInline
        preload="auto"
        aria-hidden
        tabIndex={-1}
      />
    </>
  );
}
