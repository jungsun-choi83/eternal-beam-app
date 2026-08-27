/**
 * Shaker 자이로 패럴랙스 — "유리 뒤에 있는 것 같다"까지만.
 *
 * 목표는 게임이 아니라 **깊이의 암시**다. 그래서 이 모듈의 상수는 전부 작고,
 * 큰 값을 넣어도 커지지 않도록 상한이 걸려 있다.
 *
 * 깊이는 레이어를 **다른 양만큼** 움직여서 만든다:
 *   펫    가까이 있으므로 많이 움직인다 (1.0×)
 *   배경  멀리 있으므로 덜 움직인다   (0.35×)
 * 같은 방향이다. 반대로 움직이면 깊이가 아니라 "찢어짐"으로 보인다.
 *
 * ── 순수하게 유지하는 이유 ──────────────────────────────────────────────────
 * 계산(createParallaxTracker)에는 DOM 도 센서도 없다. 그래야 node --test 가
 * 기울기 → 픽셀 변환, 데드존, 기준점, 감쇠를 전부 검증할 수 있다. 센서 구독과
 * 권한 요청만 부수효과로 남기고, 그 둘은 얇게 만든다.
 */

/** DeviceOrientationEvent 의 원본 각도(도). */
export interface GyroSample {
  /** 앞뒤 기울기. -180 ~ 180 */
  beta: number | null;
  /** 좌우 기울기. -90 ~ 90 */
  gamma: number | null;
}

export interface Offset {
  x: number;
  y: number;
}

export interface ParallaxFrame {
  pet: Offset;
  background: Offset;
}

export const NO_PARALLAX: ParallaxFrame = {
  pet: { x: 0, y: 0 },
  background: { x: 0, y: 0 },
};

export interface ParallaxConfig {
  /** 이 각도(도)에서 최대 이동에 도달한다. 크게 잡을수록 둔해진다. */
  rangeDeg: number;
  /** 펫 레이어의 최대 이동(px). **작게 유지한다** — 이것이 "은은함"의 실체다. */
  petMaxPx: number;
  /** 배경 레이어의 최대 이동(px). 펫보다 작다(멀리 있으므로). */
  backgroundMaxPx: number;
  /** 이 각도(도) 안의 흔들림은 무시한다. 손 떨림으로 펫이 떠는 것을 막는다. */
  deadZoneDeg: number;
  /** 지수 감쇠 계수 0~1. 작을수록 부드럽고 느리다. */
  smoothing: number;
}

/**
 * 실측 기준값.
 *
 * petMaxPx=10 은 6.1인치 화면에서 눈에 "띄지 않게" 느껴지는 상한이다. 12를 넘으면
 * 사람들이 움직임 자체를 인지하기 시작하고, 그때부터는 깊이가 아니라 효과로 보인다.
 */
export const PARALLAX_DEFAULT: ParallaxConfig = {
  rangeDeg: 26,
  petMaxPx: 10,
  backgroundMaxPx: 3.5,
  deadZoneDeg: 1.2,
  smoothing: 0.12,
};

/** 설정이 어떻게 들어오든 은은함을 넘지 않게 자른다. */
const PET_MAX_PX_CEILING = 16;
const BG_MAX_PX_CEILING = 8;

function clamp(v: number, lo: number, hi: number): number {
  return v < lo ? lo : v > hi ? hi : v;
}

export function sanitizeParallaxConfig(input?: Partial<ParallaxConfig>): ParallaxConfig {
  const c = { ...PARALLAX_DEFAULT, ...(input || {}) };
  return {
    rangeDeg: clamp(c.rangeDeg, 5, 90),
    petMaxPx: clamp(c.petMaxPx, 0, PET_MAX_PX_CEILING),
    backgroundMaxPx: clamp(c.backgroundMaxPx, 0, BG_MAX_PX_CEILING),
    deadZoneDeg: clamp(c.deadZoneDeg, 0, 10),
    smoothing: clamp(c.smoothing, 0.01, 1),
  };
}

/**
 * 각도 차 → -1~1 정규화. 데드존 안은 0 이고, 데드존 밖은 **0 에서 다시 시작한다**.
 *
 * 데드존을 빼지 않고 그냥 자르면 경계에서 값이 툭 튄다(0 → 0.05). 빼고 다시
 * 정규화해야 데드존을 벗어나는 순간이 매끄럽다.
 */
export function normalizeTilt(deltaDeg: number, rangeDeg: number, deadZoneDeg: number): number {
  const sign = deltaDeg < 0 ? -1 : 1;
  const mag = Math.abs(deltaDeg);
  if (mag <= deadZoneDeg) return 0;
  const usable = Math.max(1e-6, rangeDeg - deadZoneDeg);
  return sign * clamp((mag - deadZoneDeg) / usable, 0, 1);
}

