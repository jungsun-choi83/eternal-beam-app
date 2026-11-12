"use client";

import { useEffect, useState } from "react";

const HERO_DOG = "/home/hero-dog.png?v=21";

/** 강아지는 고정. 배경만 교차한다. */
const HERO_BACKGROUNDS = [
  "/home/hero-bg-meadow.jpg?v=21",
  "/home/hero-bg-sea.jpg?v=21",
  "/home/hero-bg-forest.jpg?v=21",
  "/home/hero-bg-autumn.jpg?v=21",
  "/home/hero-bg-winter.jpg?v=21",
] as const;

const BG_HOLD_MS = 3800;

/**
 * 강아지는 그대로 두고, 배경만 서서히 바뀐다.
 * (모션 감소 설정이어도 장면 전환은 유지한다.)
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

  // preload so swaps aren't blank
  useEffect(() => {
    HERO_BACKGROUNDS.forEach((url) => {
      const img = new Image();
      img.src = url;
    });
  }, []);

  return (
    <div
      className="eb-home__hero-media"
      data-hero="living"
      data-paused={paused ? "true" : "false"}
      data-reduce={reduce ? "true" : "false"}
      data-scene={bgIndex}
      style={{ ["--eb-hero-art" as string]: `url(${src})` }}
    >
      <div className="eb-home__hero-bgs" aria-hidden>
        {HERO_BACKGROUNDS.map((bg, i) => (
          <img
            key={bg}
            src={bg}
            alt=""
            className={`eb-home__hero-bg${i === bgIndex ? " is-on" : ""}`}
            width={832}
            height={740}
            draggable={false}
            decoding="async"
            loading="eager"
            fetchPriority={i === 0 ? "high" : "low"}
          />
        ))}
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
    </div>
  );
}
