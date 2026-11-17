"use client";

import { useEffect, useRef, useState } from "react";

const HERO_DOG = "/home/hero-dog.png?v=22";

/** 강아지는 고정. 배경만 교차한다. */
const HERO_BACKGROUNDS = [
  "/home/hero-bg-meadow.jpg?v=22",
  "/home/hero-bg-sea.jpg?v=22",
  "/home/hero-bg-forest.jpg?v=22",
  "/home/hero-bg-autumn.jpg?v=22",
  "/home/hero-bg-winter.jpg?v=22",
] as const;

const SCENE_KEYS = ["meadow", "sea", "forest", "autumn", "winter"] as const;

/** 장면마다 충분히 머물러 대기 모션이 보이게 */
const BG_HOLD_MS = 7200;

/** 단풍 — 느리고 가볍게 */
const AUTUMN_LEAVES = [
  { left: "10%", delay: "0s", dur: "16s", tone: "a", size: 13 },
  { left: "22%", delay: "-3s", dur: "18s", tone: "b", size: 11 },
  { left: "34%", delay: "-6s", dur: "15s", tone: "c", size: 14 },
  { left: "48%", delay: "-2s", dur: "17s", tone: "a", size: 12 },
  { left: "60%", delay: "-8s", dur: "16s", tone: "d", size: 13 },
  { left: "72%", delay: "-4s", dur: "19s", tone: "b", size: 11 },
  { left: "84%", delay: "-10s", dur: "17s", tone: "c", size: 12 },
] as const;

function MapleLeafIcon() {
  return (
    <svg viewBox="0 0 24 24" aria-hidden focusable="false">
      <path
        d="M12 2.2c.4 1.6 1.2 2.8 2.4 3.6 1.5-1.2 3.2-1.6 4.8-1.2-.2 1.8-1 3.2-2.2 4.2 1.6.6 2.8 1.6 3.4 3-.1.2-2.2.4-3.6.2 1 .9 1.6 2.1 1.8 3.4-1.5-.2-2.8-.8-3.8-1.8.2 1.6.2 3.1 0 4.6L12 21.8l-2.8-3.2c-.2-1.5-.2-3 0-4.6-1 1-2.3 1.6-3.8 1.8.2-1.3.8-2.5 1.8-3.4-1.4.2-3.5 0-3.6-.2.6-1.4 1.8-2.4 3.4-3-1.2-1-2-2.4-2.2-4.2 1.6-.4 3.3 0 4.8 1.2C10.8 5 11.6 3.8 12 2.2z"
        fill="currentColor"
      />
    </svg>
  );
}

type Flake = {
  x: number;
  y: number;
  r: number;
  vy: number;
  vx: number;
  a: number;
};

/**
 * CSS animation 이 OS 모션 감소에 막혀도 눈이 내리도록
 * requestAnimationFrame 캔버스로 그린다. (영상 루프처럼)
 */
function WinterSnowCanvas({ active, paused }: { active: boolean; paused: boolean }) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const flakesRef = useRef<Flake[]>([]);
  const rafRef = useRef(0);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const parent = canvas.parentElement;
    if (!parent) return;

    const resize = () => {
      const rect = parent.getBoundingClientRect();
      const dpr = Math.min(window.devicePixelRatio || 1, 2);
      canvas.width = Math.max(1, Math.floor(rect.width * dpr));
      canvas.height = Math.max(1, Math.floor(rect.height * dpr));
      canvas.style.width = `${rect.width}px`;
      canvas.style.height = `${rect.height}px`;
      const ctx = canvas.getContext("2d");
      if (ctx) ctx.setTransform(dpr, 0, 0, dpr, 0, 0);

      const count = Math.max(36, Math.floor((rect.width * rect.height) / 9000));
      flakesRef.current = Array.from({ length: count }, () => ({
        x: Math.random() * rect.width,
        y: Math.random() * rect.height,
        r: 1.2 + Math.random() * 2.4,
        vy: 18 + Math.random() * 28,
        vx: -12 + Math.random() * 24,
        a: 0.35 + Math.random() * 0.45,
      }));
    };

    resize();
    const ro = new ResizeObserver(resize);
    ro.observe(parent);
    return () => ro.disconnect();
  }, []);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;

    let last = performance.now();

    const tick = (now: number) => {
      rafRef.current = requestAnimationFrame(tick);
      if (!active || paused) {
        ctx.clearRect(0, 0, canvas.clientWidth, canvas.clientHeight);
        return;
      }

      const dt = Math.min(0.05, (now - last) / 1000);
      last = now;
      const w = canvas.clientWidth;
      const h = canvas.clientHeight;
      ctx.clearRect(0, 0, w, h);

      for (const f of flakesRef.current) {
        f.y += f.vy * dt;
        f.x += (f.vx + Math.sin((now / 1000 + f.y) * 0.8) * 8) * dt;
        if (f.y > h + 6) {
          f.y = -6;
          f.x = Math.random() * w;
        }
        if (f.x < -8) f.x = w + 8;
        if (f.x > w + 8) f.x = -8;

        ctx.beginPath();
        ctx.fillStyle = `rgba(255,255,255,${f.a})`;
        ctx.arc(f.x, f.y, f.r, 0, Math.PI * 2);
        ctx.fill();
      }
    };

    rafRef.current = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(rafRef.current);
  }, [active, paused]);

  return (
    <canvas
      ref={canvasRef}
      className={`eb-home__snow-canvas${active ? " is-on" : ""}`}
      aria-hidden
    />
  );
}

