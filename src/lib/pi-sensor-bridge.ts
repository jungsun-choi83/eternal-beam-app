/** Pi 하드웨어 센서(NFC/거리/마이크) → 웹앱 이벤트 */

import { isDeviceKickstarterDemo } from '@/lib/device-demo-config'
import { triggerNfcActivation } from '@/lib/nfc-activation'

const PI_HTTP_PORT = 8787
const PI_HOST_STORAGE_KEY = 'eternalbeam:pi-http-base'
const DEVICE_CONNECTED_KEY = 'eternal_beam_device_connected'
const PROBE_TIMEOUT_MS = 1200
const DISCOVERY_BUDGET_MS = 6000
const MAX_PARALLEL_PROBES = 3

/** 같은 서브넷에서 흔한 Pi 주소 (핫스팟 DHCP) */
const DEMO_PI_PROBE_HOSTS = [
  'eternalbeam.local',
  'raspberrypi.local',
  '192.168.0.100',
  '192.168.0.101',
  '192.168.0.102',
  '192.168.0.103',
  '192.168.0.104',
  '192.168.0.105',
  '192.168.43.1',
  '192.168.43.100',
  '172.30.1.54',
  '172.30.1.68',
]

export type PiSensorPayload = {
  event?: string
  distance_mm?: number
  theme_id?: string
  source?: string
}

function normalizeBase(hostOrUrl: string): string {
  const raw = hostOrUrl.trim().replace(/\/$/, '')
  if (!raw) return ''
  if (raw.startsWith('http://') || raw.startsWith('https://')) return raw
  return `http://${raw}:${PI_HTTP_PORT}`
}

function readUrlPiHost(): string | null {
  if (typeof window === 'undefined') return null
  const params = new URLSearchParams(window.location.search)
  const pi = params.get('pi')?.trim() || params.get('pi_host')?.trim()
  if (!pi) return null
  return normalizeBase(pi)
}

function readStoredPiBase(): string | null {
  if (typeof window === 'undefined') return null
  try {
    const stored = localStorage.getItem(PI_HOST_STORAGE_KEY)?.trim()
    return stored ? normalizeBase(stored) : null
  } catch {
    return null
  }
}

function rememberPiBase(base: string): void {
  if (typeof window === 'undefined') return
  try {
    localStorage.setItem(PI_HOST_STORAGE_KEY, base)
  } catch {
    /* ignore quota */
  }
}

/** QR 스플래시 완료 또는 ?demo=device — Pi 자동 탐색 대상 */
export function isDevicePaired(): boolean {
  if (typeof window === 'undefined') return false
  if (isDeviceKickstarterDemo()) return true
  try {
    return localStorage.getItem(DEVICE_CONNECTED_KEY) === '1'
  } catch {
    return false
  }
}

function buildPiCandidates(): string[] {
  const seen = new Set<string>()
  const ordered: string[] = []

  const push = (value: string | null | undefined) => {
    if (!value) return
    const base = normalizeBase(value)
    if (!base || seen.has(base)) return
    seen.add(base)
    ordered.push(base)
  }

  push(readUrlPiHost())
  push(readStoredPiBase())

  const explicit = import.meta.env.VITE_PI_HTTP_BASE?.trim()
  if (explicit) push(explicit)

  const host = import.meta.env.VITE_PI_HOST?.trim()
  if (host) push(host)

  const hosts = import.meta.env.VITE_PI_HOSTS?.trim()
  if (hosts) {
    for (const h of hosts.split(',')) push(h.trim())
  }

  if (isDevicePaired()) {
    for (const h of DEMO_PI_PROBE_HOSTS) push(h)
  }

  return ordered
}

async function probePiBase(base: string): Promise<boolean> {
  const ctrl = new AbortController()
  const timer = setTimeout(() => ctrl.abort(), PROBE_TIMEOUT_MS)
  try {
    const res = await fetch(`${base}/health`, {
      method: 'GET',
      signal: ctrl.signal,
      cache: 'no-store',
    })
    return res.ok
  } catch {
    return false
  } finally {
    clearTimeout(timer)
  }
}

function prioritizeCandidates(candidates: string[]): string[] {
  const ips: string[] = []
  const locals: string[] = []
  for (const base of candidates) {
    if (base.includes('.local')) locals.push(base)
    else ips.push(base)
  }
  return [...ips, ...locals]
}

async function probeCandidatesBatched(candidates: string[]): Promise<string | null> {
  const ordered = prioritizeCandidates(candidates)
  for (let i = 0; i < ordered.length; i += MAX_PARALLEL_PROBES) {
    const batch = ordered.slice(i, i + MAX_PARALLEL_PROBES)
    const hits = await Promise.all(
      batch.map(async (base) => ((await probePiBase(base)) ? base : null)),
    )
    const found = hits.find(Boolean)
    if (found) return found
  }
  return null
}

async function discoverPiHttpBaseInner(): Promise<string | null> {
  const candidates = buildPiCandidates()
  if (!candidates.length) return null

  const stored = readStoredPiBase()
  const fromUrl = readUrlPiHost()
  const fastFirst = [fromUrl, stored].filter(Boolean) as string[]
  for (const base of fastFirst) {
    if (await probePiBase(base)) {
      rememberPiBase(base)
      return base
    }
  }

  const rest = candidates.filter((c) => !fastFirst.includes(c))
  if (!rest.length) return null

  const found = await probeCandidatesBatched(rest)
  if (found) {
    rememberPiBase(found)
    return found
  }
  return null
}

let discoveryPromise: Promise<string | null> | null = null

