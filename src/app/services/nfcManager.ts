import {
  checkNFCWritePermission,
  markNFCWritten,
} from './firebaseService'

export interface NFCWriteResult {
  success: boolean
  slotNumber: number
  videoId: string
  message: string
}

/**
 * NFC 쓰기 (권한 체크 포함)
 * payload: 서버에서 받은 nfc_payload(unique_url 등)가 있으면 해당 JSON 기록
 */
export const writeToNFCSlot = async (
  contentId: string,
  videoId: string,
  slotNumber: number,
  payload?: { unique_url?: string; content_id?: string; theme_id?: string; slot_number?: number },
): Promise<NFCWriteResult> => {
  try {
    console.log('🔒 NFC 쓰기 권한 확인 중...')
    await checkNFCWritePermission(contentId)

    if (!('NDEFReader' in window)) {
      throw new Error('이 기기는 NFC를 지원하지 않습니다.')
    }

    // Content_ID만 기록 (레이어 시스템): version, content_id, slot_number
    const dataToWrite = payload?.content_id != null
      ? { version: 1, content_id: payload.content_id, slot_number: payload.slot_number ?? slotNumber }
      : payload?.unique_url
        ? { version: 1, content_id: payload.content_id || contentId, unique_url: payload.unique_url, theme_id: payload.theme_id ?? '', slot_number: payload.slot_number ?? slotNumber }
        : { video_id: videoId, content_id: contentId, slot: slotNumber, timestamp: new Date().toISOString() }

    console.log('📡 NFC 쓰기 시작...')
    const ndef = new (window as unknown as { NDEFReader: new () => NDEFReader }).NDEFReader()

    await ndef.write({
      records: [
        {
          recordType: 'text',
          data: JSON.stringify(dataToWrite),
        },
      ],
    })

    await markNFCWritten(contentId, slotNumber)

    if (navigator.vibrate) {
      navigator.vibrate([100, 50, 100, 50, 100])
    }

    console.log('✅ NFC 쓰기 완료!')
    return {
      success: true,
      slotNumber,
      videoId,
      message: '슬롯에 데이터가 기록되었습니다!',
    }
  } catch (error: unknown) {
    const err = error as Error
    console.error('❌ NFC 쓰기 실패:', error)
    return {
      success: false,
      slotNumber,
      videoId,
      message: err.message || 'NFC 쓰기에 실패했습니다.',
    }
  }
}

/**
 * NFC 읽기
 */
export const readFromNFCSlot = async (): Promise<unknown> => {
  try {
    if (!('NDEFReader' in window)) {
      throw new Error('이 기기는 NFC를 지원하지 않습니다.')
    }

    const ndef = new (window as unknown as { NDEFReader: new () => NDEFReader }).NDEFReader()
    await ndef.scan()

    return new Promise((resolve, reject) => {
      ndef.addEventListener(
        'reading',
        ({ message }: { message: NDEFMessage }) => {
          const record = message.records[0]
          const textDecoder = new TextDecoder()
          const data = JSON.parse(textDecoder.decode(record.data))
          resolve(data)
        },
      )

      setTimeout(() => reject(new Error('NFC 읽기 시간 초과')), 10000)
    })
  } catch (error) {
    console.error('❌ NFC 읽기 실패:', error)
    throw error
  }
}
