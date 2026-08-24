import { ShakerOpsScreen } from '@/components/memorial/shaker-ops-screen'
import { OpsProductionScreen } from '@/components/memorial/ops-production-screen'
import { ThemePurchaseReturnScreen } from '@/components/memorial/theme-purchase-return-screen'
import { OrderConfirmationScreen } from '@/components/memorial/order-confirmation-screen'
import { SoulTraceImportScreen } from '@/components/memorial/soul-trace-import-screen'
import { ShakerScreen } from '@/components/memorial/shaker-screen'
import { isShakerEntry } from '@/lib/shaker-entry'
import { OpsPartnersScreen } from '@/components/memorial/ops-partners-screen'
import { isOpsPartnersEntry, isOpsProductionEntry, isOpsShakerEntry } from '@/lib/shaker-ops-entry'
import { orderReturnEntry, themeReturnEntry } from '@/lib/app-entry'
import { isSoulTraceImportEntry, peekSoulTraceHandoffState } from '@/lib/soul-trace-handoff'
import { EternalBeamApp } from './EternalBeamApp'

/**
 * 세 갈래 진입 — 소유 모델을 그대로 반영한다.
 *
 *   /ops/shaker  판매자(Eternal Beam) 영역 — QR 생성·관리
 *   /shaker      고객이 QR 로 여는 공개 경험
 *   그 외        고객 앱
 *
 * Shaker 를 EternalBeamApp **바깥에서** 분기하는 이유: 안에 넣으면 공개
 * 방문자에게도 메인 앱 트리가 마운트된다 — 인증 부팅, 파이프라인 복원, 프리미엄
 * 자산 폴링, 기기 동기화 effect 가 전부 따라 붙는다. 그중 하나라도 생성이나 과금
 * 경로를 건드리면 "Shaker 는 절대 생성하지 않는다"가 조용히 깨진다.
 *
 * 여기서 끊으면 그런 코드는 **실행 자체가 되지 않는다.** 검증할 필요가 없는 것이
 * 가장 확실한 검증이다.
 *
 * ⚠️ 운영 경로 분기는 **보안 경계가 아니다.** 인가는 서버가 한다
 * (JWT + SHAKER_OPS_USER_IDS). 여기서는 어느 화면을 그릴지만 정한다.
 */
export default function App() {
  // 테마 일회성 결제 복귀. 구독 복귀(/billing/*)와 경로를 나눠 두어, 테마 결제가
  // 구독 confirm 을 타는 일이 없다.
  // 실물 주문은 보여 줄 것이 다르다(주문번호·아이·제품·수령인·결제 상태).
  // Soul Trace 핸드오프 착지점. EternalBeamApp **바깥**에서 끊는다 — 안에 넣으면
  // 아직 로그인도 하지 않은 방문자에게 파이프라인 복원·프리미엄 폴링·기기 동기화
  // effect 가 전부 붙고, 그 사이에 1회용 토큰이 든 URL 이 살아 있게 된다.
  if (isSoulTraceImportEntry(window.location.pathname)) return <SoulTraceImportScreen />
  if (orderReturnEntry()) return <OrderConfirmationScreen />
  if (themeReturnEntry()) return <ThemePurchaseReturnScreen />
  if (isOpsPartnersEntry()) return <OpsPartnersScreen />
  if (isOpsProductionEntry()) return <OpsProductionScreen />
  if (isOpsShakerEntry()) return <ShakerOpsScreen />
  if (isShakerEntry()) return <ShakerScreen />

  // ── 이메일 확인 탭에서 돌아온 경우 ────────────────────────────────────────
  // 가입 확인 링크는 **새 탭**에서 열리고, 그 탭이 어디에 떨어질지는 Supabase 의
  // 리다이렉트 설정이 정한다 — 대개 앱 루트다. 그러면 핸드오프는 저장돼 있는데
  // 그것을 읽는 화면은 마운트되지 않아, 사용자는 로그인만 되고 편지는 사라진
  // 것처럼 보인다.
  //
  // 그래서 **핸드오프의 흔적이 있으면** import 화면으로 이어 붙인다.
  //
  // ⚠️ 만료도 포함한다. 예전에는 유효한 것만 봤고(hasPendingSoulTraceHandoff),
  // 만료된 사용자는 아무 설명 없이 평소 온보딩(qrConnection)으로 떨어졌다 —
  // 편지를 기다리던 사람에게 "기기를 QR 로 연결하세요"가 나오고, 편지가 어디로
  // 갔는지는 어디에도 적혀 있지 않았다. import 화면이 만료를 알아보고 Soul Trace
  // 로 다시 보내 준다.
  //
  // 흔적이 아예 없으면 이 줄은 아무 일도 하지 않으므로,
  // device.eternalbeam.com 으로 직접 들어오는 기존 흐름은 그대로다.
  if (peekSoulTraceHandoffState() !== 'none') return <SoulTraceImportScreen />

  return <EternalBeamApp />
}
