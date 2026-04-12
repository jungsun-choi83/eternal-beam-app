import { useEffect, useRef, useState } from 'react'
import { Image, Lock, Settings, Smartphone, Grid3x3 } from 'lucide-react'
import { useSubjectSlot } from '../../contexts/SubjectSlotContext'
import { useLanguage } from '../../contexts/LanguageContext'

interface HomeScreenProps {
  onNavigate?: (screen: string) => void
}

function gradientToDataUrl(gradient: string, size = 256): string {
  const canvas = document.createElement('canvas')
  canvas.width = size
  canvas.height = size
  const ctx = canvas.getContext('2d')
  if (!ctx) return ''
  const colorMatch = gradient.match(/#[a-fA-F0-9]{3,8}|rgb\([^)]+\)|rgba\([^)]+\)/g)
  const colors = colorMatch && colorMatch.length >= 1 ? colorMatch.map((c) => c.trim()) : ['#667eea', '#764ba2']
  const angle = (135 * Math.PI) / 180
  const x1 = size / 2 - (size / 2) * Math.cos(angle)
  const y1 = size / 2 - (size / 2) * Math.sin(angle)
  const x2 = size / 2 + (size / 2) * Math.cos(angle)
  const y2 = size / 2 + (size / 2) * Math.sin(angle)
  const grad = ctx.createLinearGradient(x1, y1, x2, y2)
  colors.forEach((color, i) => {
    grad.addColorStop(colors.length === 1 ? 0 : i / (colors.length - 1), color)
  })
  ctx.fillStyle = grad
  ctx.fillRect(0, 0, size, size)
  return canvas.toDataURL('image/jpeg', 0.9)
}

export function HomeScreen({ onNavigate }: HomeScreenProps) {
  const { t } = useLanguage()
  const {
    subjectImageUrl,
    setSubjectImageUrl,
    selectedThemeId,
    setSelectedThemeId,
    themes,
    canUseTheme,
    getTheme,
  } = useSubjectSlot()
  const [showCheckoutPopup, setShowCheckoutPopup] = useState<string | null>(null)
  const scrollRef = useRef<HTMLDivElement>(null)

  const mainPhoto =
    typeof localStorage !== 'undefined' ? localStorage.getItem('eternal_beam_main_photo') : null
  const displaySubjectUrl = subjectImageUrl || mainPhoto

  // 누끼 이미지 우선 (서버 없이 즉시 겹쳐 보이기용)
  const cutoutUrl = typeof localStorage !== 'undefined' ? localStorage.getItem('eternal_beam_cutout_url') : null
  const cutoutBase64 = typeof localStorage !== 'undefined' ? localStorage.getItem('eternal_beam_cutout_base64') : null
  const subjectImageForPreview = cutoutUrl || (cutoutBase64 ? `data:image/png;base64,${cutoutBase64}` : null) || displaySubjectUrl

  const selectedTheme = selectedThemeId ? getTheme(selectedThemeId) : themes[0]
  const previewBackgroundStyle = selectedTheme
    ? { background: selectedTheme.gradient }
    : { background: 'radial-gradient(circle at 50% 50%, rgba(30,30,50,0.95) 0%, #000 70%)' }

  useEffect(() => {
    const stored = localStorage.getItem('eternal_beam_main_photo')
    if (stored && stored !== subjectImageUrl) setSubjectImageUrl(stored)
  }, [mainPhoto, subjectImageUrl, setSubjectImageUrl])

  const handleThemeClick = (themeId: string) => {
    if (canUseTheme(themeId)) {
      setSelectedThemeId(themeId)
      const theme = getTheme(themeId)
      if (theme) {
        const dataUrl = gradientToDataUrl(theme.gradient)
        localStorage.setItem('eternal_beam_background_image', dataUrl)
        localStorage.setItem('eternal_beam_background_theme_id', theme.id)
        localStorage.setItem('eternal_beam_background_theme_name', theme.nameKo)
      }
    } else {
      setShowCheckoutPopup(themeId)
    }
  }

  const handlePhotoReplace = () => {
    localStorage.removeItem('eternal_beam_after_upload')
    onNavigate?.('photoUpload')
  }

  const startFlow = () => {
    if (!displaySubjectUrl) {
      onNavigate?.('photoUpload')
      return
    }
    onNavigate?.('preview')
  }

  return (
    <div
      className="relative flex h-full w-full flex-col overflow-hidden"
      style={{ background: '#000000' }}
    >
      {/* 상단 바 */}
      <div className="flex shrink-0 items-center justify-between px-4 py-3">
        <span className="text-sm font-medium text-white/70">Eternal Beam</span>
        <div className="flex items-center gap-2">
          <button
            onClick={() => onNavigate?.('settings')}
            className="rounded-full p-2 text-white/70 transition hover:bg-white/10 hover:text-white"
          >
            <Settings className="h-5 w-5" />
          </button>
        </div>
      </div>

      {/* 중앙: 피사체(아이) + 글로우 — 서버 호출 없이 CSS/레이어로 즉시 겹쳐 보이기 */}
      <div className="relative flex flex-1 flex-col items-center justify-center px-4">
        <div
          className="relative flex items-center justify-center rounded-3xl overflow-hidden"
          style={{
            width: '100%',
            maxWidth: 320,
            aspectRatio: '1',
            boxShadow:
              '0 0 60px rgba(0,212,255,0.25), 0 0 120px rgba(118,75,162,0.15), inset 0 0 40px rgba(255,255,255,0.05)',
            border: '1px solid rgba(255,255,255,0.08)',
            ...previewBackgroundStyle,
          }}
        >
          {subjectImageForPreview ? (
            <>
              {/* Layer 2: 누끼/피사체 이미지 — 테마 카드 클릭 시 배경만 바뀌어 즉시 반영 */}
              <img
                src={subjectImageForPreview}
                alt="피사체"
                className="h-full w-full object-contain object-center"
                style={{
                  mixBlendMode: 'screen',
                  filter: 'brightness(1.05) contrast(1.02)',
                }}
              />
            </>
          ) : (
            <div className="flex flex-col items-center justify-center gap-3 text-white/5">
              <Image className="h-20 w-20" />
              <span className="text-sm">사진을 등록해주세요</span>
            </div>
          )}
        </div>
        <button
          onClick={handlePhotoReplace}
          className="mt-4 flex items-center gap-2 rounded-full border border-white/20 bg-white/5 px-5 py-2.5 text-sm font-medium text-white/90 transition hover:bg-white/10"
        >
          <Image className="h-4 w-4" />
          사진 교체
        </button>
      </div>

      {/* 하단: 슬롯(테마) 가로 스크롤 */}
      <div className="shrink-0 border-t border-white/10 pb-6 pt-4">
        <p className="mb-3 px-4 text-xs font-medium uppercase tracking-wider text-white/50">
          테마 슬롯
        </p>
        <div
          ref={scrollRef}
          className="flex gap-3 overflow-x-auto px-4 pb-2 scrollbar-hide"
          style={{ scrollSnapType: 'x mandatory' }}
        >
          {themes.map((theme) => {
            const selected = selectedThemeId === theme.id
            const locked = theme.isPremium && !canUseTheme(theme.id)
            return (
              <button
                key={theme.id}
                type="button"
                onClick={() => handleThemeClick(theme.id)}
                className="relative shrink-0 snap-start rounded-2xl transition-all focus:outline-none focus:ring-2 focus:ring-cyan-400/50"
                style={{
                  width: 100,
                  height: 100,
                  background: theme.gradient,
                  boxShadow: selected
                    ? '0 0 24px rgba(0,212,255,0.4), inset 0 0 0 2px rgba(255,255,255,0.3)'
                    : '0 4px 20px rgba(0,0,0,0.3)',
                  border: selected ? '2px solid rgba(255,255,255,0.5)' : '2px solid transparent',
                }}
              >
                {locked && (
                  <div className="absolute inset-0 flex items-center justify-center rounded-2xl bg-black/50">
                    <Lock className="h-8 w-8 text-white" />
                  </div>
                )}
                <span
                  className="absolute bottom-1 left-1 right-1 truncate text-center text-xs font-medium text-white drop-shadow-md"
                  style={{ textShadow: '0 1px 2px rgba(0,0,0,0.5)' }}
                >
                  {theme.nameKo}
                </span>
              </button>
            )
          })}
        </div>
        <div className="mt-4 flex justify-center px-4">
          <button
            onClick={startFlow}
            className="w-full max-w-xs rounded-2xl py-3.5 text-sm font-bold text-white transition"
            style={{
              background: 'linear-gradient(135deg, #00d4ff 0%, #667eea 50%, #764ba2 100%)',
              boxShadow: '0 8px 32px rgba(0,212,255,0.35)',
            }}
          >
            {displaySubjectUrl ? 'NFC 슬롯에 저장하기' : '사진 올리고 시작하기'}
          </button>
        </div>
      </div>

      {/* 결제 팝업 (프리미엄 테마) */}
      {showCheckoutPopup && (
        <div
          className="absolute inset-0 z-50 flex items-center justify-center bg-black/80 p-4"
          onClick={() => setShowCheckoutPopup(null)}
        >
          <div
            className="w-full max-w-sm rounded-2xl border border-white/20 p-6"
            style={{ background: 'linear-gradient(180deg, #0f0f1a 0%, #1a1a2e 100%)' }}
            onClick={(e) => e.stopPropagation()}
          >
            <p className="mb-4 text-center text-white">
              프리미엄 테마는 결제 후 사용할 수 있습니다.
            </p>
            <div className="flex gap-3">
              <button
                onClick={() => setShowCheckoutPopup(null)}
                className="flex-1 rounded-xl border border-white/30 py-3 text-sm font-medium text-white"
              >
                취소
              </button>
              <button
                onClick={() => {
                  setShowCheckoutPopup(null)
                  onNavigate?.('checkout')
                }}
                className="flex-1 rounded-xl py-3 text-sm font-bold text-white"
                style={{
                  background: 'linear-gradient(135deg, #667eea 0%, #764ba2 100%)',
                }}
              >
                결제하기
              </button>
            </div>
          </div>
        </div>
      )}

      {/* 하단 네비 */}
      <div className="flex shrink-0 items-center justify-around border-t border-white/10 px-4 py-3">
        <button
          onClick={() => onNavigate?.('home')}
          className="flex flex-col items-center gap-1 text-cyan-400"
        >
          <Grid3x3 className="h-5 w-5" />
          <span className="text-xs font-medium">홈</span>
        </button>
        <button
          onClick={() => onNavigate?.('gallery')}
          className="flex flex-col items-center gap-1 text-white/50 hover:text-white/80"
        >
          <Image className="h-5 w-5" />
          <span className="text-xs">갤러리</span>
        </button>
        <button
          onClick={() => onNavigate?.('device')}
          className="flex flex-col items-center gap-1 text-white/50 hover:text-white/80"
        >
          <Smartphone className="h-5 w-5" />
          <span className="text-xs">기기</span>
        </button>
      </div>

      <style>{`
        .scrollbar-hide::-webkit-scrollbar { display: none; }
        .scrollbar-hide { -ms-overflow-style: none; scrollbar-width: none; }
      `}</style>
    </div>
  )
}
