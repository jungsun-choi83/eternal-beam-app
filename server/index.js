/**
 * 에버로그 FFmpeg 합성 API 서버
 * - 음성 + 음악 믹싱
 * - 영상 합성 (H.264/AAC, Pi Zero 2W 최적화)
 */

import { config } from 'dotenv'
import path from 'path'
import { fileURLToPath } from 'url'
config({ path: path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../.env') })

import express from 'express'
import cors from 'cors'
import fs from 'fs'
import multer from 'multer'
import { synthesizeVideo } from './ffmpegSynthesizer.js'
import { uploadToSupabase, updateUserQuota, insertUserMedia } from './supabaseStorage.js'

const __dirname = path.dirname(fileURLToPath(import.meta.url))

const app = express()
const PORT = process.env.SERVER_PORT || 3001

app.use(cors({ origin: '*' }))
app.use(express.json())

const uploadDir = path.join(__dirname, 'uploads')
const outputDir = path.join(__dirname, 'output')
const musicDir = path.join(__dirname, '..', 'public', 'assets', 'music')

for (const dir of [uploadDir, outputDir]) {
  if (!fs.existsSync(dir)) fs.mkdirSync(dir, { recursive: true })
}

const storage = multer.diskStorage({
  destination: (req, file, cb) => cb(null, uploadDir),
  filename: (req, file, cb) =>
    cb(null, `${Date.now()}_${file.originalname.replace(/[^a-zA-Z0-9.-]/g, '_')}`),
})

const upload = multer({
  storage,
  limits: { fileSize: 50 * 1024 * 1024 }, // 50MB
})

// 기본 음악 라이브러리 목록
app.get('/api/music/list', (req, res) => {
  try {
    if (!fs.existsSync(musicDir)) {
      return res.json([])
    }
    const files = fs.readdirSync(musicDir).filter((f) =>
      /\.(mp3|m4a|aac|wav)$/i.test(f)
    )
    const base = '/assets/music'
    const list = files.map((f) => ({
      id: path.basename(f, path.extname(f)),
      name: path.basename(f, path.extname(f)).replace(/_/g, ' '),
      url: `${base}/${f}`,
    }))
    res.json(list)
  } catch (e) {
    res.status(500).json({ error: e.message })
  }
})

// 합성 API
app.post(
  '/api/synthesize',
  upload.fields([
    { name: 'voice', maxCount: 1 },
    { name: 'music', maxCount: 1 },
    { name: 'video', maxCount: 1 },
    { name: 'image', maxCount: 1 },
  ]),
  async (req, res) => {
    const voiceFile = req.files?.voice?.[0]
    const musicFile = req.files?.music?.[0]
    const videoFile = req.files?.video?.[0]
    const imageFile = req.files?.image?.[0]

    const voiceVolume = Math.min(100, Math.max(0, Number(req.body.voiceVolume) || 80))
    const musicVolume = Math.min(100, Math.max(0, Number(req.body.musicVolume) || 20))
    const musicTrimStart = Math.max(0, Number(req.body.musicTrimStart) || 0)
    const musicTrimEnd = Number(req.body.musicTrimEnd) || 0
    const fadeOutSeconds = Math.min(10, Math.max(0, Number(req.body.fadeOutSeconds) || 2))
    const musicLibraryId = req.body.musicLibraryId || ''
    const userId = req.body.userId || 'anonymous'
    const planType = req.body.planType || 'basic'
    const contentId = req.body.contentId || `content_${Date.now()}_${Math.random().toString(36).slice(2, 9)}`
    const useSupabaseStorage = !!(process.env.SUPABASE_URL && process.env.SUPABASE_SERVICE_ROLE_KEY)

    const PLAN_MAX_HEIGHT = { basic: 720, premium: 1080, lifetime: 1080 }
    const maxHeight = PLAN_MAX_HEIGHT[planType] || 720

    if (!voiceFile) {
      return res.status(400).json({ error: '음성 파일(voice)이 필요합니다.' })
    }

    let musicPath = musicFile?.path
      ? musicFile.path
      : musicLibraryId
        ? path.join(musicDir, `${musicLibraryId}.mp3`)
        : null

    if (musicLibraryId && musicPath && !fs.existsSync(musicPath)) {
      try {
        if (fs.existsSync(musicDir)) {
          const alt = fs.readdirSync(musicDir).find((f) =>
            f.startsWith(musicLibraryId)
          )
          if (alt) musicPath = path.join(musicDir, alt)
        }
      } catch {}
    }

    const videoPath = videoFile?.path || null
    const imagePath = imageFile?.path || null

    try {
      const result = await synthesizeVideo({
        voicePath: voiceFile.path,
        musicPath: musicPath && fs.existsSync(musicPath) ? musicPath : null,
        videoPath,
        imagePath,
        voiceVolume,
        musicVolume,
        musicTrimStart,
        musicTrimEnd,
        fadeOutSeconds,
        outputDir,
        maxHeight,
      })

      let outputUrl = `/output/${path.basename(result.outputPath)}`
      let signedUrl = null

      if (useSupabaseStorage) {
        try {
          const { objectPath, fileSize, signedUrl: s } = await uploadToSupabase(
            userId,
            result.outputPath,
            contentId
          )
          signedUrl = s
          outputUrl = objectPath
          await updateUserQuota(userId, fileSize, true)
          await insertUserMedia(userId, objectPath, fileSize, 'video', contentId)
          try { fs.unlinkSync(result.outputPath) } catch {}
        } catch (supabaseErr) {
          console.error('Supabase 업로드 실패, 로컬 URL 반환:', supabaseErr)
        }
      }

      res.json({
        success: true,
        outputUrl,
        signedUrl,
        outputPath: result.outputPath,
        contentId,
      })
    } catch (err) {
      console.error('합성 실패:', err)
      res.status(500).json({
        error: err.message || '영상 합성 중 오류가 발생했습니다.',
      })
    } finally {
      // 업로드 파일 정리
      for (const f of [voiceFile, musicFile, videoFile, imageFile].filter(Boolean)) {
        try {
          if (f?.path && fs.existsSync(f.path)) fs.unlinkSync(f.path)
        } catch {}
      }
    }
  }
)

// 합성 결과 파일 서빙
app.use('/output', express.static(outputDir))

// Pi 기기로 업로드 & 재생 트리거 (프록시용 - 실제 구현은 Pi 쪽 API 호출)
app.post('/api/device/:deviceId/upload', express.json(), async (req, res) => {
  const { deviceId } = req.params
  const { outputPath, localPath } = req.body
  if (!outputPath && !localPath) {
    return res.status(400).json({ error: 'outputPath 또는 localPath 필요' })
  }
  const filePath = localPath || path.join(outputDir, path.basename(outputPath))
  if (!fs.existsSync(filePath)) {
    return res.status(404).json({ error: '파일을 찾을 수 없습니다.' })
  }
  // TODO: Wi-Fi로 Pi에 파일 전송 (scp, HTTP upload 등)
  console.log('[Device Upload]', deviceId, filePath)
  res.json({ success: true, message: '전송 요청이 등록되었습니다.' })
})

app.post('/api/device/:deviceId/play', async (req, res) => {
  const { deviceId } = req.params
  // TODO: Firebase/Supabase 등으로 Pi에 재생 트리거
  console.log('[Play Trigger]', deviceId)
  res.json({ success: true, message: '재생 명령이 전송되었습니다.' })
})

app.listen(PORT, () => {
  console.log(`🎬 에버로그 합성 API: http://localhost:${PORT}`)
})
