import {
  ArrowLeft,
  ArrowRight,
  CreditCard,
  Wallet,
  Building2,
  Check,
  Globe,
  Rocket,
  ExternalLink,
} from 'lucide-react'
import { useLanguage } from '../../contexts/LanguageContext'
import { useSubjectSlot } from '../../contexts/SubjectSlotContext'
import { useEffect, useState } from 'react'
import { createContentPermission, saveFinalContent } from '../../services/firebaseService'
import { ensureUserQuota } from '../../services/quotaService'
import { saveContentToSupabase } from '../../services/supabaseContentService'
import { auth } from '../../config/firebase'

interface CheckoutScreenProps {
  onNavigate?: (screen: string) => void
  onBack?: () => void
}

declare global {
  interface Window {
    TossPayments: (clientKey: string) => {
      requestPayment: (
        method: string,
        options: {
          amount: number
          orderId: string
          orderName: string
          customerName: string
          successUrl: string
          failUrl: string
        },
      ) => Promise<void>
    }
  }
}

const PLANS = [
  { id: 'basic' as const, name: '베이직', price: 14900, desc: '월 10회 교체 / 5GB' },
  { id: 'premium' as const, name: '프리미엄', price: 29900, desc: '월 30회 교체 / 20GB' },
  { id: 'lifetime' as const, name: '평생 소장', price: 490000, desc: '월 30회 교체 / 50GB · 평생 보존' },
]

type PaymentMethod = 'tosspay' | 'card' | 'transfer'

const getAppOrigin = () =>
  typeof window !== 'undefined'
    ? (import.meta.env.VITE_APP_URL as string | undefined) || window.location.origin
    : ''

const isTelegramWebView = () =>
  typeof window !== 'undefined' &&
  !!(window as unknown as { Telegram?: { WebApp?: unknown } }).Telegram?.WebApp

