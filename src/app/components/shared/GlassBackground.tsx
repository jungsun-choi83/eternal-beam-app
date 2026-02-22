/**
 * 글래스모피즘 + 미래지향적 배경
 * 연보라·주황·핑크 블러 그라데이션 + 모션그래픽
 */
export function GlassBackground() {
  return (
    <div className="pointer-events-none fixed inset-0 overflow-hidden -z-10">
      {/* 5개 블러 오브 - 부드러운 움직임 */}
      <div
        className="absolute -left-32 -top-32 h-[520px] w-[520px] rounded-full opacity-65 blur-[110px] animate-[glassFloat1_10s_ease-in-out_infinite]"
        style={{
          background: 'linear-gradient(135deg, #E8D5F2 0%, #D4B5E8 50%, #F5D0C5 100%)',
        }}
      />
      <div
        className="absolute -bottom-40 -right-32 h-[580px] w-[580px] rounded-full opacity-55 blur-[110px] animate-[glassFloat2_13s_ease-in-out_infinite_0.8s]"
        style={{
          background: 'linear-gradient(135deg, #F5D0C5 0%, #F5E6D3 50%, #E8D5F2 100%)',
        }}
      />
      <div
        className="absolute left-1/2 top-1/3 h-[420px] w-[420px] -translate-x-1/2 rounded-full opacity-45 blur-[90px] animate-[glassFloat3_11s_ease-in-out_infinite_0.4s]"
        style={{
          background: 'linear-gradient(180deg, #E8D5F2 0%, #F5D0C5 100%)',
        }}
      />
      <div
        className="absolute right-1/4 top-1/4 h-[300px] w-[300px] rounded-full opacity-35 blur-[70px] animate-[glassFloat4_9s_ease-in-out_infinite_1.2s]"
        style={{
          background: 'linear-gradient(135deg, #F5E6D3 0%, #E8D5F2 100%)',
        }}
      />
      <div
        className="absolute bottom-1/3 left-1/4 h-[350px] w-[350px] rounded-full opacity-30 blur-[80px] animate-[glassFloat5_12s_ease-in-out_infinite_0.6s]"
        style={{
          background: 'linear-gradient(225deg, #D4B5E8 0%, #F5D0C5 100%)',
        }}
      />
      {/* 상단 밝기 그라데이션 */}
      <div
        className="absolute inset-0"
        style={{
          background: 'radial-gradient(ellipse 80% 50% at 50% 0%, rgba(255,255,255,0.5) 0%, transparent 55%)',
        }}
      />
      {/* 셔머 레이어 - 은은한 빛 스윕 */}
      <div
        className="absolute inset-0 opacity-[0.15] animate-[shimmerSweep_8s_ease-in-out_infinite]"
        style={{
          background: 'linear-gradient(105deg, transparent 0%, transparent 40%, rgba(255,255,255,0.6) 50%, transparent 60%, transparent 100%)',
          backgroundSize: '200% 100%',
        }}
      />
      <style>{`
        @keyframes glassFloat1 {
          0%, 100% { transform: translate(0, 0) scale(1) rotate(0deg); }
          33% { transform: translate(40px, -50px) scale(1.08) rotate(2deg); }
          66% { transform: translate(-20px, -20px) scale(0.98) rotate(-1deg); }
        }
        @keyframes glassFloat2 {
          0%, 100% { transform: translate(0, 0) scale(1) rotate(0deg); }
          33% { transform: translate(-45px, 30px) scale(1.1) rotate(-2deg); }
          66% { transform: translate(25px, -15px) scale(0.95) rotate(1deg); }
        }
        @keyframes glassFloat3 {
          0%, 100% { transform: translate(-50%, -50%) scale(1) rotate(0deg); }
          50% { transform: translate(-50%, -50%) scale(1.12) rotate(3deg); }
        }
        @keyframes glassFloat4 {
          0%, 100% { transform: translate(0, 0) scale(1); }
          50% { transform: translate(25px, 35px) scale(1.06); }
        }
        @keyframes glassFloat5 {
          0%, 100% { transform: translate(0, 0) scale(1); }
          50% { transform: translate(-30px, -25px) scale(1.05); }
        }
        @keyframes shimmerSweep {
          0%, 100% { background-position: 200% 0; }
          50% { background-position: -200% 0; }
        }
      `}</style>
    </div>
  )
}
