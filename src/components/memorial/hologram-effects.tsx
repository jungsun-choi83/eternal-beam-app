/**
 * 홀로그램 배경 효과 (스캔라인 + Aurora)
 * CSS만 사용, fixed 배경 레이어
 */
export function HologramEffects() {
  return (
    <div className="hologram-bg" aria-hidden>
      <div className="hologram-scanline" />
      <div className="hologram-scanline-pattern" />
      <div className="hologram-aurora-1" />
      <div className="hologram-aurora-2" />
    </div>
  )
}
