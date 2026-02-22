/**
 * Supabase Storage 연동 (서버 사이드)
 * - 합성 결과 MP4 업로드
 * - Pre-signed URL 생성
 */

import { createClient } from '@supabase/supabase-js'
import fs from 'fs'
import path from 'path'

const supabaseUrl = process.env.SUPABASE_URL || process.env.VITE_SUPABASE_URL || ''
const supabaseServiceKey = process.env.SUPABASE_SERVICE_ROLE_KEY || ''

const supabase = supabaseUrl && supabaseServiceKey
  ? createClient(supabaseUrl, supabaseServiceKey)
  : null

const BUCKET = 'user-media'

export async function uploadToSupabase(
  userId,
  localFilePath,
  contentId
) {
  if (!supabase) {
    throw new Error('Supabase가 설정되지 않았습니다. SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY를 확인하세요.')
  }

  const stats = fs.statSync(localFilePath)
  const fileSize = stats.size
  const ext = path.extname(localFilePath)
  const objectPath = `${userId}/${contentId || Date.now()}${ext}`

  const fileBuffer = fs.readFileSync(localFilePath)

  const { error } = await supabase.storage
    .from(BUCKET)
    .upload(objectPath, fileBuffer, {
      contentType: 'video/mp4',
      upsert: true,
    })

  if (error) throw error

  const { data: signed } = await supabase.storage
    .from(BUCKET)
    .createSignedUrl(objectPath, 60 * 60 * 24) // 24시간

  return {
    objectPath,
    fileSize,
    signedUrl: signed?.signedUrl || null,
    publicUrl: supabase.storage.from(BUCKET).getPublicUrl(objectPath).data?.publicUrl,
  }
}

export async function updateUserQuota(userId, addedStorageBytes, incrementGeneration = true) {
  if (!supabase) return

  const { error } = await supabase.rpc('increment_user_quota', {
    p_user_id: userId,
    p_added_storage_bytes: addedStorageBytes,
    p_increment_generation: incrementGeneration,
  })

  if (error) throw error
}

export async function insertUserMedia(userId, objectPath, fileSize, mediaType, contentId) {
  if (!supabase) return

  await supabase.from('user_media').insert({
    user_id: userId,
    file_path: objectPath,
    file_size_bytes: fileSize,
    media_type: mediaType,
    content_id: contentId || null,
  })
}