type Glint = {
  x: number;
  y: number;
  rx: number;
  ry: number;
  vx: number;
  phase: number;
  a: number;
};

/**
 * 바닷가 — 줄무늬 없이, 수면 위 부드러운 빛 얼룩만.
 * (가로 stroke 물결선은 쓰지 않는다.)
 */
function SeaWaterCanvas({ active, paused }: { active: boolean; paused: boolean }) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const glintsRef = useRef<Glint[]>([]);
  const rafRef = useRef(0);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const parent = canvas.parentElement;
    if (!parent) return;

    const resize = () => {
      const rect = parent.getBoundingClientRect();
      const dpr = Math.min(window.devicePixelRatio || 1, 2);
      canvas.width = Math.max(1, Math.floor(rect.width * dpr));
      canvas.height = Math.max(1, Math.floor(rect.height * dpr));
      canvas.style.width = `${rect.width}px`;
      canvas.style.height = `${rect.height}px`;
      const ctx = canvas.getContext("2d");
      if (ctx) ctx.setTransform(dpr, 0, 0, dpr, 0, 0);

      // 수면은 그림 중·우측 (모래·야자수는 피함)
      const y0 = rect.height * 0.42;
      const y1 = rect.height * 0.56;
      glintsRef.current = Array.from({ length: 14 }, (_, i) => ({
        x: rect.width * (0.28 + Math.random() * 0.68),
        y: y0 + Math.random() * (y1 - y0),
        rx: 18 + Math.random() * 36,
        ry: 5 + Math.random() * 9,
        vx: 6 + Math.random() * 12,
        phase: Math.random() * Math.PI * 2,
        a: 0.2 + (i % 4) * 0.05,
      }));
    };

    resize();
    const ro = new ResizeObserver(resize);
    ro.observe(parent);
    return () => ro.disconnect();
  }, []);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;

    let last = performance.now();

    const tick = (now: number) => {
      rafRef.current = requestAnimationFrame(tick);
      const w = canvas.clientWidth;
      const h = canvas.clientHeight;
      if (!active || paused) {
        ctx.clearRect(0, 0, w, h);
        return;
      }

      const dt = Math.min(0.05, (now - last) / 1000);
      last = now;
      ctx.clearRect(0, 0, w, h);

      // 바다 영역만 — 왼쪽 야자수·아래 모래는 페이드로 약하게
      const y0 = h * 0.4;
      const y1 = h * 0.58;
      const t = now / 1000;

      ctx.save();
      ctx.beginPath();
      // 오른쪽 수면 위주 사다리꼴에 가깝게
      ctx.moveTo(w * 0.22, y0);
      ctx.lineTo(w, y0);
      ctx.lineTo(w, y1);
      ctx.lineTo(w * 0.18, y1);
      ctx.closePath();
      ctx.clip();

      for (const g of glintsRef.current) {
        g.x += g.vx * dt;
        g.y += Math.sin(t * 0.9 + g.phase) * 4 * dt;
        if (g.x > w + g.rx) {
          g.x = w * 0.2 - g.rx;
          g.y = y0 + Math.random() * (y1 - y0);
        }
        if (g.y < y0 + 2) g.y = y0 + 2;
        if (g.y > y1 - 2) g.y = y1 - 2;

        const pulse = 0.65 + 0.35 * Math.sin(t * 1.6 + g.phase);
        const grad = ctx.createRadialGradient(g.x, g.y, 0, g.x, g.y, g.rx);
        grad.addColorStop(0, `rgba(255,255,255,${g.a * pulse})`);
        grad.addColorStop(0.45, `rgba(210,236,250,${g.a * pulse * 0.45})`);
        grad.addColorStop(1, "rgba(255,255,255,0)");
        ctx.fillStyle = grad;
        ctx.beginPath();
        ctx.ellipse(g.x, g.y, g.rx, g.ry, 0, 0, Math.PI * 2);
        ctx.fill();
      }

      // 아주 옅은 햇살 흐림 (띠/선 아님)
      const sunX = w * (0.55 + 0.12 * Math.sin(t * 0.35));
      const sunY = (y0 + y1) / 2;
      const sun = ctx.createRadialGradient(sunX, sunY, 0, sunX, sunY, w * 0.28);
      sun.addColorStop(0, "rgba(255,255,255,0.1)");
      sun.addColorStop(0.55, "rgba(200,230,245,0.04)");
      sun.addColorStop(1, "rgba(255,255,255,0)");
      ctx.fillStyle = sun;
      ctx.fillRect(w * 0.18, y0, w * 0.82, y1 - y0);

      ctx.restore();
    };

    rafRef.current = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(rafRef.current);
  }, [active, paused]);

  return (
    <canvas
      ref={canvasRef}
      className={`eb-home__sea-canvas${active ? " is-on" : ""}`}
      aria-hidden
    />
  );
}

