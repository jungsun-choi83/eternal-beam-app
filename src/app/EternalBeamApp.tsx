import { useState, useEffect, useRef } from 'react'
import { createDisplayCutoutUrl } from '@/lib/display-image'
import { persistDeviceContentFromPipeline } from '@/lib/persist-device-content'
import { AnimatePresence, motion } from 'framer-motion'
import { AppShell } from '@/components/layout/app-shell'
import { DesktopNav, type DesktopNavKey } from '@/components/layout/desktop-nav'
import { MobileNav } from '@/components/layout/mobile-nav'
import { UserMenu } from '@/components/layout/user-menu'
import { AuthScreen } from '@/components/memorial/auth-screen'
import { GetStartedScreen } from '@/components/memorial/get-started-screen'
import { ResetPasswordScreen } from '@/components/memorial/reset-password-screen'
import { HomeScreen } from '@/components/memorial/home-screen'
import { MyLibraryScreen } from '@/components/memorial/my-library-screen'
import { GalleryScreen } from '@/components/memorial/gallery-screen'
import { PhotoUploadScreen } from '@/components/memorial/photo-upload-screen'
import {
  AIProcessingScreen,
  ETERNAL_BEAM_PIPELINE_KEY,
} from '@/components/memorial/ai-processing-screen'
import { ThemeSelectionScreen } from '@/components/memorial/theme-selection-screen'
import { CustomBackgroundScreen } from '@/components/memorial/custom-background-screen'
import { PreviewScreen } from '@/components/memorial/preview-screen'
import { ShippingAddressScreen } from '@/components/memorial/shipping-address-screen'
import { PhysicalOrderScreen } from '@/components/memorial/physical-order-screen'
import { NFCPlaybackScreen } from '@/components/memorial/nfc-playback-screen'
import { ForestExperienceScreen } from '@/components/memorial/forest-experience-screen'
import { MemorialDevicePlayScreen } from '@/components/memorial/memorial-device-play-screen'
import { DeviceScreen } from '@/components/memorial/device-screen'
import { SettingsScreen } from '@/components/memorial/settings-screen'
import { memorialT } from '@/components/memorial/memorial-i18n'
import {
  getMemorialTheme,
  getMemorialThemeByKey,
  CUSTOM_PHOTO_BG_THEME_ID,
  isPremiumTheme,
} from '@/components/memorial/themes'
import { clearStoredCustomBgVideoUrl } from '@/lib/custom-background-store'
import { finalizePreviewContent } from '@/lib/finalize-preview-content'
import { schedulePiDiscovery } from '@/lib/pi-sensor-bridge'
import { getPetName } from '@/lib/pet-profile'
import { billingReturnEntry, isPublicForestEntry, orderReturnEntry } from '@/lib/app-entry'
import { passwordRecoveryEntry } from '@/lib/password-recovery'
import { consumeSoulTracePendingUpload } from '@/lib/soul-trace-handoff'
import {
  clearBillingReturnState,
  readBillingReturnState,
  resolveBillingReturn,
  saveBillingReturnState,
} from '@/lib/billing-return-state'
import { BillingResultScreen } from '@/components/memorial/billing-result-screen'
import {
  DEVICE_DEMO_GOYA_CUTOUT,
  isDeviceKickstarterDemo,
} from '@/lib/device-demo-config'
import { FOREST_THEME_ID } from '@/lib/forest-demo-config'
import { inferMediaKind } from '@/lib/media-file-kind'
import {
  clearMainMedia,
  commitMainMedia,
  resolveOriginalPhoto,
  type MediaKind,
} from '@/lib/main-media-store'
import {
  clearAllPendingCutouts,
  getPendingCutoutMeta,
  readStoredPipeline,
} from '@/lib/pending-generation'
import { playPublishedOnBeam } from '@/lib/play-on-beam'
import { resolveSelectedThemeId } from '@/lib/theme-selection-store'
import {
  MAX_PET_SLOTS,
  alignActivePetSlot,
  capturePetSlotState,
  clearAllPetSlotState,
  clearPetSlotState,
  petSlotIdForIndex,
  readActivePetSlotIndex,
  readPetSlotSnapshot,
  readPetSlotValue,
  switchPetSlotState,
  writeActivePetSlotIndex,
  writeActivePetSlotSnapshot,
} from '@/lib/pet-slot-state'
import { resolveThemePurchaseReturnApply } from '@/lib/theme-purchase-return-apply'
import {
  clearThemePurchaseReturnState,
  readThemePurchaseReturnState,
} from '@/lib/theme-purchase-return-state'
import { OrderConfirmationScreen } from '@/components/memorial/order-confirmation-screen'
import { getEternalBeamPetId } from '@/lib/pet-identity'
import { traceImage } from '@/lib/image-trace' // [IMAGE-TRACE]
import type { PickedMedia } from '@/lib/pick-media-file'
import {
  beginPhase1Intake,
  clearPhase1Intake,
  identityForAddedPhotos,
  readPhase1Intake,
  type Phase1IntakeIdentity,
} from '@/lib/phase1-intake-session'
import { hasActiveGeneration, resolveResumeContentId } from '@/lib/generation-resume'
import {
  fetchPetInputsLocked,
  intakeContinueTarget,
  resolvePetInputsLocked,
} from '@/lib/pet-input-lock'
import { getPremiumAccessToken } from '@/lib/premium-auth-token'
import { hasLibraryFlowMarker } from '@/lib/library-flow-state'
import {
  matchingCutoutFallback,
  type ActiveCutoutIdentity,
} from '@/lib/durable-cutout-readiness'
import { useDurableCutoutReadiness } from '@/lib/use-durable-cutout-readiness'

type IntakeImageState = {
  status: 'pending' | 'uploading' | 'success' | 'error'
  error?: string | null
}

/**
 * 펫 한 마리분의 인테이크 상태. **이 타입 바깥에 펫별 상태를 두지 않는다.**
 *
 * `uploadedImages` 가 사진의 유일한 출처다 — 예전에 함께 들고 있던
 * `uploadedImage` 는 여기서 없앴다. 두 칸이 각자 갱신되다 어긋나면 화면은 A 를
 * 보여 주고 인테이크는 B 를 올린다. 첫 장이 필요한 곳은 `[0]` 에서 파생한다.
 *
 * 영상은 사진 배열에 섞지 않는다(누끼 인테이크 대상이 아니다). `mediaKind` 가
 * 이 슬롯이 사진인지 영상인지를 말하고, 전역 localStorage 는 더 이상 그 답을
 * 쥐고 있지 않다 — 그건 언제나 **마지막으로 만진 펫**의 답이었다.
 */
type PetIntakeSlot = {
  slotId: string
  mediaKind: MediaKind | null
  uploadedImages: string[]
  videoUrl: string | null
  intakeImageStates: IntakeImageState[]
  intakeIdentity: Phase1IntakeIdentity | null
  cutoutImage: string | null
  /**
   * 이 아이에게 고른 테마.
   *
   * 앱 전체에 하나였을 때는, 펫 2에 숲을 고른 뒤 펫 1로 돌아가면 화면은 숲을
   * 보여 주는데 저장소에는 펫 1의 테마가 들어 있었다 — 어느 쪽이 맞는지 답이
   * 두 개였다. 저장소 칸(theme_key·theme_id·background_*)도 함께 자리별로
   * 갈아 끼워진다(pet-slot-state).
   */
  selectedTheme: number | null
}

const MAX_IMAGES_PER_PET = 3

/** 슬롯 식별자는 자리에서 나온다(pet-slot-state) — 시계·난수가 아니다. */
function createPetIntakeSlot(index: number, seed?: Partial<PetIntakeSlot>): PetIntakeSlot {
  return {
    slotId: petSlotIdForIndex(index),
    mediaKind: seed?.mediaKind ?? null,
    uploadedImages: seed?.uploadedImages ?? [],
    videoUrl: seed?.videoUrl ?? null,
    intakeImageStates: seed?.intakeImageStates ?? [],
    intakeIdentity: seed?.intakeIdentity ?? null,
    cutoutImage: seed?.cutoutImage ?? null,
    selectedTheme: seed?.selectedTheme ?? null,
  }
}

/** 이 슬롯이 화면에 내보일 한 장(또는 한 편). 사진은 언제나 `[0]`. */
function slotPrimaryMedia(slot: PetIntakeSlot): string | null {
  if (slot.mediaKind === 'video') return slot.videoUrl
  return slot.uploadedImages[0] ?? null
}

/**
 * 새로고침을 건너 살아남는 슬롯의 몫.
 *
 * `videoUrl` 은 **일부러 뺐다** — `blob:` URL 은 문서가 사라지면 함께 폐기되므로,
 * 되살려 봐야 재생되지 않는 주소를 화면에 들려 줄 뿐이다. 영상 슬롯은 새로고침
 * 뒤 빈 자리로 돌아온다.
 */
type PersistedPetSlot = {
  /** 이 자리에 사진이 몇 장 있었는가. 내용이 아니라 **사실**만 남긴다. */
  imageCount: number
  /** 다시 불러올 수 있는 주소. 하나라도 없으면 통째로 비운다(아래 참고). */
  imageUrls: string[]
  /** 누끼 표시용 주소. 브라우저 누끼의 data: URL 은 담지 않는다. */
  cutoutUrl: string | null
}

/**
 * 다시 불러올 수 있는 주소만 남긴다.
 *
 * `data:` 와 `blob:` 은 **주소가 아니라 내용 그 자체**다. 원본 사진 한 장이 수
 * MB 라 세 마리분이면 세션 용량을 넘기고, 넘칠 때 잃는 것은 방금 만든 펫의
 * 파이프라인이다. 서버 누끼·업로드 결과처럼 진짜 주소가 있을 때만 담는다.
 */
