const SCANNED_DEVICE_KEY = "eternal_beam_scanned_device_id";

/** QR 문자열에서 기기 식별자만 꺼낸다. 주소면 device / device_id 를 먼저 본다. */
export function deviceIdFromQr(raw: string): string | null {
  const text = raw.trim();
  if (!text || text.length > 200) return null;

  try {
    const url = new URL(text);
    const fromQuery =
      url.searchParams.get("device_id") ||
      url.searchParams.get("device") ||
      url.searchParams.get("id");
    const queryId = fromQuery?.trim();
    if (queryId) return queryId.slice(0, 80);
    const segment = url.pathname.split("/").filter(Boolean).pop()?.trim();
    if (segment && segment.length <= 80) return decodeURIComponent(segment);
  } catch {
    /* 주소가 아닌 기기 코드 */
  }

  if (/\s/.test(text)) return null;
  return text.slice(0, 80);
}

export function saveScannedDeviceId(deviceId: string): void {
  const id = deviceId.trim();
  if (!id) return;
  try {
    localStorage.setItem(SCANNED_DEVICE_KEY, id);
  } catch {
    /* 저장을 못 해도 화면 전환은 막지 않는다 */
  }
}

export function readScannedDeviceId(): string | null {
  try {
    const id = localStorage.getItem(SCANNED_DEVICE_KEY)?.trim();
    return id || null;
  } catch {
    return null;
  }
}
