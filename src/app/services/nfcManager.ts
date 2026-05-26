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
 * NFC ?°ê¸° (ê¶Œí•œ ì²´í¬ ?¬í•¨)
 * payload: ?œë²„?ì„œ ë°›ì? nfc_payload(unique_url ??ê°€ ?ˆìœ¼ë©??´ë‹¹ JSON ê¸°ë¡
 */
export const writeToNFCSlot = async (
  contentId: string,
  videoId: string,
  slotNumber: number,
  payload?: { unique_url?: string; content_id?: string; theme_id?: string; slot_number?: number },
): Promise<NFCWriteResult> => {
  try {
    console.log('?”’ NFC ?°ê¸° ê¶Œí•œ ?•ì¸ ì¤?..')
    await checkNFCWritePermission(contentId)

    if (!('NDEFReader' in window)) {
      throw new Error('??ê¸°ê¸°??NFCë¥?ì§€?í•˜ì§€ ?ŠìŠµ?ˆë‹¤.')
    }

    // Content_IDë§?ê¸°ë¡ (?ˆì´???œìŠ¤??: version, content_id, slot_number
    const dataToWrite = payload?.content_id != null
      ? { version: 1, content_id: payload.content_id, slot_number: payload.slot_number ?? slotNumber }
      : payload?.unique_url
        ? { version: 1, content_id: payload.content_id || contentId, unique_url: payload.unique_url, theme_id: payload.theme_id ?? '', slot_number: payload.slot_number ?? slotNumber }
        : { video_id: videoId, content_id: contentId, slot: slotNumber, timestamp: new Date().toISOString() }

    console.log('?“¡ NFC ?°ê¸° ?œì‘...')
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

    console.log('??NFC ?°ê¸° ?„ë£Œ!')
    return {
      success: true,
      slotNumber,
      videoId,
      message: '?¬ë¡¯???°ì´?°ê? ê¸°ë¡?˜ì—ˆ?µë‹ˆ??',
    }
  } catch (error: unknown) {
    const err = error as Error
    console.error('??NFC ?°ê¸° ?¤íŒ¨:', error)
    return {
      success: false,
      slotNumber,
      videoId,
      message: err.message || 'NFC ?°ê¸°???¤íŒ¨?ˆìŠµ?ˆë‹¤.',
    }
  }
}

/**
 * NFC ?½ê¸°
 */
export const readFromNFCSlot = async (): Promise<unknown> => {
  try {
    if (!('NDEFReader' in window)) {
      throw new Error('??ê¸°ê¸°??NFCë¥?ì§€?í•˜ì§€ ?ŠìŠµ?ˆë‹¤.')
    }

    const ndef = new (window as unknown as { NDEFReader: new () => NDEFReader }).NDEFReader()
    await ndef.scan()

    return new Promise((resolve, reject) => {
      ndef.addEventListener('reading', (event: Event) => {
        const readingEvent = event as Event & { message: NDEFMessage }
        const record = readingEvent.message.records[0]
        const textDecoder = new TextDecoder()
        const data = JSON.parse(textDecoder.decode(record.data))
        resolve(data)
      })

      setTimeout(() => reject(new Error('NFC ?½ê¸° ?œê°„ ì´ˆê³¼')), 10000)
    })
  } catch (error) {
    console.error('??NFC ?½ê¸° ?¤íŒ¨:', error)
    throw error
  }
}