function restorableMediaUrl(url: string | null | undefined): string | null {
  const value = (url || '').trim()
  if (!value) return null
  if (/^https?:\/\//i.test(value)) return value
  if (value.startsWith('/')) return value
  return null
}

/**
 * 장별 진행 상태와 사진 **내용**은 일부러 빼고, 가벼운 것만 적는다.
 *
 * 새로고침 뒤의 "업로드 중" 표시는 이미 끝난 실행의 잔상이라 거짓말이고,
 * 사진은 위 규칙대로 주소가 있을 때만 담는다. 주소가 없으면 `imageUrls` 가
 * 비고 `imageCount` 만 남는다 — 그 자리는 "사진을 다시 골라야 하는 자리"로
 * 돌아온다. 복원되지 않는 편이, 남의 사진이 돌아오는 것보다 낫다.
 */
function serializePetSlot(slot: PetIntakeSlot): string {
  const images = slot.mediaKind === 'video' ? [] : slot.uploadedImages.slice(0, MAX_IMAGES_PER_PET)
  const urls = images.map((url) => restorableMediaUrl(url))
  const persisted: PersistedPetSlot = {
    imageCount: images.length,
    imageUrls: urls.every((url): url is string => Boolean(url)) ? urls : [],
    cutoutUrl: restorableMediaUrl(slot.cutoutImage),
  }
  return JSON.stringify(persisted)
}

/**
 * 저장된 값을 **믿지 않고** 읽는다. 모양이 어긋나면 그 자리는 없는 것으로 친다 —
 * 반쯤 복원된 슬롯은 화면과 인테이크가 서로 다른 사진을 보는 상태다.
 */
function parsePetSlot(raw: string | null): PersistedPetSlot | null {
  if (!raw) return null
  try {
    const parsed = JSON.parse(raw) as unknown
    if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) return null
    const candidate = parsed as Partial<PersistedPetSlot>
    const imageUrls = Array.isArray(candidate.imageUrls)
      ? candidate.imageUrls
          .map((url) => restorableMediaUrl(typeof url === 'string' ? url : null))
          .filter((url): url is string => Boolean(url))
          .slice(0, MAX_IMAGES_PER_PET)
      : []
    const rawCount = Number(candidate.imageCount)
    const imageCount = Number.isInteger(rawCount)
      ? Math.max(0, Math.min(MAX_IMAGES_PER_PET, rawCount))
      : 0
    return {
      imageCount,
      imageUrls,
      cutoutUrl: restorableMediaUrl(
        typeof candidate.cutoutUrl === 'string' ? candidate.cutoutUrl : null,
      ),
    }
  } catch {
    return null
  }
}

/**
 * 새로고침 뒤 펫 1–3 을 **각자의 보관분에서** 되살린다.
 *
 * 자리는 이어져 있다(1 → 2 → 3). 중간이 비면 거기서 멈춘다 — 2번 없이 3번만
 * 되살리면 화면의 자리 번호와 보관함의 자리 번호가 어긋나고, 그 어긋남이 곧
 * 남의 파이프라인을 읽는 경로다.
 */
function restorePetSlots(activeIndex: number): PetIntakeSlot[] {
  const slots: PetIntakeSlot[] = []
  for (let index = 0; index < MAX_PET_SLOTS; index += 1) {
    const slotId = petSlotIdForIndex(index)
    const isActive = index === activeIndex
    const persisted = parsePetSlot(readPetSlotSnapshot(slotId, isActive))
    if (!persisted) break
    // 전부 되살릴 수 있을 때만 사진을 되살린다. 일부만 돌아오면 장 번호가
    // 인테이크 영수증의 레퍼런스 순서와 어긋나고, 그 어긋남은 조용하다.
    const images =
      persisted.imageCount > 0 && persisted.imageUrls.length === persisted.imageCount
        ? persisted.imageUrls
        : []
    const themeId = Number(readPetSlotValue(slotId, 'eternal_beam_theme_id', isActive))
    slots.push(
      createPetIntakeSlot(index, {
        mediaKind: images.length > 0 ? 'image' : null,
        uploadedImages: images,
        intakeImageStates: images.map(() => ({ status: 'pending' as const })),
        cutoutImage: persisted.cutoutUrl,
        // 신원·테마도 이 자리 앞으로 남은 것만 읽는다 — 다른 펫의 칸은 보지 않는다.
        intakeIdentity: readPhase1Intake(slotId),
        selectedTheme: Number.isFinite(themeId) && themeId > 0 ? themeId : null,
      }),
    )
  }
  return slots
}

type Screen =
  | 'getStarted'
  | 'signup'
  | 'login'
  | 'resetPassword'
  | 'home'
  | 'library'
  | 'gallery'
  | 'photoUpload'
  | 'aiProcessing'
  | 'themeSelection'
  | 'customBackground'
  | 'preview'
  | 'shippingAddress'
  | 'physicalOrder'
  | 'nfcPlayback'
  | 'forestExperience'
  | 'devicePlay'
  | 'device'
  | 'settings'
  | 'billingResult'
  | 'orderResult'

/**
 * 몰입형/집중형 화면에서는 데스크톱 상단 내비게이션(과 그 안의 UserMenu 언어
 * 전환)도 숨긴다 — 진입·인증·처리 중·숲/기기 재생 화면은 모바일에서도 다른
 * 크롬 없이 전체 화면으로 보여 주던 화면들이다. 진입(Get Started)은 언어
 * 전환을 헤더 구석에 갖고 있고, 인증 폼 위에는 떠 있는 토글이 없다.
 */
const DESKTOP_NAV_HIDDEN_SCREENS = new Set<Screen>([
  'getStarted',
  'signup',
  'login',
  'resetPassword',
  'aiProcessing',
  'forestExperience',
  'devicePlay',
  'nfcPlayback',
])

/** 데스크톱 내비게이션의 현재 활성 탭 — 화면 상태 기계는 그대로 두고 표시만 매핑한다. */
function activeDesktopNavKeyFor(screen: Screen): DesktopNavKey | undefined {
  switch (screen) {
    case 'home':
      return 'home'
    case 'library':
      return 'library'
    case 'photoUpload':
    case 'themeSelection':
    case 'customBackground':
    case 'preview':
    case 'shippingAddress':
    case 'physicalOrder':
      return 'create'
    case 'device':
      return 'device'
    case 'settings':
      return 'settings'
    default:
      return undefined
  }
}

/**
 * 인증 진입 화면(Get Started · 가입 · 로그인 · 비밀번호 재설정) — 앱 셸의 520px
 * 폰 폭 카드가 아니라 페이지 전체를 쓴다(데스크톱: 히어로 좌 / 폼 우 분할).
 * Home 과 같은 unframed.
 */
const AUTH_ENTRY_SCREENS = new Set<Screen>(['getStarted', 'signup', 'login', 'resetPassword'])

function resolveInitialScreen(): Screen {
  if (typeof window === 'undefined') return 'getStarted'
  // 비밀번호 재설정 링크 — 다른 durable 마커보다 먼저 본다. 방금 메일의
  // 링크를 누른 것은 가장 명시적인 사용자 행동이고, URL 해시는 supabase-js
  // 가 비동기로 처리하며 지운다(password-recovery.ts) — 여기서 동기로 먼저
  // 읽어야 그 전의 해시를 볼 수 있다.
  if (passwordRecoveryEntry()) return 'resetPassword'
  // Toss 결제 복귀 — 전용 경로가 없으면 결제 후 첫 화면(QR)으로 떨어져
  // 사용자가 방금 낸 돈의 결과를 볼 수 없다.
  if (billingReturnEntry()) return 'billingResult'
  // 실물 주문 결제 복귀도 **앱 안에서** 받는다. 예전에는 App.tsx 가 앱 셸 밖에서
  // 가로챘고, 그 화면을 나가는 유일한 길이 루트 새로고침이었다 — 루트는 아래
  // 폴백(getStarted)으로 떨어지므로 결제를 마친 고객이 진입 화면을 다시 봤다.
  if (orderReturnEntry()) return 'orderResult'
  // 테마 Toss 결제는 앱 바깥의 확인 화면을 거친 뒤 이 표식과 함께 루트로
  // 돌아온다. QR 온보딩 대신 방금 보던 테마 선택 화면을 복원한다.
  if (readThemePurchaseReturnState()) return 'themeSelection'
  // Soul Trace 편지를 막 가져왔다 — 다음은 아이를 만드는 단계다.
  // **기존 Upload Pet 흐름을 그대로 쓴다**(새 화면을 만들지 않는다).
  // 표식은 한 번만 소비되므로 다음 방문부터는 평소 진입 화면으로 돌아간다.
  if (consumeSoulTracePendingUpload()) return 'photoUpload'
  if (isPublicForestEntry()) return 'forestExperience'
  if (isDeviceKickstarterDemo()) return 'home'
  // Phase 7 새로고침 재개 — 확인을 눌러 generation-run 이 실제로 시작된
  // 적이 있으면(durable 마커, localStorage) 온보딩 대신 미리보기로 돌아간다.
  // 거기서 실행 상태를 다시 조회해 이어서 폴링하거나 완료/오류 상태를 그대로
  // 복원한다 — 처음부터 다시 만들지 않는다. 마커가 없으면(확인 전) 평소
  // 진입 화면(getStarted)으로 그대로 떨어진다.
  const resumeContentId = resolveResumeContentId()
  if (resumeContentId && hasActiveGeneration(resumeContentId)) return 'preview'
  // Phase 9 — My Library 새로고침 재개. sessionStorage 표식(탭 하나의 방문
  // 동안만 산다)이 있으면 그 탭에서는 이미 로그인 문턱을 넘은 뒤였다는 뜻이다
  // — 인증 확인을 기다리지 않고 곧장 라이브러리로 돌아간다. 실제 펫·모션은
  // MyLibraryScreen 이 서버 레지스트리와 대조한 뒤에만 되살린다.
  if (hasLibraryFlowMarker()) return 'library'
  // 첫 방문(세션 없음) — Get Started. 사용자가 고른다: 시작하기(가입) /
  // 이미 계정이 있어요(로그인). 자동 타이머 전환은 없다. 세션이 있으면 아래
  // 인증-인지 시작 효과가 이 화면을 건너뛰고 곧장 홈으로 보낸다.
  return 'getStarted'
}

type NavDirection = 'forward' | 'back'

const DEVICE_CONNECTED_KEY = 'eternal_beam_device_connected'


const pageEase = [0.22, 1, 0.36, 1] as const

type PageMotionCustom = { dir: NavDirection; duration: number }

const pageVariants = {
  initial: ({ dir }: PageMotionCustom) => ({
    opacity: 0,
    x: dir === 'forward' ? 28 : -28,
  }),
  animate: ({ duration }: PageMotionCustom) => ({
    opacity: 1,
    x: 0,
    transition: { duration, ease: pageEase },
  }),
  exit: ({ dir, duration }: PageMotionCustom) => ({
    opacity: 0,
    x: dir === 'forward' ? -28 : 28,
    transition: { duration, ease: pageEase },
  }),
}

function persistThemeChoice(themeId: number) {
  const th = getMemorialTheme(themeId)
  if (!th) return
  try {
    localStorage.setItem('eternal_beam_theme_key', th.themeKey)
    localStorage.setItem('eternal_beam_theme_id', String(themeId))
    localStorage.setItem('eternal_beam_background_theme_id', String(themeId))
    localStorage.setItem('eternal_beam_background_theme_name', th.nameKo || th.name)
  } catch {
    /* ignore */
  }
}

