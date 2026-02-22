import { db } from '../config/firebase'
import {
  collection,
  doc,
  getDoc,
  updateDoc,
  setDoc,
  query,
  where,
  getDocs,
  serverTimestamp,
  onSnapshot,
  type Unsubscribe,
} from 'firebase/firestore'
import type { Device, ContentPermission, User, FinalContent } from '../types/firebase'

// ========================================
// 1. 기기 등록 관련
// ========================================

/**
 * QR 코드 스캔 → 기기 등록
 * devices 컬렉션의 user_id를 현재 로그인한 사용자로 업데이트
 */
export const registerDevice = async (
  deviceId: string,
  userId: string,
): Promise<boolean> => {
  try {
    const deviceRef = doc(db, 'devices', deviceId)
    const deviceSnap = await getDoc(deviceRef)

    if (!deviceSnap.exists()) {
      throw new Error('존재하지 않는 기기입니다. QR 코드를 확인하세요.')
    }

    const device = deviceSnap.data() as Device

    if (device.user_id && device.user_id !== userId) {
      throw new Error('이미 다른 사용자에게 등록된 기기입니다.')
    }

    await updateDoc(deviceRef, {
      user_id: userId,
      registered_at: serverTimestamp(),
    })

    const userRef = doc(db, 'users', userId)
    const userSnap = await getDoc(userRef)

    if (userSnap.exists()) {
      const userData = userSnap.data() as User
      const devices = userData.registered_devices || []

      if (!devices.includes(deviceId)) {
        await updateDoc(userRef, {
          registered_devices: [...devices, deviceId],
        })
      }
    }

    console.log('✅ 기기 등록 성공:', deviceId)
    return true
  } catch (error) {
    console.error('❌ 기기 등록 실패:', error)
    throw error
  }
}

/**
 * 사용자의 등록된 기기 목록 조회
 */
export const getUserDevices = async (userId: string): Promise<Device[]> => {
  try {
    const q = query(
      collection(db, 'devices'),
      where('user_id', '==', userId),
    )
    const querySnapshot = await getDocs(q)

    const devices: Device[] = []
    querySnapshot.forEach((docSnap) => {
      devices.push({ device_id: docSnap.id, ...docSnap.data() } as Device)
    })

    return devices
  } catch (error) {
    console.error('❌ 기기 목록 조회 실패:', error)
    return []
  }
}

/**
 * 기기 등록 여부 확인
 */
export const isDeviceRegistered = async (userId: string): Promise<boolean> => {
  try {
    const devices = await getUserDevices(userId)
    return devices.length > 0
  } catch {
    return false
  }
}

// ========================================
// 2. 결제 & 권한 관리
// ========================================

/**
 * 결제 완료 후 콘텐츠 권한 생성
 */
export const createContentPermission = async (
  userId: string,
  paymentId: string,
  amount: number,
  videoId: string,
): Promise<string> => {
  try {
    const contentId = `content_${Date.now()}_${Math.random().toString(36).substring(2, 11)}`
    const permissionRef = doc(db, 'content_permissions', contentId)

    const permission: ContentPermission = {
      content_id: contentId,
      user_id: userId,
      payment_id: paymentId,
      payment_status: 'completed',
      amount,
      nfc_write_allowed: true,
      nfc_written: false,
      slot_number: null,
      video_id: videoId,
      created_at: new Date().toISOString(),
      nfc_written_at: null,
    }

    await setDoc(permissionRef, permission)

    console.log('✅ 콘텐츠 권한 생성:', contentId)
    return contentId
  } catch (error) {
    console.error('❌ 권한 생성 실패:', error)
    throw error
  }
}

/**
 * 결제 완료 후 최종 콘텐츠 메타데이터 저장 (합성 미리보기, 배경, 음성 등)
 */