export interface ParallaxTracker {
  /** 샘플 하나를 넣고 이번 프레임의 오프셋을 받는다. */
  push(sample: GyroSample): ParallaxFrame;
  /** 기준 자세를 다시 잡는다 (방향 전환·권한 재획득 후). */
  reset(): void;
  /** 지금까지의 출력. 센서가 멎어도 마지막 값을 유지한다. */
  current(): ParallaxFrame;
}

export function isValidGyroSample(sample: GyroSample): sample is {
  beta: number;
  gamma: number;
} {
  return (
    typeof sample?.beta === "number" &&
    Number.isFinite(sample.beta) &&
    typeof sample?.gamma === "number" &&
    Number.isFinite(sample.gamma)
  );
}

/**
 * DeviceOrientation axes stay attached to the physical device. Rotate the raw
 * gamma/beta pair into the current screen axes so "left/right" remains X in
 * portrait and either landscape orientation.
 */
export function alignGyroSampleToScreen(
  sample: GyroSample,
  screenAngleDeg: number
): GyroSample {
  if (!isValidGyroSample(sample)) return sample;
  const angle = Number.isFinite(screenAngleDeg) ? screenAngleDeg : 0;
  const radians = (angle * Math.PI) / 180;
  const cos = Math.cos(radians);
  const sin = Math.sin(radians);
  const deviceX = sample.gamma;
  const deviceY = sample.beta;
  return {
    gamma: deviceX * cos - deviceY * sin,
    beta: deviceX * sin + deviceY * cos,
  };
}

/**
 * 기울기 → 레이어 오프셋. **첫 샘플이 기준 자세가 된다.**
 *
 * 기준점을 잡는 것이 핵심이다. 사람은 폰을 45° 쯤 기울여 들고 보는데, 절대 각도를
 * 그대로 쓰면 시작하자마자 최대치에 붙어 버려 움직일 여지가 없다. 처음 든 자세를
 * 0 으로 두면 거기서부터 ± 로 움직인다.
 */
export function createParallaxTracker(config?: Partial<ParallaxConfig>): ParallaxTracker {
  const cfg = sanitizeParallaxConfig(config);
  let originBeta: number | null = null;
  let originGamma: number | null = null;
  let smoothX = 0;
  let smoothY = 0;

  const frame = (): ParallaxFrame => ({
    pet: {
      x: smoothX * cfg.petMaxPx,
      y: smoothY * cfg.petMaxPx,
    },
    background: {
      x: smoothX * cfg.backgroundMaxPx,
      y: smoothY * cfg.backgroundMaxPx,
    },
  });

  return {
    push(sample: GyroSample): ParallaxFrame {
      // 센서가 null 을 주는 경우가 실제로 있다(권한은 있는데 하드웨어가 조용한
      // 순간). 그때 0 으로 취급하면 펫이 중앙으로 튄다 — 마지막 값을 유지한다.
      if (!isValidGyroSample(sample)) return frame();
      const { beta, gamma } = sample;

      if (originBeta === null || originGamma === null) {
        originBeta = beta;
        originGamma = gamma;
      }

      const nx = normalizeTilt(gamma - originGamma, cfg.rangeDeg, cfg.deadZoneDeg);
      const ny = normalizeTilt(beta - originBeta, cfg.rangeDeg, cfg.deadZoneDeg);

      smoothX += (nx - smoothX) * cfg.smoothing;
      smoothY += (ny - smoothY) * cfg.smoothing;
      return frame();
    },
    reset() {
      originBeta = null;
      originGamma = null;
      smoothX = 0;
      smoothY = 0;
    },
    current: frame,
  };
}

// ── 프레임 스케줄링 · 센서 수명주기 ─────────────────────────────────────────

export interface ParallaxFrameLoop {
  /** 최신 유효 샘플을 저장하고, 아직 없다면 다음 애니메이션 프레임을 예약한다. */
  push(sample: GyroSample): boolean;
  /** 예약 프레임과 이전 기준 자세를 버린다. 다음 유효 샘플이 새 기준이 된다. */
  reset(): void;
  /** 예약 프레임을 취소하고 다시 사용할 수 없게 만든다. */
  destroy(): void;
}

/**
 * 센서 이벤트를 화면 주사율에 맞춰 합친다.
 *
 * DeviceOrientationEvent 는 기기에 따라 60Hz 보다 훨씬 자주 올 수 있다. 이벤트마다
 * React state 를 바꾸지 않고 여기서 마지막 값만 보관한 뒤, 한 animation frame 에
 * 정확히 한 번만 트래커와 DOM 콜백을 갱신한다.
 */
