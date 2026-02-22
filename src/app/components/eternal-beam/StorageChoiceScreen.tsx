import { ArrowLeft, HardDrive, Grid3x3, ChevronRight } from 'lucide-react'

interface StorageChoiceScreenProps {
  onNavigate?: (screen: string) => void
}

const STORAGE_HARDWARE = 'hardware'
const STORAGE_SLOT = 'slot'

export function StorageChoiceScreen({ onNavigate }: StorageChoiceScreenProps) {
  const handleSelect = (target: 'hardware' | 'slot') => {
    localStorage.setItem('eternal_beam_storage_target', target)
    onNavigate?.('preview')
  }

  return (
    <div className="relative h-full w-full overflow-y-auto bg-transparent">
      {/* Header */}
      <div className="glass sticky top-0 z-10 px-6 py-4 border-0 rounded-b-2xl">
        <div className="mb-2 flex items-center justify-between">
          <h1 className="font-headline text-2xl font-bold" style={{ color: '#2D2640' }}>
            저장 위치 선택
          </h1>
          <button
            onClick={() => onNavigate?.('record')}
            type="button"
            className="glass-strong flex h-10 w-10 items-center justify-center rounded-full shrink-0"
          >
            <ArrowLeft className="h-5 w-5 text-[#7C6B9B]" />
          </button>
        </div>
        <p className="text-sm" style={{ color: '#6B5B7A' }}>
          콘텐츠를 저장할 위치를 선택하세요
        </p>
      </div>

      {/* 선택 카드 */}
      <div className="px-6 py-6 space-y-4">
        <button
          onClick={() => handleSelect(STORAGE_HARDWARE as 'hardware')}
          type="button"
          className="glass-strong w-full flex items-center gap-4 rounded-2xl p-5 text-left transition-all hover:shadow-lg"
        >
          <div
            className="flex h-14 w-14 shrink-0 items-center justify-center rounded-2xl"
            style={{
              background: 'linear-gradient(135deg, #00d4ff 0%, #667eea 100%)',
            }}
          >
            <HardDrive className="h-7 w-7 text-white" />
          </div>
          <div className="flex-1 min-w-0">
            <p className="font-bold text-gray-800">하드웨어 저장</p>
            <p className="text-sm text-gray-500">프로젝터에 직접 저장 · QR로 연결</p>
          </div>
          <ChevronRight className="h-6 w-6 text-[#7C6B9B] shrink-0" />
        </button>

        <button
          onClick={() => handleSelect(STORAGE_SLOT as 'slot')}
          type="button"
          className="glass-strong w-full flex items-center gap-4 rounded-2xl p-5 text-left transition-all hover:shadow-lg"
        >
          <div
            className="flex h-14 w-14 shrink-0 items-center justify-center rounded-2xl"
            style={{
              background: 'linear-gradient(135deg, #B8A4D4 0%, #D4A89C 100%)',
            }}
          >
            <Grid3x3 className="h-7 w-7 text-white" />
          </div>
          <div className="flex-1 min-w-0">
            <p className="font-bold text-gray-800">슬롯 저장</p>
            <p className="text-sm text-gray-500">NFC 슬롯에 저장 · 결제 후 배경화면 사용</p>
          </div>
          <ChevronRight className="h-6 w-6 text-[#7C6B9B] shrink-0" />
        </button>
      </div>
    </div>
  )
}
