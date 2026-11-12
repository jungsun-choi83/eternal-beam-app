"use client";

import { useEffect, useRef, useState } from "react";
import { memorialT } from "@/components/memorial/memorial-i18n";
import { LanguageToggle } from "./language-toggle";
import { PaperBrandLockup } from "@/components/memorial/paper-brand-header";
import { deviceIdFromQr, saveScannedDeviceId } from "@/lib/device-qr";

interface DeviceConnectScreenProps {
  language?: string;
  onLanguageChange?: (lang: "ko" | "en") => void;
  /** QR에서 기기 코드를 읽은 뒤. */
  onConnected: () => void;
  /** 기기가 없는 사람 — 연결을 건너뛴다. */
  onSkip: () => void;
}

type DetectedBarcode = { rawValue?: string };
type BarcodeDetectorLike = {
  detect: (source: CanvasImageSource) => Promise<DetectedBarcode[]>;
};

function createQrDetector(): BarcodeDetectorLike | null {
  const Ctor = (
    window as unknown as {
      BarcodeDetector?: new (options: { formats: string[] }) => BarcodeDetectorLike;
    }
  ).BarcodeDetector;
  if (!Ctor) return null;
  try {
    return new Ctor({ formats: ["qr_code"] });
  } catch {
    return null;
  }
}

/**
 * 가입 다음 — 기기 QR을 찍어 연결한다.
 * 아직 기기가 없는 사람은 하단 버튼으로 건너뛴다.
 */
export function DeviceConnectScreen({
  language = "ko",
  onLanguageChange,
  onConnected,
  onSkip,
}: DeviceConnectScreenProps) {
  const copy = memorialT(language).deviceConnect;
  const videoRef = useRef<HTMLVideoElement>(null);
  const onConnectedRef = useRef(onConnected);
  onConnectedRef.current = onConnected;
  const [cameraError, setCameraError] = useState(false);
  const [unsupported, setUnsupported] = useState(false);

  useEffect(() => {
    let cancelled = false;
    let stream: MediaStream | null = null;
    let frame = 0;

    const stop = () => {
      cancelled = true;
      if (frame) window.cancelAnimationFrame(frame);
      stream?.getTracks().forEach((track) => track.stop());
    };

    void (async () => {
      if (!navigator.mediaDevices?.getUserMedia) {
        setCameraError(true);
        return;
      }
      try {
        stream = await navigator.mediaDevices.getUserMedia({
          video: { facingMode: { ideal: "environment" } },
          audio: false,
        });
      } catch {
        if (!cancelled) setCameraError(true);
        return;
      }
      if (cancelled) {
        stream.getTracks().forEach((track) => track.stop());
        return;
      }
      const video = videoRef.current;
      if (!video) return;
      video.srcObject = stream;
      try {
        await video.play();
      } catch {
        if (!cancelled) setCameraError(true);
        return;
      }

      const detector = createQrDetector();
      if (!detector) {
        if (!cancelled) setUnsupported(true);
        return;
      }

      const tick = async () => {
        if (cancelled) return;
        try {
          const codes = await detector.detect(video);
          const deviceId = deviceIdFromQr(codes[0]?.rawValue ?? "");
          if (deviceId) {
            saveScannedDeviceId(deviceId);
            stop();
            onConnectedRef.current();
            return;
          }
        } catch {
          /* 한 프레임 실패는 다음 프레임에서 다시 본다 */
        }
        frame = window.requestAnimationFrame(() => {
          void tick();
        });
      };
      void tick();
    })();

    return stop;
  }, []);

  return (
    <div data-screen="deviceConnect" className="eb-device-connect">
      <div className="eb-langbar">
        {onLanguageChange ? (
          <LanguageToggle language={language} onChange={onLanguageChange} className="eb-entry__lang" />
        ) : null}
      </div>
      <div className="eb-device-connect__body">
        <PaperBrandLockup size={44} />
        <h1 className="eb-device-connect__title">{copy.title}</h1>
        <p className="eb-device-connect__lead">{copy.lead}</p>

        <div className="eb-device-connect__finder" aria-hidden={cameraError}>
          <video ref={videoRef} className="eb-device-connect__video" playsInline muted />
          <span className="eb-device-connect__frame" />
        </div>

        <p className="eb-device-connect__hint" role="status">
          {cameraError ? copy.cameraError : unsupported ? copy.unsupported : copy.hint}
        </p>
      </div>

      <div className="eb-device-connect__footer">
        <button
          type="button"
          className="eb-btn eb-btn--secondary eb-device-connect__skip"
          data-action="skip-device-qr"
          onClick={onSkip}
        >
          {copy.skip}
        </button>
      </div>
    </div>
  );
}