export function createParallaxFrameLoop(input: {
  onFrame: (frame: ParallaxFrame) => void;
  requestFrame: (callback: (timestamp: number) => void) => number;
  cancelFrame: (id: number) => void;
  config?: Partial<ParallaxConfig>;
}): ParallaxFrameLoop {
  const tracker = createParallaxTracker(input.config);
  let latest: GyroSample | null = null;
  let frameId: number | null = null;
  let destroyed = false;

  const flush = () => {
    frameId = null;
    if (destroyed || !latest) return;
    const sample = latest;
    latest = null;
    input.onFrame(tracker.push(sample));
  };

  return {
    push(sample) {
      if (destroyed || !isValidGyroSample(sample)) return false;
      latest = { beta: sample.beta, gamma: sample.gamma };
      if (frameId === null) frameId = input.requestFrame(flush);
      return true;
    },
    reset() {
      latest = null;
      if (frameId !== null) input.cancelFrame(frameId);
      frameId = null;
      tracker.reset();
    },
    destroy() {
      if (destroyed) return;
      destroyed = true;
      latest = null;
      if (frameId !== null) input.cancelFrame(frameId);
      frameId = null;
      tracker.reset();
    },
  };
}

export const ORIENTATION_SAMPLE_TIMEOUT_MS = 1_500;

export interface OrientationMotionSession {
  start(): void;
  destroy(): void;
}

type Unsubscribe = () => void;
type TimerHandle = unknown;

/**
 * 브라우저 센서의 수명주기를 한곳에서 관리한다.
 *
 * 구독 방법을 주입받으므로 이 모듈은 DOM 없이도 다음을 검증할 수 있다:
 * 무샘플 타임아웃, hidden 일 때 중지, visible 복귀 후 새 기준점, 방향 전환 재보정,
 * 그리고 unmount 정리.
 */
export function createOrientationMotionSession(input: {
  frameLoop: ParallaxFrameLoop;
  subscribeOrientation: (listener: (sample: GyroSample) => void) => Unsubscribe;
  subscribeOrientationChange: (listener: () => void) => Unsubscribe;
  subscribeVisibilityChange: (listener: () => void) => Unsubscribe;
  isHidden: () => boolean;
  scheduleTimeout: (callback: () => void, delayMs: number) => TimerHandle;
  cancelTimeout: (handle: TimerHandle) => void;
  timeoutMs?: number;
  onActiveChange?: (active: boolean) => void;
}): OrientationMotionSession {
  const timeoutMs = input.timeoutMs ?? ORIENTATION_SAMPLE_TIMEOUT_MS;
  let started = false;
  let destroyed = false;
  let acceptingSamples = false;
  let active = false;
  let timeoutHandle: TimerHandle | null = null;
  let unsubscribers: Unsubscribe[] = [];

  const setActive = (next: boolean) => {
    if (active === next) return;
    active = next;
    input.onActiveChange?.(next);
  };

  const clearSampleTimeout = () => {
    if (timeoutHandle === null) return;
    input.cancelTimeout(timeoutHandle);
    timeoutHandle = null;
  };

  const pause = () => {
    acceptingSamples = false;
    clearSampleTimeout();
    input.frameLoop.reset();
    setActive(false);
  };

  const armForFreshSample = () => {
    pause();
    if (destroyed || input.isHidden()) return;
    acceptingSamples = true;
    timeoutHandle = input.scheduleTimeout(() => {
      timeoutHandle = null;
      acceptingSamples = false;
      input.frameLoop.reset();
      setActive(false);
    }, timeoutMs);
  };

  const onOrientation = (sample: GyroSample) => {
    if (!acceptingSamples || !isValidGyroSample(sample)) return;
    if (!active) {
      clearSampleTimeout();
      setActive(true);
    }
    input.frameLoop.push(sample);
  };

  const onVisibilityChange = () => {
    if (input.isHidden()) pause();
    else armForFreshSample();
  };

  const onOrientationChange = () => {
    if (!input.isHidden()) armForFreshSample();
  };

  return {
    start() {
      if (started || destroyed) return;
      started = true;
      unsubscribers = [
        input.subscribeOrientation(onOrientation),
        input.subscribeOrientationChange(onOrientationChange),
        input.subscribeVisibilityChange(onVisibilityChange),
      ];
      armForFreshSample();
    },
    destroy() {
      if (destroyed) return;
      destroyed = true;
      pause();
      for (const unsubscribe of unsubscribers) unsubscribe();
      unsubscribers = [];
      input.frameLoop.destroy();
    },
  };
}