export function EternalBeamApp() {
  const [themePurchaseReturn] = useState(() => readThemePurchaseReturnState())
  const [screen, setScreen] = useState<Screen>(() => resolveInitialScreen())
  const [publicForestDemo] = useState(() => isPublicForestEntry())
  const [deviceDemo] = useState(() => isDeviceKickstarterDemo())
  // 새로고침 뒤에는 자리별 보관분에서 펫 1–3 을 각자 되살린다. 보관분이 전혀
  // 없을 때만 예전 경로(결제 복귀 한 마리 복원)로 떨어진다.
  const [restoredActiveIndex] = useState(() => readActivePetSlotIndex())
  const [petSlots, setPetSlots] = useState<PetIntakeSlot[]>(() => {
    const rehydrated = restorePetSlots(restoredActiveIndex)
    if (rehydrated.length > 0) return rehydrated

    const restored = themePurchaseReturn ? resolveOriginalPhoto() : null
    // 신원은 **복원된 사진과 함께일 때만** 되살린다. 사진 없이 신원만 물려받으면
    // 다음에 올리는 새 사진이 지난 세션 아이의 content_id 에 붙는다.
    const restoredIdentity = restored ? readPhase1Intake(petSlotIdForIndex(0)) : null
    const restoredCutout = themePurchaseReturn ? getPendingCutoutMeta()?.displayUrl ?? null : null
    return [
      createPetIntakeSlot(0, {
        mediaKind: restored ? 'image' : null,
        uploadedImages: restored ? [restored] : [],
        intakeIdentity: restoredIdentity,
        cutoutImage: restoredCutout,
        selectedTheme: getMemorialThemeByKey(themePurchaseReturn?.themeKey)?.id ?? null,
      }),
    ]
  })
  const [activePetSlotIndex, setActivePetSlotIndex] = useState(() =>
    Math.min(restoredActiveIndex, Math.max(0, petSlots.length - 1)),
  )
  // Phase 9 — 새로고침 재개 화면(위 resolveInitialScreen 의 'preview' 분기)은
  // 예전에는 항상 기본값(scale 1, 중앙)으로 열렸다. 결제 왕복 스냅샷(같은 펫일
  // 때만 유효, resolveBillingReturn 이 판정)이 있으면 같은 규칙으로 재사용한다
  // — 화면 종류와 무관하게 순수 판정이라 여기서도 안전하다.
  const [previewSettings, setPreviewSettings] = useState(() => {
    const restored = resolveBillingReturn(readBillingReturnState(), readStoredPipeline())
    return restored ? restored.settings : { scale: 1, posX: 0, posY: 0 }
  })
  const [language, setLanguage] = useState(() => {
    if (typeof window === 'undefined') return 'ko'
    return localStorage.getItem('eternal_beam_lang') || 'ko'
  })

  const handleLanguageChange = (lang: string) => {
    setLanguage(lang)
    localStorage.setItem('eternal_beam_lang', lang)
  }
  const [userName, setUserName] = useState<string | null>(null)
  const [, setIsFirstTime] = useState(true)
  const [tapFlash, setTapFlash] = useState(false)
  const navDirection = useRef<NavDirection>('forward')
  const pageTransitionSec = useRef(0.38)

  const pageMotionCustom = (): PageMotionCustom => ({
    dir: navDirection.current,
    duration: pageTransitionSec.current,
  })

  // Memorial 의 '크레딧 받기' → 설정으로 이동하면서 크레딧 섹션을 강조한다.
  const [focusMembership, setFocusMembership] = useState(false)

  // 설정에서 '뒤로' 를 눌렀을 때 돌아갈 화면.
  //
  // 예전에는 설정의 뒤로가 **항상 home 으로 하드코딩**돼 있었다. 그래서 Memorial 에서
  // 설정에 들어갔다 나오면 흐름의 처음으로 튕겨 나갔고(펫·테마·위치 상태는 남아 있지만
  // 그 화면으로 돌아갈 길이 없다), 크레딧을 충전하고 돌아와 잠금 해제하는 동선이
  // 그대로 끊겼다.
  //
  // qrBackTarget 과 같은 패턴이다 — 들어온 화면을 기억했다가 그리로 돌려보낸다.
  const [settingsBackTarget, setSettingsBackTarget] = useState<Screen | null>(null)

  const activePetSlot = petSlots[activePetSlotIndex] ?? petSlots[0] ?? createPetIntakeSlot(0)
  const activePetSlotId = activePetSlot.slotId
  const uploadedImages = activePetSlot.uploadedImages
  const uploadedImage = slotPrimaryMedia(activePetSlot)
  const activeMediaKind = activePetSlot.mediaKind
  // 인테이크(누끼 → Phase 1)는 **사진만** 처리한다. 영상 한 편으로 Start 를
  // 열어 주면 aiProcessing 이 처리할 게 없어 영원히 10% 에 멈춘다.
  const canStartIntake = uploadedImages.length > 0
  const intakeImageStates = activePetSlot.intakeImageStates
  const intakeIdentity = activePetSlot.intakeIdentity

  // ── 펫 입력 잠금 (Stage 1c) ───────────────────────────────────────────────
  // 생성이 시작된 아이의 사진은 바꿀 수 없다. 서버 답을 알면 그것이 정본이고
  // (실패한 실행 뒤 풀리는 것도 서버만 안다), 모를 때는 "확인을 눌렀다"는 로컬
  // 기록으로 곧바로 잠긴 화면을 보여 준다. 서버도 같은 변경을 PHASE1_LOCKED 로 막는다.
  const [serverInputsLocked, setServerInputsLocked] = useState<Record<string, boolean | null>>({})
  const lockPetId = intakeIdentity?.petId ?? null
  useEffect(() => {
    if (screen !== 'photoUpload' || !lockPetId) return
    let cancelled = false
    void (async () => {
      const auth = await getPremiumAccessToken()
      if (!auth.token) return
      const locked = await fetchPetInputsLocked({ petId: lockPetId, accessToken: auth.token })
      if (!cancelled) setServerInputsLocked((current) => ({ ...current, [lockPetId]: locked }))
    })()
    return () => {
      cancelled = true
    }
  }, [screen, lockPetId])
  const photosLocked = resolvePetInputsLocked({
    serverLocked: lockPetId ? serverInputsLocked[lockPetId] : null,
    localMarker: intakeIdentity ? hasActiveGeneration(intakeIdentity.contentId) : false,
    // 빈 자리는 잠기지 않는다 — 거기서 고르는 사진은 새 신원의 새 아이다.
    hasPhotos: uploadedImages.length > 0,
  })
  const cutoutImage = activePetSlot.cutoutImage
  const selectedTheme = activePetSlot.selectedTheme

  /**
   * Phase-1 누끼 준비 여부는 슬롯의 표시용 data URL 이 아니라 서버 레퍼런스
   * 대장이 정한다. 세션 영수증은 이미 알고 있는 cutout id 를 함께 고정해,
   * 같은 pet/content 라도 다른 레퍼런스 응답을 화면에 붙이지 못하게 한다.
   */
  const storedPipelineForCutout = readStoredPipeline()
  const pendingCutoutForDisplay = getPendingCutoutMeta()
  const activeCutoutIdentity: ActiveCutoutIdentity | null = (() => {
    const pipelineContentId = (storedPipelineForCutout?.content_id || '').trim()
    const pipelinePetId = (storedPipelineForCutout?.phase1_intake?.pet_id || '').trim()
    const slotContentId = (intakeIdentity?.contentId || '').trim()
    const slotPetId = (intakeIdentity?.petId || '').trim()
    const contentId = slotContentId || pipelineContentId || (pendingCutoutForDisplay?.contentId || '').trim()
    if (!contentId) return null
    const petId = slotPetId || pipelinePetId || `pet_${contentId}`
    const pipelineMatches =
      pipelineContentId === contentId && (!pipelinePetId || pipelinePetId === petId)
    return {
      petId,
      contentId,
      expectedCutoutReferenceId: pipelineMatches
        ? storedPipelineForCutout?.phase1_intake?.cutout_reference_id ?? null
        : null,
    }
  })()
  const { state: durableCutout, refresh: refreshDurableCutout } =
    useDurableCutoutReadiness(activeCutoutIdentity)
  const loadingCutoutFallback = matchingCutoutFallback(
    activeCutoutIdentity,
    storedPipelineForCutout,
    pendingCutoutForDisplay,
  )
  // Signed URLs remain runtime-only: never write them into PetIntakeSlot, whose snapshot
  // serializer accepts HTTP URLs. Only an existing data URL wins (to avoid a visible image
  // swap); an old HTTP URL must not outrank the freshly signed durable-object URL.
  const existingDataCutout = cutoutImage?.startsWith('data:') ? cutoutImage : null
  const effectiveCutoutImage =
    existingDataCutout ||
    (durableCutout.status === 'ready' ? durableCutout.displayUrl : null) ||
    (durableCutout.status === 'loading' ? cutoutImage || loadingCutoutFallback : null)

  // 홈 대시보드 "나의 반려" 카드용 — 슬롯 배열을 그대로 UI 모델로 쓰지 않고
  // 화면이 필요한 최소 형태(자리 번호 + 미리보기 한 장)로 추린다.
  const homePetSummaries = petSlots.map((slot, index) => ({
    index,
    previewImage: slot.cutoutImage ?? slot.uploadedImages[0] ?? null,
  }))

  // 결제 왕복 표식 적용 — 자리(petSlots/activePetSlotIndex)는 위 useState
  // 초기화에서 **이미** 복원돼 있다(동기 실행이라 이 effect보다 항상 먼저다).
  // 그 자리 위에 결제로 확정된 테마를 authoritative 하게 얹는다.
  //
  // 예전에는 여기서 persistThemeChoice 만 불렀다 — 레거시 전역 저장 칸만
  // 갱신되고 activePetSlot.selectedTheme(React state)는 그대로였다. 화면은
  // 다음 새로고침 전까지 결제 전 테마를 계속 보여 줬다. setSelectedTheme 을
  // 함께 불러야 두 갈래(React state·투영 저장)가 같은 값을 가리킨다.
  //
  // 표식은 **적용에 성공했을 때만** 지운다 — themeKey 를 못 찾으면 표식을
  // 남겨서 다음 로드에서도 다시 시도한다(확인된 결제를 조용히 잃지 않는다).
  useEffect(() => {
    const { themeId, clearMarker } = resolveThemePurchaseReturnApply(
      themePurchaseReturn,
      (themeKey) => getMemorialThemeByKey(themeKey)?.id,
    )
    if (themeId != null) {
      setSelectedTheme(themeId)
      persistThemeChoice(themeId)
    }
    if (clearMarker) clearThemePurchaseReturnState()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [themePurchaseReturn])

  // ── 결제 왕복 스냅샷 ───────────────────────────────────────────────────────
  // Toss 는 결제창을 마치면 페이지를 **이동**시킨다 → React state 가 사라진다.
  // 복원 가능한 화면(발행된 펫을 재생하는 preview, 또는 My Library)에 있는
  // 동안 계속 최신 스냅샷을 남겨 두면, 결제 후 돌아왔을 때 같은 맥락으로
  // 그대로 돌아갈 수 있다. 직접 Pi 송출 화면(devicePlay)은 더 이상 복원
  // 대상이 아니다 — 정상 결제 복귀가 그 화면으로 가면 안 된다.
  //
  // 결제 버튼 직전이 아니라 **화면에 있는 동안** 저장하는 이유: 결제 진입은
  // preview → 설정 → 시작하기로 두 단계라, 버튼 시점에는 이미 원래 화면을
  // 떠난 뒤다. 여기서 저장하면 그 경로와 무관하게 항상 정확하다.
  useEffect(() => {
    if (screen !== 'preview' && screen !== 'library') return
    saveBillingReturnState({
      screen,
      settings: previewSettings,
      contentId: readStoredPipeline()?.content_id ?? null,
    })
  }, [screen, previewSettings])

  /** 설정 열기. 돌아갈 화면을 함께 기억한다. */
  const openSettings = (from: Screen, options?: { focusMembership?: boolean }) => {
    setSettingsBackTarget(from)
    if (options?.focusMembership) setFocusMembership(true)
    navigateTo('settings')
  }

  /** 설정에서 뒤로. 기억한 화면이 없으면 예전 동작(home)을 유지한다. */
  const handleSettingsBack = () => {
    const target = settingsBackTarget ?? 'home'
    setSettingsBackTarget(null)
    setFocusMembership(false)
    navigateTo(target, 'back')
  }

  // 세션 복원 / 토큰 갱신 / 로그아웃 구독.
  //
  // 앱을 새로 열면 supabase-js 가 저장된 세션을 복원하고 INITIAL_SESSION 을 쏜다.
  // 그때 서버가 확정한 Eternal Beam 신원을 다시 받아 로컬과 맞춘다 — 이게 없으면
  // 새로고침 후 로컬 user_id 와 서버 신원이 갈라져 지갑·자산 조회가 어긋난다.
  // 여기서는 아무것도 구매하지 않는다(조회 한 번뿐).
  //
  // 표시 이름도 여기서 되살린다 — 가입 때 Supabase user_metadata(full_name)에
  // 넣은 값이다. 없으면 null 그대로(지어내지 않는다 → 홈 인사말은 생략된다).
  useEffect(() => {
    let unsubscribe = () => {}
    void import('@/lib/supabase-auth').then((m) => {
      void m.syncEternalBeamIdentity()
      unsubscribe = m.onAuthStateChange((signedIn, profile, isPasswordRecovery) => {
        // 재설정 링크도 유효한 세션을 세운다(signedIn = true) — 그러나 이건
        // "로그인 완료"가 아니다. 펫 등록 같은 정상 로그인 부수 효과를 여기서
        // 건너뛴다; ResetPasswordScreen 자신이 이 순간을 따로 잡는다.
        if (!signedIn || isPasswordRecovery) return
        if (profile?.name) setUserName(profile.name)
        void import('@/lib/pet-registry-api').then((registry) =>
          registry.ensureStoredReadyPetRegistered()
        )
      })
    })
    return () => unsubscribe()
  }, [])

  /**
   * Phase 9 — 인증을 아는 시작.
   *
   * resolveInitialScreen 은 동기 함수라 Supabase 세션(비동기)을 알지 못하고,
   * 다른 durable 마커(결제/주문/테마 복귀, 생성 재개)가 하나도 없으면 무조건
   * 'getStarted'(첫 방문 진입)로 떨어진다. 이미 로그인돼 있는 손님이
   * 새로고침할 때마다 진입/인증 화면을 다시 보게 되는 지점이 이것이다.
   *
   * 여기서는 그 폴백 화면일 때만(다른 마커가 이미 더 급한 화면을 골랐으면
   * 손대지 않는다) 세션을 확인해, 있으면 진입/인증을 건너뛰고 곧장 홈으로
   * 보낸다. 표시 이름은 위 세션 구독(INITIAL_SESSION)이 채운다.
   */
  useEffect(() => {
    if (screen !== 'getStarted') return
    let cancelled = false
    void import('@/lib/supabase-auth').then(async (m) => {
      const signedIn = await m.hasSession()
      if (cancelled || !signedIn) return
      navigateTo('home')
    })
    return () => {
      cancelled = true
    }
    // 마운트 시 1회만 — 로그아웃은 handleLogout 이 명시적으로 getStarted 로
    // 되돌리며, 그 경우 이 효과를 다시 태워 즉시 홈으로 튕기면 안 된다.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  useEffect(() => {
    if (!deviceDemo) return
    updatePetSlot(activePetSlotIndex, (slot) => ({ ...slot, cutoutImage: DEVICE_DEMO_GOYA_CUTOUT }))
    setSelectedTheme(FOREST_THEME_ID)
    try {
      localStorage.setItem('eternal_beam_background_theme_id', String(FOREST_THEME_ID))
      localStorage.setItem('eternal_beam_background_theme_name', '숲속')
    } catch {
      /* ignore */
    }
  }, [activePetSlotIndex, deviceDemo])

  // 직접 Pi LAN 탐색은 명시적 기기 데모(?demo=device)에서만 — 정상 사용자의
  // 기기 경로는 /api/v1/device/commands 게이트웨이 하나다.
  useEffect(() => {
    if (!deviceDemo) return
    try {
      if (localStorage.getItem(DEVICE_CONNECTED_KEY) !== '1') return
    } catch {
      return
    }
    schedulePiDiscovery(1200)
  }, [deviceDemo])

  /**
   * Phase 9 — 내비게이션 히스토리.
   *
   * 예전에는 화면 전환이 history 항목을 하나도 남기지 않았다. 그래서 물리
   * 뒤로가기는 이 SPA 안에서 "이전 화면"이라는 개념 자체가 없었다 — 첫 로드
   * 지점(대개 온보딩) 아니면 앱 밖으로 곧장 나갔다. 모든 전환마다(방향과
   * 무관하게 — in-app '뒤로' 버튼도 시간상으로는 새 지점이다) 항목을 하나씩
   * 쌓아 두면 물리 뒤로가기가 그 직전 화면으로 정확히 돌아간다(popstate
   * 구독, 아래 effect). URL 은 바꾸지 않는다 — 결제/주문/테마 복귀가 쓰는
   * 특정 경로(app-entry.ts)와 절대 부딪히지 않기 위해서다.
   */
  const navigateTo = (nextScreen: Screen, direction: NavDirection = 'forward') => {
    navDirection.current = direction
    pageTransitionSec.current = direction === 'forward' ? 0.38 : 0.32
    setTapFlash(true)
    window.setTimeout(() => setTapFlash(false), 150)
    setScreen(nextScreen)
    try {
      window.history.pushState({ ebScreen: nextScreen }, '')
    } catch {
      /* ignore (테스트/SSR 환경) */
    }
  }

  /** 되돌릴 수 없는 초기화(로그아웃·전체 리셋) 전용 — 새 항목을 쌓지 않고
   *  지금 자리를 덮어쓴다. 쌓으면 물리 뒤로가기가 방금 지운 상태로 돌아가려
   *  든다. */
  const replaceScreen = (nextScreen: Screen) => {
    setScreen(nextScreen)
    try {
      window.history.replaceState({ ebScreen: nextScreen }, '')
    } catch {
      /* ignore */
    }
  }

  /**
   * Phase 9 — 물리 뒤로/앞으로가기 구독.
   *
   * 마운트 시 지금 화면을 현재 history 항목에 태그해 둔다(그래야 아무 전환도
   * 하기 전에 뒤로가기를 눌러도 이 지점으로 돌아올 수 있다). 이후 popstate 는
   * 그 태그를 읽어 화면을 맞춘다 — navigateTo 를 다시 부르지 않는다(부르면
   * 새 history 항목을 또 쌓아 브라우저 히스토리와 우리 쪽 스택이 갈라진다).
   * 태그가 없는 항목(이 SPA 가 태그하기 전의 최초 진입점)까지 뒤로 가면 홈으로
   * — 빈 화면이나 미확정 상태로 떨어지는 것보다 낫다.
   */
  useEffect(() => {
    try {
      window.history.replaceState({ ebScreen: screen }, '')
    } catch {
      /* ignore */
    }
    const onPopState = (event: PopStateEvent) => {
      const target = (event.state as { ebScreen?: Screen } | null)?.ebScreen
      navDirection.current = 'back'
      pageTransitionSec.current = 0.32
      setScreen(target ?? 'home')
    }
    window.addEventListener('popstate', onPopState)
    return () => window.removeEventListener('popstate', onPopState)
    // 마운트 시 1회만 — screen 변화마다 재구독할 이유가 없다(setScreen 클로저
    // 문제 없음: 함수형 갱신이 필요 없는 단순 대입이라 최신 setter 만 있으면 된다).
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  /** 데스크톱 상단 탭 클릭 — 기존 화면들이 이미 쓰는 것과 같은 전환만 재사용한다. */
  const handleDesktopNav = (key: DesktopNavKey) => {
    if (key === 'home') return navigateTo('home')
    if (key === 'library') return navigateTo('library')
    if (key === 'device') return navigateTo('device')
    // settings 는 항상 openSettings 를 거쳐야 '뒤로'가 이 화면으로 돌아온다.
    if (key === 'settings') {
      if (screen !== 'settings') openSettings(screen)
      return
    }
    // 'create' — Home 히어로 "Create a Memory" 와 같은 규칙(startCreateMemory).
    startCreateMemory()
  }

  /** 사진도 영상도 없는 자리의 번호 — 없으면 null. */
  const firstEmptyPetSlotIndex = (): number | null => {
    const index = petSlots.findIndex((slot) => slot.uploadedImages.length === 0 && !slot.videoUrl)
    return index === -1 ? null : index
  }

  /**
   * Home "Create a Memory" / 데스크톱 'create' 탭 — 언제나 **업로드·펫 추가
   * 흐름**으로 간다(미리보기가 아니다; 만들던 아이의 미리보기는 Library 에서
   * 연다). 활성 자리가 비어 있으면 그 자리에, 아니면 비어 있는 자리를 골라
   * 쓰고, 그것도 없으면 새 자리를 만든다. 세 자리가 모두 차 있으면 활성 아이의
   * 업로드 화면으로 간다 — 자리를 지어내지 않는다.
   */
  const startCreateMemory = () => {
    const activeIsEmpty = uploadedImages.length === 0 && !activePetSlot.videoUrl
    if (!activeIsEmpty) {
      const emptyIndex = firstEmptyPetSlotIndex()
      if (emptyIndex !== null) {
        selectPetSlot(emptyIndex)
      } else if (petSlots.length < MAX_PET_SLOTS && addPetSlot() === null) {
        return
      }
    }
    navigateTo('photoUpload')
  }

  /**
   * Home "Add Pet" — 이미 비어 있는 자리가 있으면 그 자리를 다시 쓴다(예전에는
   * 무조건 새 자리를 만들어, 되돌아 나온 빈 자리가 유령처럼 남았다).
   */
  const startAddPet = () => {
    const emptyIndex = firstEmptyPetSlotIndex()
    if (emptyIndex !== null) {
      selectPetSlot(emptyIndex)
      navigateTo('photoUpload')
      return
    }
    const nextIndex = addPetSlot()
    if (nextIndex !== null) navigateTo('photoUpload')
  }

  /**
   * 슬롯 하나를 갱신한다. **여기에 저장·API 호출을 넣지 않는다.**
   *
   * 예전에는 `setPetSlots` 안에서 localStorage 를 쓰고 세션 키를 지웠다. React 는
   * 갱신 함수를 순수하다고 보고 필요하면 두 번 돌린다(StrictMode·동시 렌더) —
   * 그때마다 저장이 두 번 일어났고, 반대로 갱신이 버려져도 저장은 남았다.
   * 저장은 아래 투영 effect 와 각 핸들러의 **호출 시점**에서만 한다.
   */
  const updatePetSlot = (index: number, updater: (slot: PetIntakeSlot) => PetIntakeSlot) => {
    setPetSlots((prev) => {
      if (prev.length === 0) return [updater(createPetIntakeSlot(0))]
      return prev.map((slot, i) => (i === index ? updater(slot) : slot))
    })
  }

  /**
   * 테마 선택은 **활성 펫의 것**이다.
   *
   * 호출부는 예전 `setSelectedTheme` 를 그대로 쓴다 — 바뀐 것은 값이 어디에
   * 사는가뿐이다. 저장소 칸(theme_key·theme_id·background_*)은 자리 전환 때
   * pet-slot-state 가 함께 갈아 끼운다.
   */
  const setSelectedTheme = (themeId: number | null) => {
    updatePetSlot(activePetSlotIndex, (slot) => ({ ...slot, selectedTheme: themeId }))
  }

  /**
   * 활성 펫의 미디어를 저장소 칸(main_photo / main_video / media_type)에 투영한다.
   *
   * 펫을 바꾸면 이 effect 가 **새 펫의 답**으로 세 칸을 다시 쓴다. 새 펫이 아직
   * 아무것도 올리지 않았으면 비운다 — 남겨 두면 그건 직전 펫의 사진이고,
   * 테마 카드·미리보기 배경·장면 합성이 그걸 그대로 쓴다.
   */
  const projectedMediaRef = useRef<string | null>(null)
  useEffect(() => {
    const next = activeMediaKind && uploadedImage ? `${activeMediaKind}:${uploadedImage}` : ''
    if (projectedMediaRef.current === null && !next) {
      // 최초 마운트이고 활성 펫에 아직 아무것도 없다. 결제 복귀처럼 저장소에만
      // 남아 있는 상태를 여기서 지우면 복원이 끊긴다 — 아직 할 말이 없을 뿐이다.
      projectedMediaRef.current = ''
      return
    }
    if (projectedMediaRef.current === next) return
    projectedMediaRef.current = next
    if (activeMediaKind && uploadedImage) commitMainMedia(activeMediaKind, uploadedImage)
    else clearMainMedia()
  }, [activeMediaKind, uploadedImage])

  /**
   * 활성 펫의 화면 상태를 자리 앞으로 적어 둔다 — 그리고 보관함까지 곧바로.
   *
   * 보관은 전환을 기다리지 않는다. 기다리면 그 사이에 난 새로고침이 "마지막으로
   * 활성이던 자리"의 기록만 남기고 나머지를 잃는다. 여기서 매번 적어 두면
   * 보관함이 언제나 세 자리의 최신 상태다.
   */
  const persistedSlotRef = useRef<string | null>(null)
  useEffect(() => {
    const serialized = serializePetSlot(activePetSlot)
    if (persistedSlotRef.current === serialized) return
    persistedSlotRef.current = serialized
    writeActivePetSlotSnapshot(serialized)
    capturePetSlotState(activePetSlotId)
  }, [activePetSlot, activePetSlotId])
  // 위 비교는 직렬화 결과로 한다 — 장별 진행 상태만 바뀐 재렌더에서는 같은
  // 문자열이 나오므로 무거운 쓰기가 일어나지 않는다.

  /**
   * 새로고침 직후 — 모듈 메모리의 자리 표시를 복원된 자리에 맞춘다.
   *
   * 살아 있는 저장소 칸들은 이미 그 펫의 것이므로 다시 투영하지 않는다(결제
   * 복귀가 그 칸에만 남긴 상태를 덮어쓰지 않기 위해서다).
   */
  useEffect(() => {
    alignActivePetSlot(petSlotIdForIndex(restoredActiveIndex))
    // 최초 1회. 이후의 자리 이동은 selectPetSlot/addPetSlot 이 책임진다.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  /**
   * 펫 전환 — 저장소 칸을 **먼저** 갈아 끼우고 자리를 옮긴다.
   *
   * 파이프라인·대기 누끼·content_id 는 앱 전체에 한 칸씩뿐이다. 그 칸을 바꾸지
   * 않고 탭만 옮기면, 화면은 펫 1인데 미리보기·생성은 마지막으로 처리한 펫을
   * 쓴다 — "펫을 바꿨더니 이전 아이가 생성되는" 결함이 바로 이것이었다.
   */
  const selectPetSlot = (index: number) => {
    if (index < 0 || index >= petSlots.length) return
    if (index === activePetSlotIndex) return
    switchPetSlotState(activePetSlotId, petSlotIdForIndex(index))
    writeActivePetSlotIndex(index)
    persistedSlotRef.current = null
    setActivePetSlotIndex(index)
  }

  /**
   * 펫 추가 — 자리 번호로 결정된다.
   *
   * 예전에는 다음 자리 번호를 `setPetSlots` 갱신 함수 **안에서** 읽어 바깥
   * 변수에 적었다. 그 함수가 두 번 돌면 번호도 두 번 적혔고, 갱신이 버려지면
   * 잘못된 번호가 남았다. 이제 현재 배열 길이 하나로 결정한다.
   */
  const addPetSlot = (): number | null => {
    if (petSlots.length >= MAX_PET_SLOTS) {
      alert(language === 'en' ? 'You can add up to 3 pets.' : '최대 3마리까지 추가할 수 있어요.')
      return null
    }
    const nextIndex = petSlots.length
    const nextSlot = createPetIntakeSlot(nextIndex)
    // 새 자리는 **빈 자리**여야 한다. 같은 번호를 쓰던 앞선 세션의 보관분이
    // 남아 있으면 그 파이프라인이 새 아이의 것으로 둔갑한다.
    clearPetSlotState(nextSlot.slotId)
    clearPhase1Intake(nextSlot.slotId)
    switchPetSlotState(activePetSlotId, nextSlot.slotId)
    writeActivePetSlotIndex(nextIndex)
    persistedSlotRef.current = null
    setPetSlots((prev) => (prev.length >= MAX_PET_SLOTS ? prev : [...prev, nextSlot]))
    setActivePetSlotIndex(nextIndex)
    return nextIndex
  }

  /**
   * 업로드 확정 — **두 경로가 공유하는 단 하나의 지점.**
   *
   * 예전에는 홈 화면 선택기만 저장했고 업로드 화면은 React 상태만 갱신했다.
   * 그래서 업로드 화면으로 고른 사진은 eternal_beam_main_photo 에 들어가지
   * 않았고, 원본 배경을 읽는 화면들(테마 카드·미리보기 배경·장면 합성)은
   * 지난번 사진을 보거나 아무것도 보지 못했다.
   */
  const commitUpload = (url: string, kind: MediaKind) => {
    const slotId = activePetSlotId
    if (kind === 'image') {
      // 신원은 **이 슬롯 앞으로** 발급된다. 전역 한 칸이던 시절에는 펫 2의
      // 발급이 펫 1의 칸을 덮어써, 두 아이가 같은 pet_id 를 나눠 가졌다.
      const nextIdentity = beginPhase1Intake(slotId)
      updatePetSlot(activePetSlotIndex, (slot) => ({
        ...slot,
        mediaKind: 'image',
        uploadedImages: [url],
        videoUrl: null,
        intakeImageStates: [{ status: 'pending' }],
        intakeIdentity: nextIdentity,
      }))
      return
    }
    clearPhase1Intake(slotId)
    updatePetSlot(activePetSlotIndex, (slot) => ({
      ...slot,
      mediaKind: 'video',
      uploadedImages: [],
      videoUrl: url,
      intakeImageStates: [],
      intakeIdentity: null,
      cutoutImage: null,
    }))
  }

  /**
   * "원본 사진 그대로" 배경에 쓰이는 **단 한 장.**
   *
   * 여기서 한 번 정하고 화면들에 내려 준다. 예전에는 화면마다 각자
   * localStorage 를 읽어서, 저장이 늦거나 실패하면 카드·미리보기·생성이 서로
   * 다른 그림을 봤다.
   */
  // 영상은 원본 사진이 아니다 — 슬롯의 사진 배열 첫 장만 후보로 넘긴다.
  const originalPhoto = resolveOriginalPhoto(uploadedImages[0] ?? null)

  const handleImageUpload = (imageUrl: string, kind: MediaKind = 'image') => {
    commitUpload(imageUrl, kind)
    // 영상은 누끼 인테이크를 거치지 않는다. 홈 화면 선택기와 **같은 길**로
    // 보낸다 — 예전에는 업로드 화면에서 Start 가 열려 aiProcessing 으로 갔고,
    // 그 화면은 data:image/ 가 아닌 입력을 처리할 수 없어 그대로 멈춰 있었다.
    if (kind === 'video') navigateTo('themeSelection')
  }

  /**
   * 홈 화면 선택기(MediaFileTrigger) 경로 — 업로드 화면과 **같은 함수**
   * (commitUpload)로 저장한다. 저장 로직을 여기 다시 적지 않는다. 두 곳에
   * 적혀 있었을 때 한쪽이 main_photo 를 빠뜨렸고, 그 차이가 곧 "원본 사진이
   * 검게 비는" 결함이었다.
   */
  const applyPickedMedia = (picked: PickedMedia) => {
    const { file, kind } = picked
    const alerts = memorialT(language).alerts
    if (kind === 'video' && file.size > 100 * 1024 * 1024) {
      alert(alerts.videoSize)
      return
    }

    // [IMAGE-TRACE] 홈 화면 경로의 파일 선택 지점.
    void traceImage('file-selected (home picker)', file, 'original-upload', `kind=${kind}`)

    if (kind === 'image') {
      const reader = new FileReader()
      reader.onload = (ev) => {
        const result = String(ev.target?.result || '')
        if (!result) return
        void traceImage('state:uploadedImage', result, 'original-upload') // [IMAGE-TRACE]
        commitUpload(result, 'image')
        navigateTo('photoUpload')
      }
      reader.onerror = () => alert(alerts.fileType)
      reader.readAsDataURL(file)
      return
    }

    commitUpload(URL.createObjectURL(file), 'video')
    navigateTo('themeSelection')
  }

  const handleMediaFile = (file: File) => {
    const kind = inferMediaKind(file)
    if (!kind) {
      alert(memorialT(language).alerts.fileType)
      return
    }
    applyPickedMedia({ file, kind })
  }

  const readDataUrl = (file: File) =>
    new Promise<string>((resolve, reject) => {
      const reader = new FileReader()
      reader.onload = () => {
        const result = String(reader.result || '')
        if (!result) {
          reject(new Error('사진을 읽지 못했습니다.'))
          return
        }
        resolve(result)
      }
      reader.onerror = () => reject(new Error('사진을 읽지 못했습니다.'))
      reader.readAsDataURL(file)
    })

  /**
   * 새로 고른 사진을 **더한다** (덮어쓰지 않는다).
   *
   * 예전에는 선택할 때마다 배열을 통째로 갈아 끼웠다. 그래서 "한 장 고르고 또
   * 한 장" 이라는 가장 평범한 동작이 앞 장을 지웠고, 3장 인테이크는 한 번에
   * 세 장을 고른 사람만 쓸 수 있었다. 빈자리만큼만 받고, 같은 펫이므로
   * 업로드 신원(content_id)은 그대로 둔다.
   */
  const handleImagesUpload = async (files: File[]) => {
    if (photosLocked) return // 생성이 시작된 아이 — 사진을 더할 수 없다
    const slotIndex = activePetSlotIndex
    const slot = petSlots[slotIndex]
    if (!slot) return
    const slotId = slot.slotId
    const existing = slot.mediaKind === 'video' ? [] : slot.uploadedImages
    const room = MAX_IMAGES_PER_PET - existing.length
    if (room <= 0) {
      alert(
        language === 'en'
          ? `You can add up to ${MAX_IMAGES_PER_PET} photos per pet.`
          : `한 아이당 최대 ${MAX_IMAGES_PER_PET}장까지 올릴 수 있어요.`,
      )
      return
    }
    const selected = files.slice(0, room)
    if (selected.length === 0) return
    const urls = (await Promise.all(selected.map((file) => readDataUrl(file)))).filter(Boolean)
    if (urls.length === 0) return
    // 이미 사진이 있으면 **같은 아이에 장을 더하는 것**이므로 신원을 그대로 쓴다.
    // 비어 있으면 새 인테이크다 — 남아 있던 신원을 물려받지 않는다.
    // (빈 자리에 남아 있던 신원이 잠긴 아이의 것이어도 마찬가지다 — 새 사진은 새
    // content_id/pet_id 로 올라가고, 잠긴 아이는 손대지 않는다.)
    const identity = identityForAddedPhotos(slotId, existing.length, slot.intakeIdentity)
    updatePetSlot(slotIndex, (current) => {
      const base = current.mediaKind === 'video' ? [] : current.uploadedImages
      const nextImages = [...base, ...urls].slice(0, MAX_IMAGES_PER_PET)
      const pending: IntakeImageState = { status: 'pending' }
      return {
        ...current,
        mediaKind: 'image',
        uploadedImages: nextImages,
        videoUrl: null,
        intakeImageStates: nextImages.map((_, i) =>
          i < base.length ? current.intakeImageStates[i] ?? pending : pending,
        ),
        intakeIdentity: identity,
      }
    })
  }

  const handleRemoveUploadedImage = (index: number) => {
    if (photosLocked) return // 생성이 시작된 아이 — 사진을 뺄 수 없다
    const slotIndex = activePetSlotIndex
    const slot = petSlots[slotIndex]
    if (!slot) return
    const nextImages = slot.uploadedImages.filter((_, i) => i !== index)
    // 저장소 조작은 갱신 함수 **바깥**에서 한 번만.
    if (nextImages.length === 0) clearPhase1Intake(slot.slotId)
    updatePetSlot(slotIndex, (current) => ({
      ...current,
      mediaKind: nextImages.length > 0 ? 'image' : null,
      uploadedImages: nextImages,
      intakeImageStates: current.intakeImageStates.filter((_, i) => i !== index),
      intakeIdentity: nextImages.length > 0 ? current.intakeIdentity : null,
    }))
  }

  /**
   * 사진 한 장을 새 파일로 교체한다 — **자리와 신원은 그대로**.
   *
   * 이 아이의 신원(content_id/pet_id)은 슬롯 앞으로 발급된 것이지 사진 내용에서
   * 나온 게 아니다(phase1-intake-session) — 그래서 같은 자리의 사진 한 장을
   * 바꾸는 것은 새 인테이크가 아니다. 새로 추가([handleImagesUpload])·전부
   * 제거([handleRemoveUploadedImage])와 달리 신원을 새로 발급하거나 지우지
   * 않는다. 바뀐 장만 다시 처리 대상(pending)으로 되돌린다.
   */
  const handleReplaceUploadedImage = async (index: number, file: File) => {
    if (photosLocked) return // 생성이 시작된 아이 — 사진을 바꿀 수 없다
    const slotIndex = activePetSlotIndex
    const slot = petSlots[slotIndex]
    if (!slot || slot.mediaKind !== 'image') return
    if (index < 0 || index >= slot.uploadedImages.length) return
    const url = await readDataUrl(file)
    void traceImage('file-selected (replace)', file, 'original-upload', `index=${index}`) // [IMAGE-TRACE]
    updatePetSlot(slotIndex, (current) => {
      if (index >= current.uploadedImages.length) return current
      const nextImages = [...current.uploadedImages]
      nextImages[index] = url
      const nextStates = [...current.intakeImageStates]
      while (nextStates.length <= index) nextStates.push({ status: 'pending' })
      nextStates[index] = { status: 'pending' }
      return { ...current, uploadedImages: nextImages, intakeImageStates: nextStates }
    })
  }

  const handleIntakeImageStateForSlot = (slotIndex: number, index: number, state: IntakeImageState) => {
    updatePetSlot(slotIndex, (slot) => {
      const next = [...slot.intakeImageStates]
      while (next.length <= index) next.push({ status: 'pending' })
      next[index] = state
      return { ...slot, intakeImageStates: next }
    })
  }

  const handleAddPetFromUpload = () => {
    addPetSlot()
  }

  const handleSelectPetFromUpload = (index: number) => {
    selectPetSlot(index)
  }

  const handleAIProcessingComplete = async (slotIndex: number, cutoutUrl: string) => {
    const thumb = await createDisplayCutoutUrl(cutoutUrl, 640)
    updatePetSlot(slotIndex, (slot) => ({ ...slot, cutoutImage: thumb }))
    refreshDurableCutout()
    // 방금 쓰인 파이프라인/대기 누끼를 **이 슬롯 앞으로** 즉시 보관한다. 전환을
    // 기다리지 않는 이유: 그 사이 새로고침이 나면 어느 펫의 것인지 알 수 없다.
    capturePetSlotState(petSlots[slotIndex]?.slotId ?? petSlotIdForIndex(slotIndex))
    navigateTo('themeSelection')
  }

  const handleThemeSelect = (themeId: number) => {
    setSelectedTheme(themeId)
    persistThemeChoice(themeId)
  }

  // "내 사진으로 나만의 배경 만들기" 카드 탭 — 일반 테마 선택과 달리 결제 전에
  // 생성 화면(customBackground)을 먼저 거친다(생성이 끝나야 미리보기/결제가 가능).
  const handleSelectCustomBackground = () => {
    navigateTo('customBackground')
  }

  const handleCustomBackgroundComplete = () => {
    setSelectedTheme(CUSTOM_PHOTO_BG_THEME_ID)
    persistThemeChoice(CUSTOM_PHOTO_BG_THEME_ID)
    navigateTo('themeSelection', 'back')
  }

  const handleThemeContinue = (themeId: number) => {
    // Theme Selection is web-only state — it must never talk to the physical
    // Beam. Only an explicit "Play on Beam" press may send theme_play.
    setSelectedTheme(themeId)
    persistThemeChoice(themeId)
    navigateTo('preview')
  }

  const handlePreviewSettingsChange = (settings: {
    scale: number
    posX: number
    posY: number
  }) => {
    setPreviewSettings(settings)
  }

  /** 모든 펫의 인테이크 흔적을 지운다 — 한 마리만 지우면 나머지가 유령이 된다. */
  const clearAllPetIntakeState = () => {
    try {
      sessionStorage.removeItem(ETERNAL_BEAM_PIPELINE_KEY)
    } catch {
      /* ignore */
    }
    clearStoredCustomBgVideoUrl()
    clearPhase1Intake()
    clearAllPetSlotState()
    clearAllPendingCutouts()
    clearMainMedia()
    projectedMediaRef.current = ''
    persistedSlotRef.current = null
    writeActivePetSlotIndex(0)
    setPetSlots([createPetIntakeSlot(0)])
    setActivePetSlotIndex(0)
  }

  const handleReset = () => {
    // 테마는 슬롯에 살고, clearAllPetIntakeState 가 슬롯을 새로 만든다.
    clearAllPetIntakeState()
    replaceScreen('home')
    setPreviewSettings({ scale: 1, posX: 0, posY: 0 })
  }

  const handleLogout = () => {
    setIsFirstTime(true)
    setUserName(null)
    clearAllPetIntakeState()
    setPreviewSettings({ scale: 1, posX: 0, posY: 0 })
    replaceScreen('getStarted')
    // Phase 9 — 로컬 상태만 지우고 실제 Supabase 세션은 그대로 두던 결함.
    // 그 상태로는 새로고침하는 순간 위 인증-인지 시작 효과가 세션을 다시
    // 찾아내 곧장 로그인 화면으로 되돌린다 — 로그아웃이 조용히 무효화된다.
    void import('@/lib/supabase-auth').then((m) => m.signOut())
  }

  /** Get Started → 회원가입. (기기 데모 경로는 예전 스플래시가 하던 Pi 탐색 예약을 그대로 잇는다.) */
  const handleGetStarted = () => {
    if (deviceDemo) {
      try {
        localStorage.setItem(DEVICE_CONNECTED_KEY, '1')
      } catch {
        /* ignore */
      }
      schedulePiDiscovery(600)
    }
    navigateTo('signup')
  }

  /** 이미 계정이 있어요 → 로그인. */
  const handleGoToSignIn = () => {
    navigateTo('login')
  }

  /**
   * 결제(성공·실패·취소) 후 복귀.
   *
   * 스냅샷이 유효하면 **원래의 현대 화면**(preview 또는 library)으로 돌려보낸다.
   * 새 펫을 만들지 않고, 업로드·누끼·BREATHING 생성을 다시 시작하지도 않는다 —
   * 파이프라인과 테마는 이미 저장소에 있고 화면들이 스스로 읽는다. 여기서
   * 되살리는 것은 React state 가 잃어버린 두 가지(화면·펫 위치)뿐이다.
   *
   * 멤버십 자격은 화면이 마운트되면서 다시 조회된다 — preview/library 는
   * PremiumAssetsProvider 가, 설정은 MembershipSection 이 마운트 시 GET 한다.
   *
   * 스냅샷이 없거나 펫이 바뀌었으면 설정(멤버십 섹션 강조)으로 간다 — 틀린 펫을
   * 복원하는 것보다 낫다. 직접 Pi 송출 화면(devicePlay)으로는 절대 가지 않는다.
   */
  const handleBillingReturn = () => {
    window.history.replaceState({}, '', '/')
    const restored = resolveBillingReturn(readBillingReturnState(), readStoredPipeline())
    clearBillingReturnState()

    if (restored) {
      setPreviewSettings(restored.settings)
      // 멤버십은 화면이 마운트되면서 PremiumAssetsProvider 가 다시 조회한다 —
      // 여기서 따로 부르지 않는다(조회 경로를 두 개로 만들지 않기 위해).
      navigateTo(restored.screen)
      return
    }
    openSettings('home', { focusMembership: true })
  }

  /**
   * 실물 주문 결제 후 — **방금 산 그 아이에게 돌아간다.**
   *
   * 고객은 이미 로그인돼 있고 canonical 펫도 이미 있다. 그런데 예전에는 확인
   * 화면을 나가는 길이 `window.location.replace('/')` 하나뿐이었고, 루트는
   * resolveInitialScreen 의 폴백인 'getStarted'(진입 → 회원가입 →
   * 사진 업로드)으로 떨어졌다. 돈을 낸 직후에 온보딩을 다시 보게 되는 것이다.
   *
   * 복원은 구독 복귀와 **같은 스냅샷**을 쓴다(billing-return-state). 결제 왕복
   * 중에 화면 상태를 붙잡아 두는 문제는 이미 그쪽에서 풀렸고, 두 벌로 만들면
   * 하나가 갱신되고 다른 하나가 잊힌다. 스냅샷은 preview/library 에 머무는
   * 동안 계속 저장되므로(위 effect), 결제 진입 경로와 무관하게 항상 최신이다.
   *
   * ── 펫을 새로 만들지 않는다 ─────────────────────────────────────────────
   * 되살리는 것은 React state 가 잃어버린 두 가지(화면·펫 위치)뿐이다.
   * 누끼·테마·파이프라인은 이미 저장소에 있고 화면들이 스스로 읽는다.
   *
   * 스냅샷이 없거나 펫이 바뀌었으면 **기념품 화면**으로 간다 — 주문을 확인할 수
   * 있는 곳이고, 고객 상태를 초기화하지 않는다. 온보딩으로는 절대 보내지 않는다.
   */
  const handleOrderReturn = () => {
    window.history.replaceState({}, '', '/')
    const restored = resolveBillingReturn(readBillingReturnState(), readStoredPipeline())
    clearBillingReturnState()

    if (restored) {
      setPreviewSettings(restored.settings)
      navigateTo(restored.screen)
      return
    }
    navigateTo('physicalOrder')
  }

  // Toss 결제 복귀는 **앱 셸 밖에서** 처리한다. 결제 결과는 업로드·테마 등 어떤
  // 진행 상태와도 무관하고, 여기서 애니메이션·파이프라인 복원을 태울 이유가 없다.
  if (screen === 'orderResult') {
    return (
      <main className="min-h-[100dvh] bg-[var(--eb-bg)] text-[var(--eb-text)] flex items-stretch md:items-center justify-center p-0 md:p-4 overflow-hidden">
        <OrderConfirmationScreen
          onContinue={handleOrderReturn}
          onViewOrders={() => {
            window.history.replaceState({}, '', '/')
            clearBillingReturnState()
            navigateTo('physicalOrder')
          }}
        />
      </main>
    )
  }

  if (screen === 'billingResult') {
    return (
      <main className="w-full min-h-[100dvh] bg-[var(--eb-bg)] text-[var(--eb-text)] overflow-hidden">
        <BillingResultScreen
          outcome={billingReturnEntry() === 'fail' ? 'fail' : 'success'}
          language={language}
          onContinue={handleBillingReturn}
        />
      </main>
    )
  }

  return (
    <main className="w-full min-h-[100dvh] bg-[var(--eb-bg)] text-[var(--eb-text)] overflow-hidden">

      <AppShell
        wide={screen === 'home' || screen === 'photoUpload' || screen === 'themeSelection' || screen === 'preview' || screen === 'device' || screen === 'library' || screen === 'settings'}
        unframed={screen === 'home' || AUTH_ENTRY_SCREENS.has(screen)}
        nav={
          !DESKTOP_NAV_HIDDEN_SCREENS.has(screen) ? (
            <DesktopNav
              active={activeDesktopNavKeyFor(screen)}
              onNavigate={handleDesktopNav}
              onHome={() => navigateTo('home')}
              right={
                <UserMenu
                  userName={userName ?? undefined}
                  settingsLabel={memorialT(language).settings.title}
                  logoutLabel={memorialT(language).settings.logout}
                  onSettings={() => { if (screen !== 'settings') openSettings(screen) }}
                  onLogout={handleLogout}
                  language={language}
                  onChangeLanguage={handleLanguageChange}
                />
              }
            />
          ) : undefined
        }
        mobileNav={
          !DESKTOP_NAV_HIDDEN_SCREENS.has(screen) ? (
            <MobileNav active={activeDesktopNavKeyFor(screen)} onNavigate={handleDesktopNav} />
          ) : undefined
        }
      >
        {tapFlash ? (
          <div
            className="eb-tap-flash pointer-events-none absolute inset-0 z-[80] rounded-[inherit]"
            aria-hidden
          />
        ) : null}
        <AnimatePresence mode="wait" initial={false}>
          {screen === 'signup' && (
            <motion.div
              key="signup"
              custom={pageMotionCustom()}
              variants={pageVariants}
              initial="initial"
              animate="animate"
              exit="exit"
              className="h-full"
            >
              <AuthScreen
                language={language}
                onLanguageChange={handleLanguageChange}
                initialMode="signup"
                onAuthComplete={(name?: string, completedMode?: 'login' | 'signup') => {
                  if (name) setUserName(name)
                  navigateTo(completedMode === 'login' ? 'home' : 'photoUpload')
                }}
              />
            </motion.div>
          )}

          {screen === 'login' && (
            <motion.div
              key="login"
              custom={pageMotionCustom()}
              variants={pageVariants}
              initial="initial"
              animate="animate"
              exit="exit"
              className="h-full"
            >
              <AuthScreen
                language={language}
                onLanguageChange={handleLanguageChange}
                initialMode="login"
                onAuthComplete={(name?: string, completedMode?: 'login' | 'signup') => {
                  if (name) setUserName(name)
                  navigateTo(completedMode === 'signup' ? 'photoUpload' : 'home')
                }}
              />
            </motion.div>
          )}

          {screen === 'resetPassword' && (
            <motion.div
              key="resetPassword"
              custom={pageMotionCustom()}
              variants={pageVariants}
              initial="initial"
              animate="animate"
              exit="exit"
              className="h-full"
            >
              <ResetPasswordScreen language={language} onDone={() => replaceScreen('login')} />
            </motion.div>
          )}

          {screen === 'getStarted' && (
            <motion.div
              key="getStarted"
              custom={pageMotionCustom()}
              variants={pageVariants}
              initial="initial"
              animate="animate"
              exit="exit"
              className="h-full"
            >
              <GetStartedScreen
                language={language}
                onLanguageChange={handleLanguageChange}
                onGetStarted={handleGetStarted}
                onSignIn={handleGoToSignIn}
              />
            </motion.div>
          )}

          {screen === 'home' && (
            <motion.div
              key="home"
              custom={pageMotionCustom()}
              variants={pageVariants}
              initial="initial"
              animate="animate"
              exit="exit"
              className="h-full"
            >
              <HomeScreen
                petName={getPetName() || undefined}
                pets={homePetSummaries}
                activePetIndex={activePetSlotIndex}
                userName={userName ?? undefined}
                language={language}
                onMediaFile={handleMediaFile}
                onSelectPet={selectPetSlot}
                onAddPet={startAddPet}
                onLibrary={() => navigateTo('library')}
                onDevice={() => navigateTo('device')}
                onSettings={() => openSettings('home')}
                onTryForest={
                  deviceDemo ? () => navigateTo('forestExperience') : undefined
                }
                onSaveToNFC={() => {
                  // 기기 데모(킥스타터)만 예외 — 준비된 누끼를 곧장 기기에서 재생.
                  if (deviceDemo && cutoutImage) {
                    setSelectedTheme(FOREST_THEME_ID)
                    navigateTo('devicePlay')
                    return
                  }
                  startCreateMemory()
                }}
              />
            </motion.div>
          )}

          {screen === 'gallery' && (
            <motion.div
              key="gallery"
              custom={pageMotionCustom()}
              variants={pageVariants}
              initial="initial"
              animate="animate"
              exit="exit"
              className="h-full"
            >
              <GalleryScreen
                onSelectItem={(id: number) => console.log('Selected item', id)}
                onAddNew={() => {
                  addPetSlot()
                  navigateTo('photoUpload')
                }}
                onBack={() => navigateTo('home', 'back')}
              />
            </motion.div>
          )}

          {screen === 'library' && (
            <motion.div
              key="library"
              custom={pageMotionCustom()}
              variants={pageVariants}
              initial="initial"
              animate="animate"
              exit="exit"
              className="h-full"
            >
              <MyLibraryScreen
                language={language}
                onBack={() => navigateTo('home', 'back')}
                onComplete={() => navigateTo('home')}
                onOpenMembership={() => openSettings('library', { focusMembership: true })}
                onCreateNew={() => {
                  addPetSlot()
                  navigateTo('photoUpload')
                }}
              />
            </motion.div>
          )}

          {screen === 'photoUpload' && (
            <motion.div
              key="photoUpload"
              custom={pageMotionCustom()}
              variants={pageVariants}
              initial="initial"
              animate="animate"
              exit="exit"
              className="h-full"
            >
              <PhotoUploadScreen
                uploadedImage={uploadedImage}
                uploadedImages={uploadedImages}
                canStart={canStartIntake}
                // 미디어 종류는 **활성 펫의 상태**에서 온다. 예전에는 화면이
                // 전역 localStorage 를 읽어, 직전 펫이 영상이면 새 펫의 사진을
                // 비디오 플레이어로 그렸다.
                mediaType={activeMediaKind}
                petSlotsCount={petSlots.length}
                activePetSlotIndex={activePetSlotIndex}
                maxPetSlots={MAX_PET_SLOTS}
                onSelectPetSlot={handleSelectPetFromUpload}
                onAddPetSlot={handleAddPetFromUpload}
                language={language}
                onImageUpload={handleImageUpload}
                onImagesUpload={handleImagesUpload}
                onRemoveImage={handleRemoveUploadedImage}
                onReplaceImage={handleReplaceUploadedImage}
                imageStates={intakeImageStates}
                maxImages={MAX_IMAGES_PER_PET}
                photosLocked={photosLocked}
                onContinue={() => {
                  // 사진이 없으면(= 영상만 있거나 비어 있으면) 처리 화면은 할 일이
                  // 없다. 버튼도 막혀 있지만, 진입 경로를 한 번 더 닫아 둔다.
                  // 잠긴 아이(생성이 이미 시작됨)는 처리 화면이 아니라 미리보기로
                  // 간다 — 거기서 만들던 영상을 이어 본다. 처리 화면으로 보내면
                  // 서버가 PHASE1_LOCKED 로 답할 것이 뻔하다.
                  if (
                    intakeContinueTarget({ canStart: canStartIntake, photosLocked }) === 'preview'
                  ) {
                    navigateTo('preview')
                    return
                  }
                  if (!canStartIntake) return
                  navigateTo('aiProcessing')
                }}
                onBack={() => navigateTo('signup', 'back')}
              />
            </motion.div>
          )}

          {screen === 'aiProcessing' && (
            <motion.div
              key="aiProcessing"
              custom={pageMotionCustom()}
              variants={pageVariants}
              initial="initial"
              animate="animate"
              exit="exit"
              className="h-full"
            >
              <AIProcessingScreen
                uploadedImage={uploadedImage}
                uploadedImages={uploadedImages}
                petSlotId={activePetSlotId}
                intakeIdentity={intakeIdentity}
                language={language}
                onImageStateChange={(index, state) =>
                  handleIntakeImageStateForSlot(activePetSlotIndex, index, state)
                }
                onComplete={(cutoutUrl) => handleAIProcessingComplete(activePetSlotIndex, cutoutUrl)}
              />
            </motion.div>
          )}

          {screen === 'themeSelection' && (
            <motion.div
              key="themeSelection"
              custom={pageMotionCustom()}
              variants={pageVariants}
              initial="initial"
              animate="animate"
              exit="exit"
              className="h-full"
            >
              <ThemeSelectionScreen
                cutoutImage={effectiveCutoutImage}
                cutoutReadiness={durableCutout.status}
                onCutoutImageError={refreshDurableCutout}
                // 카드·큰 미리보기·생성이 **같은 한 장**을 쓰게 한다.
                originalPhoto={originalPhoto}
                selectedTheme={selectedTheme}
                language={language}
                onSelectTheme={handleThemeSelect}
                onSelectCustomBackground={handleSelectCustomBackground}
                onContinue={handleThemeContinue}
                onBack={() => navigateTo('home', 'back')}
              />
            </motion.div>
          )}

          {screen === 'customBackground' && (
            <motion.div
              key="customBackground"
              custom={pageMotionCustom()}
              variants={pageVariants}
              initial="initial"
              animate="animate"
              exit="exit"
              className="h-full"
            >
              <CustomBackgroundScreen
                uploadedImage={uploadedImage}
                language={language}
                onComplete={handleCustomBackgroundComplete}
                onBack={() => navigateTo('themeSelection', 'back')}
              />
            </motion.div>
          )}

          {screen === 'preview' && (
            <motion.div
              key="preview"
              custom={pageMotionCustom()}
              variants={pageVariants}
              initial="initial"
              animate="animate"
              exit="exit"
              className="h-full"
            >
              <PreviewScreen
                cutoutImage={effectiveCutoutImage}
                originalPhoto={originalPhoto}
                selectedTheme={selectedTheme}
                language={language}
                settings={previewSettings}
                deliveryMode={
                  selectedTheme && isPremiumTheme(selectedTheme) ? 'shipping' : 'device'
                }
                onSettingsChange={handlePreviewSettingsChange}
                onComplete={() => {
                  if (!selectedTheme) setSelectedTheme(1)
                  // 실물(프리미엄) 테마만 다음 화면이 있다. 기기 전달은 발행 뒤
                  // Preview 에 머무른다 — 직접 Pi 송출 화면(devicePlay)으로
                  // 넘기지 않는다.
                  if (selectedTheme && isPremiumTheme(selectedTheme)) {
                    navigateTo('shippingAddress')
                  }
                }}
                // 명시적 "Play on Beam" — theme_play → pet_asset, 현대 게이트웨이만.
                // 발행이 이것을 자동으로 부르는 일은 없다.
                onPlayOnBeam={() => {
                  const themeId = resolveSelectedThemeId(selectedTheme)
                  const theme = themeId != null ? getMemorialTheme(themeId) : undefined
                  if (!theme) return Promise.resolve({ ok: false, reason: 'no_theme' })
                  return playPublishedOnBeam({
                    themeKey: theme.themeKey,
                    petId: getEternalBeamPetId(readStoredPipeline()?.content_id ?? null),
                    motionId: 'BREATHING',
                  })
                }}
                onOpenMembership={() => openSettings('preview', { focusMembership: true })}
                onBack={() => navigateTo('themeSelection', 'back')}
              />
            </motion.div>
          )}

          {screen === 'shippingAddress' && (
            <motion.div
              key="shippingAddress"
              custom={pageMotionCustom()}
              variants={pageVariants}
              initial="initial"
              animate="animate"
              exit="exit"
              className="h-full"
            >
              <ShippingAddressScreen
                language={language}
                onComplete={async () => {
                  if (selectedTheme != null) {
                    try {
                      await finalizePreviewContent(selectedTheme, previewSettings)
                    } catch {
                      persistDeviceContentFromPipeline(selectedTheme)
                    }
                  }
                  navigateTo('nfcPlayback')
                }}
                onBack={() => navigateTo('preview', 'back')}
              />
            </motion.div>
          )}

          {screen === 'physicalOrder' && (
            <motion.div
              key="physicalOrder"
              custom={pageMotionCustom()}
              variants={pageVariants}
              initial="initial"
              animate="animate"
              exit="exit"
              className="h-full"
            >
              {/* 실물 구매. **펫도 편지도 여기서 만들지 않는다** — 이미 있는
                  canonical petId 와 연결된 Soul Trace 편지를 가리킬 뿐이다.
                  편지가 없으면 화면이 "먼저 연결하세요"로 막는다. */}
              <PhysicalOrderScreen
                petId={getEternalBeamPetId(readStoredPipeline()?.content_id ?? null)}
                onBack={() => navigateTo('home', 'back')}
              />
            </motion.div>
          )}

          {screen === 'nfcPlayback' && (
            <motion.div
              key="nfcPlayback"
              custom={pageMotionCustom()}
              variants={pageVariants}
              initial="initial"
              animate="animate"
              exit="exit"
              className="h-full"
            >
              <NFCPlaybackScreen
                language={language}
                premiumPhysical
                onComplete={handleReset}
                onBack={() => navigateTo('shippingAddress', 'back')}
                onGoPreview={() => navigateTo('preview')}
              />
            </motion.div>
          )}

          {/* 레거시 직접 Pi 송출 화면 — 명시적 기기 데모(?demo=device|kickstarter)
              에서만 그린다. 정상 흐름의 어떤 경로도 여기로 navigateTo 하지 않는다. */}
          {screen === 'devicePlay' && deviceDemo && (
            <motion.div
              key="devicePlay"
              custom={pageMotionCustom()}
              variants={pageVariants}
              initial="initial"
              animate="animate"
              exit="exit"
              className="h-full"
            >
              <MemorialDevicePlayScreen
                cutoutImage={cutoutImage}
                selectedTheme={selectedTheme}
                settings={previewSettings}
                language={language}
                onBack={() => navigateTo('preview', 'back')}
                onComplete={handleReset}
                onOpenKeepsakes={() => navigateTo('physicalOrder')}
              />
            </motion.div>
          )}

          {screen === 'forestExperience' && (
            <motion.div
              key="forestExperience"
              custom={pageMotionCustom()}
              variants={pageVariants}
              initial="initial"
              animate="animate"
              exit="exit"
              className="h-full"
            >
              <ForestExperienceScreen
                language={language}
                publicDemo={publicForestDemo}
                onBack={() =>
                  publicForestDemo
                    ? navigateTo('home', 'back')
                    : cutoutImage
                      ? navigateTo('themeSelection', 'back')
                      : navigateTo('home', 'back')
                }
                onComplete={() =>
                  deviceDemo ? navigateTo('themeSelection') : navigateTo('nfcPlayback')
                }
              />
            </motion.div>
          )}

          {screen === 'device' && (
            <motion.div
              key="device"
              custom={pageMotionCustom()}
              variants={pageVariants}
              initial="initial"
              animate="animate"
              exit="exit"
              className="h-full"
            >
              <DeviceScreen
                onBack={() => navigateTo('settings', 'back')}
                language={language}
              />
            </motion.div>
          )}

          {screen === 'settings' && (
            <motion.div
              key="settings"
              custom={pageMotionCustom()}
              variants={pageVariants}
              initial="initial"
              animate="animate"
              exit="exit"
              className="h-full"
            >
              <SettingsScreen
                currentLanguage={language}
                onChangeLanguage={() =>
                  handleLanguageChange(language === 'ko' ? 'en' : 'ko')
                }
                onDeviceSettings={() => navigateTo('device')}
                onBack={handleSettingsBack}
                onLogout={handleLogout}
                focusMembership={focusMembership}
              />
            </motion.div>
          )}
        </AnimatePresence>
      </AppShell>
    </main>
  )
}