export const saveFinalContent = async (
  contentId: string,
  data: Omit<FinalContent, 'content_id'>,
): Promise<void> => {
  try {
    const contentRef = doc(db, 'contents', contentId)
    const finalContent: FinalContent = {
      ...data,
      content_id: contentId,
    }
    await setDoc(contentRef, finalContent)
    console.log('✅ 최종 콘텐츠 저장:', contentId)
  } catch (error) {
    console.error('❌ 최종 콘텐츠 저장 실패:', error)
    throw error
  }
}

/**
 * NFC 쓰기 권한 확인
 */
export const checkNFCWritePermission = async (
  contentId: string,
): Promise<boolean> => {
  try {
    const permissionRef = doc(db, 'content_permissions', contentId)
    const permissionSnap = await getDoc(permissionRef)

    if (!permissionSnap.exists()) {
      throw new Error('콘텐츠 권한을 찾을 수 없습니다.')
    }

    const permission = permissionSnap.data() as ContentPermission

    if (permission.payment_status !== 'completed') {
      throw new Error('결제가 완료되지 않았습니다.')
    }

    if (!permission.nfc_write_allowed) {
      throw new Error('NFC 쓰기 권한이 없습니다.')
    }

    if (permission.nfc_written) {
      throw new Error('이미 슬롯에 기록된 콘텐츠입니다.')
    }

    return true
  } catch (error) {
    console.error('❌ NFC 권한 체크 실패:', error)
    throw error
  }
}

/**
 * NFC 쓰기 완료 처리
 */
export const markNFCWritten = async (
  contentId: string,
  slotNumber: number,
): Promise<boolean> => {
  try {
    const permissionRef = doc(db, 'content_permissions', contentId)

    await updateDoc(permissionRef, {
      nfc_written: true,
      slot_number: slotNumber,
      nfc_written_at: serverTimestamp(),
    })

    console.log('✅ NFC 쓰기 완료:', contentId, '→ 슬롯', slotNumber)
    return true
  } catch (error) {
    console.error('❌ NFC 쓰기 완료 처리 실패:', error)
    throw error
  }
}

/**
 * 사용자의 콘텐츠 권한 목록 조회
 */
export const getUserContentPermissions = async (
  userId: string,
): Promise<ContentPermission[]> => {
  try {
    const q = query(
      collection(db, 'content_permissions'),
      where('user_id', '==', userId),
    )
    const querySnapshot = await getDocs(q)

    const permissions: ContentPermission[] = []
    querySnapshot.forEach((docSnap) => {
      permissions.push(docSnap.data() as ContentPermission)
    })

    return permissions
  } catch (error) {
    console.error('❌ 콘텐츠 권한 조회 실패:', error)
    return []
  }
}

// ========================================
// 3. 실시간 재생 상태 구독
// ========================================

export interface DevicePlayingState {
  is_playing: boolean
  playback_progress?: number
  device_id: string
}

/**
 * 기기 재생 상태 실시간 구독 (is_playing, playback_progress)
 */
export const subscribeToDevicePlaying = (
  deviceId: string,
  callback: (state: DevicePlayingState) => void,
): Unsubscribe => {
  const deviceRef = doc(db, 'devices', deviceId)

  return onSnapshot(
    deviceRef,
    (snap) => {
      if (snap.exists()) {
        const data = snap.data()
        callback({
          is_playing: data.is_playing ?? false,
          playback_progress: data.playback_progress ?? 0,
          device_id: snap.id,
        })
      }
    },
    (error) => {
      console.error('❌ 기기 상태 구독 실패:', error)
    },
  )
}

// ========================================
// 4. Wi-Fi 설정
// ========================================

/**
 * 기기 Wi-Fi 연결 상태 업데이트
 */
export const updateDeviceWiFi = async (
  deviceId: string,
  connected: boolean,
): Promise<boolean> => {
  try {
    const deviceRef = doc(db, 'devices', deviceId)

    await updateDoc(deviceRef, {
      wifi_connected: connected,
      last_sync: serverTimestamp(),
    })

    console.log('✅ Wi-Fi 상태 업데이트:', deviceId, connected)
    return true
  } catch (error) {
    console.error('❌ Wi-Fi 업데이트 실패:', error)
    return false
  }
}