// ── 권한 · 지원 여부 ─────────────────────────────────────────────────────────

/**
 * 이 브라우저에서 자이로를 어떻게 얻는가.
 *
 *   "ios-permission" iOS 13+ Safari — 사용자 제스처 안에서 requestPermission() 필요
 *   "auto"           Android Chrome 등 — 그냥 구독하면 된다
 *   "unsupported"    데스크톱 등 — 센서가 없다
 */
export type GyroSupport = "ios-permission" | "auto" | "unsupported";

type DeviceOrientationCtor = {
  requestPermission?: () => Promise<"granted" | "denied" | "default">;
};

export function detectGyroSupport(): GyroSupport {
  if (typeof window === "undefined") return "unsupported";
  const ctor = (window as unknown as { DeviceOrientationEvent?: DeviceOrientationCtor })
    .DeviceOrientationEvent;
  if (!ctor) return "unsupported";
  // iOS 13+ 만 이 정적 메서드를 갖는다. 이것이 유일하게 신뢰할 만한 판별법이다 —
  // userAgent 파싱은 iPadOS 가 데스크톱으로 위장하면서부터 맞지 않는다.
  if (typeof ctor.requestPermission === "function") return "ios-permission";
  return "auto";
}

export type GyroPermission = "granted" | "denied" | "unavailable";

/**
 * iOS 권한 요청. **반드시 사용자 제스처 핸들러 안에서 불러야 한다** —
 * 그렇지 않으면 Safari 가 조용히 거부한다.
 *
 * 거부는 실패가 아니다. 호출부는 폴백(비-자이로)으로 넘어가고 경험은 계속된다.
 */
export async function requestGyroPermission(): Promise<GyroPermission> {
  if (detectGyroSupport() !== "ios-permission") {
    return detectGyroSupport() === "auto" ? "granted" : "unavailable";
  }
  const ctor = (window as unknown as { DeviceOrientationEvent?: DeviceOrientationCtor })
    .DeviceOrientationEvent;
  try {
    const res = await ctor?.requestPermission?.();
    return res === "granted" ? "granted" : "denied";
  } catch {
    // 제스처 밖에서 불렸거나 사용자가 무시했다. 폴백으로 간다.
    return "denied";
  }
}

/**
 * 사용자가 움직임 최소화를 켜 두었는가.
 *
 * 켜져 있으면 패럴랙스를 **완전히 끈다**(줄이지 않는다). 전정기관 장애가 있는
 * 사용자에게 "약한 움직임"은 여전히 증상을 유발한다 — 절반은 배려가 아니다.
 */
export function prefersReducedMotion(): boolean {
  if (typeof window === "undefined" || typeof window.matchMedia !== "function") return false;
  try {
    return window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  } catch {
    return false;
  }
}

/**
 * 지금 패럴랙스를 적용해도 되는가 — 세 조건이 **모두** 참일 때만.
 *
 * 한 함수에 모은 이유는 호출부가 조건을 따로 조합하다 하나를 빠뜨리는 것을 막기
 * 위해서다. 특히 reduced-motion 은 빠뜨리기 쉽고, 빠뜨리면 접근성 회귀가 된다.
 */
export function shouldAnimateParallax(input: {
  permission: GyroPermission | null;
  reducedMotion: boolean;
  /** 비-자이로 폴백(포인터)이 붙어 있는가. */
  pointerFallbackActive?: boolean;
}): boolean {
  if (input.reducedMotion) return false;
  if (input.permission === "granted") return true;
  return Boolean(input.pointerFallbackActive);
}

/**
 * 포인터 좌표 → 자이로 샘플로 변환 (비-자이로 폴백).
 *
 * 데스크톱과 권한 거부 상태에서 같은 계산 경로를 쓰기 위한 어댑터다. 트래커를
 * 두 벌 만들지 않으므로 감쇠·데드존·상한이 자동으로 똑같이 적용된다.
 *
 * 화면 중앙을 0 으로 두고 ±rangeDeg 로 매핑한다.
 */
export function pointerToGyroSample(
  point: { x: number; y: number },
  viewport: { width: number; height: number },
  rangeDeg: number = PARALLAX_DEFAULT.rangeDeg
): GyroSample {
  const w = viewport.width || 1;
  const h = viewport.height || 1;
  const nx = clamp((point.x - w / 2) / (w / 2), -1, 1);
  const ny = clamp((point.y - h / 2) / (h / 2), -1, 1);
  return { gamma: nx * rangeDeg, beta: ny * rangeDeg };
}