/**
 * 강아지는 그대로 두고, 배경만 서서히 바뀐다.
 * 대기는 영상 루프처럼 느리고 연속적으로 — 꽃 부분 마스크 흔들림은 쓰지 않는다.
 */
export function LivingHero({ src, alt }: { src: string; alt: string }) {
  const [reduce, setReduce] = useState(false);
  const [paused, setPaused] = useState(false);
  const [bgIndex, setBgIndex] = useState(0);

  useEffect(() => {
    const motion = window.matchMedia("(prefers-reduced-motion: reduce)");
    const apply = () => setReduce(motion.matches);
    apply();
    motion.addEventListener("change", apply);
    const onHide = () => setPaused(document.hidden);
    onHide();
    document.addEventListener("visibilitychange", onHide);
    return () => {
      motion.removeEventListener("change", apply);
      document.removeEventListener("visibilitychange", onHide);
    };
  }, []);

  useEffect(() => {
    if (paused) return;
    const id = window.setInterval(() => {
      setBgIndex((i) => (i + 1) % HERO_BACKGROUNDS.length);
    }, BG_HOLD_MS);
    return () => window.clearInterval(id);
  }, [paused]);

  useEffect(() => {
    HERO_BACKGROUNDS.forEach((url) => {
      const img = new Image();
      img.src = url;
    });
  }, []);

  const scene = SCENE_KEYS[bgIndex] ?? "meadow";
  const meadowUrl = HERO_BACKGROUNDS[0];
  const seaUrl = HERO_BACKGROUNDS[1];

  return (
    <div
      className="eb-home__hero-media"
      data-hero="living"
      data-paused={paused ? "true" : "false"}
      data-reduce={reduce ? "true" : "false"}
      data-scene={bgIndex}
      data-fx={scene}
      style={{
        ["--eb-hero-art" as string]: `url(${src})`,
        ["--eb-meadow-art" as string]: `url(${meadowUrl})`,
        ["--eb-sea-art" as string]: `url(${seaUrl})`,
      }}
    >
      <div className="eb-home__hero-bgs" aria-hidden>
        {HERO_BACKGROUNDS.map((bg, i) => (
          <img
            key={bg}
            src={bg}
            alt=""
            className={`eb-home__hero-bg${i === bgIndex ? " is-on is-live" : ""}`}
            width={832}
            height={740}
            draggable={false}
            decoding="async"
            loading="eager"
            fetchPriority={i === 0 ? "high" : "low"}
          />
        ))}
      </div>

      <div className="eb-home__hero-fx eb-home__hero-fx--behind" aria-hidden>
        {/* 초원: 꽃만 흔들지 않고, 아래 풀밭 띠가 바람에 아주 천천히 */}
        <div className={`eb-home__fx eb-home__fx--meadow${scene === "meadow" ? " is-on" : ""}`}>
          <span className="eb-home__grass-wind" />
        </div>
        {/* 바다: 모래·하늘 제외, 수면만 반짝임(캔버스) */}
        <div className={`eb-home__fx eb-home__fx--sea${scene === "sea" ? " is-on" : ""}`}>
          <SeaWaterCanvas active={scene === "sea"} paused={paused} />
        </div>
      </div>

      <img
        src={HERO_DOG}
        alt={alt}
        className="eb-home__hero-dog"
        width={832}
        height={740}
        loading="eager"
        fetchPriority="high"
        decoding="async"
        draggable={false}
      />

      <div className="eb-home__hero-fx eb-home__hero-fx--front" aria-hidden>
        <div className={`eb-home__fx eb-home__fx--autumn${scene === "autumn" ? " is-on" : ""}`}>
          {AUTUMN_LEAVES.map((leaf, i) => (
            <span
              key={`leaf-${i}`}
              className={`eb-home__maple eb-home__maple--${leaf.tone}`}
              style={{
                left: leaf.left,
                width: leaf.size,
                height: leaf.size,
                animationDelay: leaf.delay,
                animationDuration: leaf.dur,
              }}
            >
              <MapleLeafIcon />
            </span>
          ))}
        </div>

        <div className={`eb-home__fx eb-home__fx--winter${scene === "winter" ? " is-on" : ""}`}>
          <WinterSnowCanvas active={scene === "winter"} paused={paused} />
        </div>
      </div>
    </div>
  );
}