export function CheckoutScreen({ onNavigate, onBack }: CheckoutScreenProps) {
  const { t } = useLanguage()
  const { setThemePaid } = useSubjectSlot()
  const [selectedPlan, setSelectedPlan] = useState<(typeof PLANS)[number]>(PLANS[1])
  const [selectedMethod, setSelectedMethod] = useState<PaymentMethod>('tosspay')

  const VAT = Math.floor(selectedPlan.price * 0.1)
  const TOTAL_AMOUNT = selectedPlan.price + VAT
  const appOrigin = getAppOrigin()
  const inTelegram = isTelegramWebView()

  useEffect(() => {
    const script = document.createElement('script')
    script.src = 'https://js.tosspayments.com/v1/payment'
    script.async = true
    document.body.appendChild(script)

    return () => {
      if (script.parentNode) {
        document.body.removeChild(script)
      }
    }
  }, [])

  const handlePaymentSuccess = async (orderId: string, amount: number) => {
    try {
      const userId = auth.currentUser?.uid ?? 'anonymous'
      await ensureUserQuota(userId, selectedPlan.id)
      const mainPhoto = localStorage.getItem('eternal_beam_main_photo') ?? ''
      const hologramVideoId =
        localStorage.getItem('eternal_beam_hologram_video_id') ??
        localStorage.getItem('eternal_beam_current_video_id') ??
        'video_unknown'
      const backgroundImage = localStorage.getItem('eternal_beam_background_image') ?? ''
      const backgroundThemeId = localStorage.getItem('eternal_beam_background_theme_id') ?? ''
      const backgroundThemeName = localStorage.getItem('eternal_beam_background_theme_name') ?? undefined
      const composedPreview = localStorage.getItem('eternal_beam_composed_preview') ?? ''
      const mainDepthUrl = localStorage.getItem('eternal_beam_depth_url') ?? null
      const mixedAudioBase64 = localStorage.getItem('eternal_beam_mixed_audio')
      const mixedAudioUrl = mixedAudioBase64
        ? `data:audio/wav;base64,${mixedAudioBase64}`
        : ''

      let contentId: string
      if (userId !== 'anonymous') {
        contentId = await createContentPermission(
          userId,
          orderId,
          amount,
          hologramVideoId,
        )
        localStorage.setItem('eternal_beam_current_content_id', contentId)
        console.log('✅ 결제 완료 & 권한 생성:', contentId)

        await saveFinalContent(contentId, {
          user_id: userId,
          main_photo_url: mainPhoto,
          hologram_video_id: hologramVideoId,
          main_depth_url: mainDepthUrl || null,
          background_image_url: backgroundImage,
          background_theme_id: backgroundThemeId,
          background_theme_name: backgroundThemeName,
          composed_preview_url: composedPreview,
          mixed_audio_url: mixedAudioUrl,
          payment_id: orderId,
          payment_status: 'completed',
          amount,
          nfc_slot_number: null,
          nfc_written: false,
          nfc_written_at: null,
          created_at: new Date().toISOString(),
        })

        await saveContentToSupabase({
          content_id: contentId,
          user_id: userId,
          main_photo_url: mainPhoto || null,
          hologram_video_id: hologramVideoId,
          main_depth_url: mainDepthUrl || null,
          video_url: null,
          background_image_url: backgroundImage || null,
          background_theme_id: backgroundThemeId || null,
          background_theme_name: backgroundThemeName ?? null,
          composed_preview_url: composedPreview || null,
          mixed_audio_url: mixedAudioUrl || null,
          payment_id: orderId,
          payment_status: 'completed',
          amount,
          nfc_slot_number: null,
          nfc_written: false,
          nfc_written_at: null,
        })
      } else {
        contentId = `content_${Date.now()}_demo`
        localStorage.setItem('eternal_beam_current_content_id', contentId)
        await saveContentToSupabase({
          content_id: contentId,
          user_id: 'anonymous',
          main_photo_url: mainPhoto || null,
          hologram_video_id: hologramVideoId,
          main_depth_url: mainDepthUrl || null,
          video_url: null,
          background_image_url: backgroundImage || null,
          background_theme_id: backgroundThemeId || null,
          background_theme_name: backgroundThemeName ?? null,
          composed_preview_url: composedPreview || null,
          mixed_audio_url: mixedAudioUrl || null,
          payment_id: orderId,
          payment_status: 'completed',
          amount,
          nfc_slot_number: null,
          nfc_written: false,
          nfc_written_at: null,
        })
      }

      if (composedPreview) {
        localStorage.setItem('eternal_beam_composed_preview', composedPreview)
      }

      setThemePaid(backgroundThemeId || '')
      const pendingThemeId = localStorage.getItem('eternal_beam_pending_theme_id')
      if (pendingThemeId) {
        setThemePaid(pendingThemeId)
        localStorage.removeItem('eternal_beam_pending_theme_id')
      }

      onNavigate?.('nfcPlayback')
    } catch (error: unknown) {
      const err = error as Error
      console.error('❌ 결제 후 처리 실패:', error)
      alert(
        '결제는 완료되었으나 권한 생성에 실패했습니다. 고객센터에 문의하세요.',
      )
    }
  }

  const handlePayment = async () => {
    const orderId = 'EB_' + new Date().getTime()

    try {
      const methodMap = {
        tosspay: '토스페이',
        card: '카드',
        transfer: '계좌이체',
      }

      const tossPayments = window.TossPayments(
        import.meta.env.VITE_TOSS_CLIENT_KEY ||
          'test_ck_D5GePWvyJnrK0W0k6q8gLzN97Eoq',
      )

      const successUrl = `${appOrigin}/payment/success`
      const failUrl = `${appOrigin}/payment/fail`
      await tossPayments.requestPayment(methodMap[selectedMethod], {
        amount: TOTAL_AMOUNT,
        orderId,
        orderName: selectedPlan.name + ' 플랜',
        customerName: '김지수',
        successUrl,
        failUrl,
      })

      await handlePaymentSuccess(orderId, TOTAL_AMOUNT)
    } catch (error) {
      console.error('결제 오류:', error)
      alert(
        '결제창을 열 수 없습니다. 결제 스크립트 로딩 또는 네트워크를 확인해주세요.',
      )
    }
  }

  const paymentMethods: { id: PaymentMethod; label: string; icon: typeof Wallet }[] = [
    { id: 'tosspay', label: '토스페이', icon: Wallet },
    { id: 'card', label: '카드', icon: CreditCard },
    { id: 'transfer', label: '계좌이체', icon: Building2 },
  ]

  return (
    <div className="relative h-full w-full overflow-y-auto bg-transparent">
      {/* Header */}
      <div className="glass sticky top-0 z-10 px-6 py-4 border-0 rounded-b-2xl">
        <div className="mb-2 flex items-center justify-between">
          <h1
            className="font-headline text-2xl font-bold"
            style={{ color: '#2d3748' }}
          >
            플랜 결제
          </h1>
          <button
            onClick={() => (onBack ? onBack() : onNavigate?.('preview'))}
            type="button"
            className="glass-strong flex h-10 w-10 items-center justify-center rounded-full shrink-0"
            title="뒤로"
          >
            <ArrowLeft className="h-5 w-5 text-[#667eea]" />
          </button>
        </div>
        <p className="text-sm" style={{ color: '#718096' }}>
          플랜을 선택하고 결제하세요
        </p>
      </div>

      {/* Plan Selection */}
      <div className="px-6 py-4 space-y-2">
        {PLANS.map((plan) => (
          <button
            key={plan.id}
            type="button"
            onClick={() => setSelectedPlan(plan)}
            className="w-full flex items-center justify-between rounded-2xl p-4 text-left transition-all"
            style={{
              border: `2px solid ${selectedPlan.id === plan.id ? '#667eea' : 'rgba(0,0,0,0.08)'}`,
              background: selectedPlan.id === plan.id ? 'rgba(102, 126, 234, 0.06)' : 'white',
            }}
          >
            <div>
              <p className="font-bold" style={{ color: '#2d3748' }}>{plan.name}</p>
              <p className="text-xs" style={{ color: '#718096' }}>{plan.desc}</p>
            </div>
            <p className="font-bold" style={{ color: '#667eea' }}>
              ₩{plan.price.toLocaleString()}
            </p>
          </button>
        ))}
      </div>

      {/* Product Card - Payment Method */}
      <div className="px-6 py-4">
        <div className="glass rounded-2xl p-6">

          {/* Payment Method Selection */}
          <div className="space-y-2">
            <p className="mb-2 text-sm font-semibold" style={{ color: '#4a5568' }}>
              결제 수단
            </p>
            {paymentMethods.map((method) => (
              <button
                key={method.id}
                onClick={() => setSelectedMethod(method.id)}
                className="flex w-full items-center gap-4 rounded-xl border-2 p-4 transition-all"
                style={{
                  borderColor:
                    selectedMethod === method.id ? '#667eea' : 'rgba(0, 0, 0, 0.1)',
                  background:
                    selectedMethod === method.id
                      ? 'rgba(102, 126, 234, 0.05)'
                      : 'transparent',
                }}
              >
                <method.icon
                  className="h-6 w-6"
                  style={{
                    color: selectedMethod === method.id ? '#667eea' : '#a0aec0',
                  }}
                />
                <span
                  className="flex-1 text-left font-semibold"
                  style={{
                    color: selectedMethod === method.id ? '#2d3748' : '#718096',
                  }}
                >
                  {method.label}
                </span>
                {selectedMethod === method.id && (
                  <Check className="h-5 w-5 text-[#667eea]" />
                )}
              </button>
            ))}
          </div>

          {/* Total Amount (VAT included) */}
          <div className="mt-6 rounded-xl p-4" style={{ background: 'rgba(102, 126, 234, 0.05)' }}>
            <div className="flex items-center justify-between">
              <span className="text-sm" style={{ color: '#718096' }}>
                총 금액 (VAT 포함)
              </span>
              <span className="text-xl font-bold" style={{ color: '#2d3748' }}>
                ₩{TOTAL_AMOUNT.toLocaleString()}
              </span>
            </div>
            <p className="mt-1 text-xs" style={{ color: '#a0aec0' }}>
              상품가 ₩{selectedPlan.price.toLocaleString()} + 부가세 ₩{VAT.toLocaleString()}
            </p>
          </div>
        </div>
      </div>

      {/* 펀딩으로 참여하기 */}
      <div className="px-6 py-4 pb-24">
        <p className="mb-3 text-sm font-semibold" style={{ color: '#4a5568' }}>
          펀딩으로 참여하기
        </p>
        <div className="grid grid-cols-2 gap-3">
          {import.meta.env.VITE_FUNDING_DOMESTIC_URL && (
            <a
              href={import.meta.env.VITE_FUNDING_DOMESTIC_URL}
              target="_blank"
              rel="noopener noreferrer"
              className="flex items-center gap-3 rounded-xl border-2 border-purple-200 p-4 transition-all hover:bg-purple-50"
            >
              <div
                className="flex h-10 w-10 shrink-0 items-center justify-center rounded-full"
                style={{ background: 'linear-gradient(135deg, #667eea 0%, #764ba2 100%)' }}
              >
                <Rocket className="h-5 w-5 text-white" />
              </div>
              <div className="min-w-0 flex-1 text-left">
                <p className="font-semibold text-gray-800">국내 펀딩</p>
                <p className="text-xs text-gray-500">와디즈·텀블벅·펀디브</p>
              </div>
            </a>
          )}
          {import.meta.env.VITE_FUNDING_GLOBAL_URL && (
            <a
              href={import.meta.env.VITE_FUNDING_GLOBAL_URL}
              target="_blank"
              rel="noopener noreferrer"
              className="flex items-center gap-3 rounded-xl border-2 border-cyan-200 p-4 transition-all hover:bg-cyan-50"
            >
              <div
                className="flex h-10 w-10 shrink-0 items-center justify-center rounded-full"
                style={{ background: 'linear-gradient(135deg, #06b6d4 0%, #3b82f6 100%)' }}
              >
                <Globe className="h-5 w-5 text-white" />
              </div>
              <div className="min-w-0 flex-1 text-left">
                <p className="font-semibold text-gray-800">해외 펀딩</p>
                <p className="text-xs text-gray-500">Kickstarter·Indiegogo</p>
              </div>
            </a>
          )}
        </div>
        {!import.meta.env.VITE_FUNDING_DOMESTIC_URL &&
          !import.meta.env.VITE_FUNDING_GLOBAL_URL && (
          <p className="rounded-xl bg-gray-100 px-4 py-3 text-center text-sm text-gray-500">
            펀딩 캠페인 URL을 .env에 설정하면 버튼이 표시됩니다
          </p>
        )}
      </div>

      {/* Payment Button */}
      <div
        className="fixed bottom-0 left-0 right-0 px-6 py-4"
        style={{
          background: 'rgba(255, 255, 255, 0.95)',
          backdropFilter: 'blur(20px)',
          borderTop: '1px solid rgba(0, 0, 0, 0.05)',
        }}
      >
        {inTelegram && (
          <div className="mb-3 rounded-xl border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-800">
            <p className="font-medium">텔레그램 앱에서는 결제창이 열리지 않을 수 있습니다.</p>
            <p className="mt-1 text-xs text-amber-700">
              아래 &quot;외부 브라우저에서 열기&quot;로 Safari/Chrome에서 열어 결제해 주세요.
            </p>
            <button
              type="button"
              onClick={() => {
                const openLink = (window as unknown as { Telegram?: { WebApp?: { openLink: (url: string) => void } } }).Telegram?.WebApp?.openLink
                if (openLink) openLink(window.location.href)
              }}
              className="mt-2 flex w-full items-center justify-center gap-2 rounded-lg border border-amber-300 bg-white py-2.5 text-sm font-medium text-amber-800"
            >
              <ExternalLink className="h-4 w-4" />
              외부 브라우저에서 열기
            </button>
          </div>
        )}
        <button
          onClick={handlePayment}
          type="button"
          className="flex w-full items-center justify-center gap-2 rounded-2xl py-4 font-bold text-white transition-all"
          style={{
            background: 'linear-gradient(135deg, #667eea 0%, #764ba2 100%)',
            boxShadow: '0 8px 24px rgba(102, 126, 234, 0.3)',
          }}
        >
          <CreditCard className="h-5 w-5" />
          ₩{TOTAL_AMOUNT.toLocaleString()} 결제하기
        </button>
      </div>
    </div>
  )
}