/** Pi HTTP 주소 자동 탐색 (URL ?pi=IP, localStorage, mDNS, 서브넷 후보) */
export async function discoverPiHttpBase(): Promise<string | null> {
  let timer: ReturnType<typeof setTimeout> | null = null
  try {
    return await Promise.race([
      discoverPiHttpBaseInner(),
      new Promise<null>((resolve) => {
        timer = setTimeout(() => resolve(null), DISCOVERY_BUDGET_MS)
      }),
    ])
  } finally {
    if (timer) clearTimeout(timer)
  }
}

/** 백그라운드 Pi 탐색 — UI 블로킹 방지 */
export function schedulePiDiscovery(delayMs = 800): void {
  if (typeof window === 'undefined') return
  window.setTimeout(() => {
    void discoverPiHttpBase()
  }, delayMs)
}

function discoverPiHttpBaseCached(): Promise<string | null> {
  if (!discoveryPromise) {
    discoveryPromise = discoverPiHttpBase().finally(() => {
      discoveryPromise = null
    })
  }
  return discoveryPromise
}

export function resolvePiHttpBase(): string | null {
  return readUrlPiHost() ?? readStoredPiBase() ?? buildPiCandidates()[0] ?? null
}

export function resolvePiSseUrl(): string | null {
  const base = resolvePiHttpBase()
  return base ? `${base}/events` : null
}

/** 포레스트 데모 → Pi → S23 idle + 센서 대기 */
export async function triggerForestMachineDemo(): Promise<boolean> {
  return triggerThemeOnDevice("fresh_forest");
}

/** 선택한 테마를 연결된 기기(Pi/S23)로 즉시 송출 */
export async function triggerThemeOnDevice(
  themeKey: string,
  contentId?: string | null
): Promise<boolean> {
  const quick = readUrlPiHost() ?? readStoredPiBase()
  if (quick && (await probePiBase(quick))) {
    return postThemeToPi(quick, themeKey, contentId)
  }

  const base = (await discoverPiHttpBaseCached()) ?? resolvePiHttpBase()
  if (!base) return false
  return postThemeToPi(base, themeKey, contentId)
}

async function postThemeToPi(
  base: string,
  themeKey: string,
  contentId?: string | null,
): Promise<boolean> {
  try {
    const res = await fetch(`${base}/demo/play`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        theme_id: themeKey,
        content_id: contentId ?? undefined,
      }),
    })
    if (res.ok) rememberPiBase(base)
    return res.ok
  } catch {
    return false
  }
}

/** 회원가입 시 등록한 반려 이름 → Pi voice wake 목록 */
export async function syncPetWakeNamesToPi(
  names: string[],
  petName?: string
): Promise<boolean> {
  const cleaned = names.map((n) => n.trim()).filter(Boolean);
  if (!cleaned.length) return false;
  const base = (await discoverPiHttpBaseCached()) ?? resolvePiHttpBase();
  if (!base) return false;
  try {
    const res = await fetch(`${base}/pet/wake-names`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        names: cleaned,
        pet_name: (petName ?? cleaned[0]).trim(),
      }),
    });
    if (res.ok) rememberPiBase(base);
    return res.ok;
  } catch {
    return false;
  }
}

function openPiEventSource(
  base: string,
  onMessage: (payload: PiSensorPayload) => void,
  onStatus?: (msg: string) => void,
): EventSource {
  const url = `${base}/events`
  const es = new EventSource(url)

  es.onopen = () => {
    rememberPiBase(base)
    onStatus?.('Pi 센서 연결됨 (거리·마이크·NFC)')
  }

  es.onmessage = (ev) => {
    try {
      onMessage(JSON.parse(ev.data) as PiSensorPayload)
    } catch {
      /* ignore bad payload */
    }
  }

  es.onerror = () => {
    onStatus?.('Pi 센서 연결 끊김 — URL에 ?pi=라즈베리IP 추가')
  }

  return es
}

export function subscribePiNfcEvents(
  onNfc: () => void,
  onStatus?: (msg: string) => void,
): () => void {
  if (typeof EventSource === 'undefined') return () => {}

  let es: EventSource | null = null
  let cancelled = false

  onStatus?.('Pi 연결 중…')
  void discoverPiHttpBaseCached().then((base) => {
    if (cancelled) return
    if (!base) {
      onStatus?.('Pi 연결 실패 — ?demo=device&pi=192.168.0.xxx')
      return
    }
    es = openPiEventSource(
      base,
      (payload) => {
        const event = String(payload.event ?? '').toLowerCase()
        if (event === 'nfc_tagged' || event === 'demo_forest') onNfc()
      },
      onStatus,
    )
  })

  return () => {
    cancelled = true
    es?.close()
  }
}

export function subscribePiSensors(
  onPetAction: () => void,
  onStatus?: (msg: string) => void,
): () => void {
  if (typeof EventSource === 'undefined') {
    onStatus?.('Pi 센서: URL 없음 (폰 마이크 사용)')
    return () => {}
  }

  let es: EventSource | null = null
  let cancelled = false

  onStatus?.('Pi 연결 중…')
  void discoverPiHttpBaseCached().then((base) => {
    if (cancelled) return
    if (!base) {
      onStatus?.('Pi 연결 실패 — ?demo=device&pi=라즈베리IP')
      return
    }
    es = openPiEventSource(
      base,
      (payload) => {
        const event = String(payload.event ?? '').toLowerCase()
        if (event === 'nfc_tagged' || event === 'demo_forest') {
          triggerNfcActivation()
          return
        }
        if (event === 'touch' || event === 'approach' || event === 'voice') {
          onPetAction()
        }
      },
      onStatus,
    )
  })

  return () => {
    cancelled = true
    es?.close()
  }
}
