/// <reference types="vite/client" />

/** Web NFC (used by nfcManager; not in all TS DOM libs) */
interface NDEFRecord {
  recordType: string
  data: ArrayBuffer | DataView
}
interface NDEFMessage {
  records: NDEFRecord[]
}
declare class NDEFReader extends EventTarget {
  write(options: { records: Array<{ recordType: string; data: string }> }): Promise<void>
  scan(options?: { signal?: AbortSignal }): Promise<void>
}
