import {
  ArrowRight,
  Heart,
  Download,
  Share2,
  Grid3x3,
} from 'lucide-react'
import { useLanguage } from '../../contexts/LanguageContext'

interface GalleryScreenProps {
  onNavigate?: (screen: string) => void
}

const mockHolograms = [
  {
    id: 1,
    name: '우리 강아지',
    theme: '크리스마스 원더',
    image: '🐕',
    favorite: true,
    date: '2026.02.10',
  },
  {
    id: 2,
    name: '여름 휴가',
    theme: '석양 해변',
    image: '🏖️',
    favorite: false,
    date: '2026.02.08',
  },
  {
    id: 3,
    name: '가족 사진',
    theme: '숲의 꿈',
    image: '👨‍👩‍👧‍👦',
    favorite: true,
    date: '2026.02.05',
  },
  {
    id: 4,
    name: '할머니',
    theme: '추모의 빛',
    image: '🕊️',
    favorite: false,
    date: '2026.02.01',
  },
  {
    id: 5,
    name: '결혼식',
    theme: '벚꽃 정원',
    image: '💑',
    favorite: true,
    date: '2026.01.25',
  },
  {
    id: 6,
    name: '졸업식',
    theme: '오로라 빛',
    image: '🎓',
    favorite: false,
    date: '2026.01.20',
  },
]

export function GalleryScreen({ onNavigate }: GalleryScreenProps) {
  const { t } = useLanguage()

  return (
    <div className="relative h-full w-full overflow-y-auto bg-transparent">
      {/* Header */}
      <div className="glass sticky top-0 z-10 px-6 py-4 border-0 rounded-b-2xl">
        <div className="mb-4 flex items-center justify-between">
          <h1
            className="font-headline text-2xl font-bold"
            style={{
              background: 'linear-gradient(135deg, #667eea 0%, #764ba2 100%)',
              WebkitBackgroundClip: 'text',
              WebkitTextFillColor: 'transparent',
              backgroundClip: 'text',
            }}
          >
            {t('gallery.title')}
          </h1>

          <div className="flex items-center gap-2">
            <button
              onClick={() => onNavigate?.('home')}
              className="glass-strong flex h-10 w-10 items-center justify-center rounded-full shrink-0"
            >
              <ArrowRight className="h-5 w-5 text-[#7C6B9B]" />
            </button>
            <button className="glass-strong flex h-10 w-10 items-center justify-center rounded-full">
              <Grid3x3 className="h-5 w-5 text-[#7C6B9B]" />
            </button>
          </div>
        </div>

        {/* Tabs */}
        <div className="flex items-center gap-2">
          <button
            className="rounded-full px-4 py-2 text-sm font-semibold"
            style={{
              background: 'linear-gradient(135deg, #667eea 0%, #764ba2 100%)',
              color: 'white',
              boxShadow: '0 4px 12px rgba(102, 126, 234, 0.3)',
            }}
          >
            {t('gallery.recent')}
          </button>
          <button
            className="rounded-full px-4 py-2 text-sm"
            style={{
              background: 'white',
              color: '#4a5568',
            }}
          >
            {t('gallery.favorites')}
          </button>
          <button
            className="rounded-full px-4 py-2 text-sm"
            style={{
              background: 'white',
              color: '#4a5568',
            }}
          >
            {t('gallery.all')}
          </button>
        </div>
      </div>

      {/* Content */}
      <div className="px-6 py-6 pb-24">
        <div className="grid grid-cols-2 gap-4">
          {mockHolograms.map((hologram) => (
            <div
              key={hologram.id}
              className="glass group relative cursor-pointer overflow-hidden rounded-2xl"
              onClick={() => onNavigate?.('preview')}
            >
              {/* Favorite Badge */}
              {hologram.favorite && (
                <div
                  className="absolute right-3 top-3 z-10 flex h-8 w-8 items-center justify-center rounded-full"
                  style={{
                    background: 'rgba(255, 255, 255, 0.9)',
                    boxShadow: '0 2px 8px rgba(0, 0, 0, 0.1)',
                  }}
                >
                  <Heart
                    className="h-4 w-4 fill-[#ef4444] text-[#ef4444]"
                  />
                </div>
              )}

              {/* Image */}
              <div
                className="relative flex h-40 items-center justify-center text-6xl"
                style={{
                  background:
                    'linear-gradient(135deg, rgba(102, 126, 234, 0.08) 0%, rgba(118, 75, 162, 0.08) 100%)',
                }}
              >
                {hologram.image}

                {/* Hover Overlay */}
                <div className="absolute inset-0 flex items-center justify-center gap-3 bg-black/50 opacity-0 transition-opacity group-hover:opacity-100">
                  <button
                    className="flex h-10 w-10 items-center justify-center rounded-full bg-white"
                  >
                    <Download className="h-5 w-5 text-[#667eea]" />
                  </button>
                  <button
                    className="flex h-10 w-10 items-center justify-center rounded-full bg-white"
                  >
                    <Share2 className="h-5 w-5 text-[#667eea]" />
                  </button>
                </div>
              </div>

              {/* Info */}
              <div className="p-3">
                <h4
                  className="mb-1 text-sm font-bold"
                  style={{ color: '#2d3748' }}
                >
                  {hologram.name}
                </h4>
                <p className="mb-1 text-xs" style={{ color: '#718096' }}>
                  {hologram.theme}
                </p>
                <p className="text-xs" style={{ color: '#a0aec0' }}>
                  {hologram.date}
                </p>
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  )
}
