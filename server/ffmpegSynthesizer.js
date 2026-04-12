/**
 * FFmpeg 기반 영상 합성
 * - 3D 영상(무음) + 음성 + 배경 음악
 * - Pi Zero 2W 최적화: H.264 baseline, AAC, 720p 이하
 * - 오디오 페이드아웃
 *
 * [하드웨어 사양] True Black + DLP 프로젝터 최적화
 * - Pixel Clamping: 흑레벨 0~10 → Pure Black(#000000)
 * - DLP 프로젝터: 검은색 = 빛 없음 = 투명 처리용 인코딩 프로파일
 */

import fs from 'fs'
import path from 'path'
import { spawn } from 'child_process'
import ffmpegStatic from 'ffmpeg-static'

const FFMPEG = ffmpegStatic || 'ffmpeg'
const DURATION_SEC = 10

/** True Black: 0~10 범위 픽셀을 Pure Black으로 클램핑 (0.04 ≈ 10/255) */
const TRUE_BLACK_CURVES = "curves=all='0/0 0.04/0 1/1'"
/** DLP 프로젝터: Full range, 검은색=빛없음(투명) 인식 */
const DLP_ENCODE_OPTS = ['-color_range', 'pc', '-movflags', '+faststart']

function runFfmpeg(args, opts = {}) {
  return new Promise((resolve, reject) => {
    const proc = spawn(FFMPEG, args, {
      stdio: opts.silent ? 'pipe' : 'inherit',
      ...opts,
    })
    proc.on('close', (code) => (code === 0 ? resolve() : reject(new Error(`ffmpeg exit ${code}`))))
    proc.on('error', reject)
  })
}

async function getDuration(filePath) {
  return new Promise((resolve) => {
    const proc = spawn(FFMPEG, [
      '-i', filePath,
      '-f', 'null', '-',
    ], { stdio: ['pipe', 'pipe', 'pipe'] })
    let stderr = ''
    proc.stderr.on('data', (d) => { stderr += d.toString() })
    proc.on('close', () => {
      const m = stderr.match(/Duration: (\d+):(\d+):(\d+)/)
      if (m) {
        const [, h, min, sec] = m.map(Number)
        resolve(h * 3600 + min * 60 + sec)
      } else {
        resolve(DURATION_SEC)
      }
    })
  })
}

export async function synthesizeVideo({
  voicePath,
  musicPath,
  videoPath,
  imagePath,
  voiceVolume = 80,
  musicVolume = 20,
  musicTrimStart = 0,
  musicTrimEnd = 0,
  fadeOutSeconds = 2,
  outputDir,
  maxHeight = 720,
}) {
  const outName = `synthesized_${Date.now()}.mp4`
  const outputPath = path.join(outputDir, outName)

  const voiceVol = voiceVolume / 100
  const musicVol = musicVolume / 100

  // 1) 음성 정규화: WAV
  const voiceWav = path.join(outputDir, `voice_${Date.now()}.wav`)
  await runFfmpeg([
    '-y', '-i', voicePath,
    '-acodec', 'pcm_s16le', '-ar', '44100', '-ac', '2',
    voiceWav,
  ], { silent: true })

  const duration = await getDuration(voiceWav)
  const fadeStart = Math.max(0, duration - fadeOutSeconds)

  // 2) 오디오 믹싱
  const mixedWav = path.join(outputDir, `mixed_${Date.now()}.wav`)
  let filterComplex
  let inputs = ['-i', voiceWav]

  if (musicPath && fs.existsSync(musicPath)) {
    const trimFilter = musicTrimEnd > 0
      ? `atrim=start=${musicTrimStart}:end=${musicTrimEnd},`
      : musicTrimStart > 0
        ? `atrim=start=${musicTrimStart},`
        : ''
    filterComplex = [
      `[0:a]volume=${voiceVol},afade=t=out:st=${fadeStart}:d=${fadeOutSeconds}[v]`,
      `[1:a]${trimFilter}atrim=end=${duration},asetpts=PTS-STARTPTS,volume=${musicVol},afade=t=out:st=${fadeStart}:d=${fadeOutSeconds}[m]`,
      '[v][m]amix=inputs=2:duration=first[aout]',
    ].join(';')
    inputs = ['-i', voiceWav, '-i', musicPath]
  } else {
    filterComplex = `[0:a]volume=${voiceVol},afade=t=out:st=${fadeStart}:d=${fadeOutSeconds}[aout]`
  }

  await runFfmpeg([
    '-y', ...inputs,
    '-filter_complex', filterComplex,
    '-map', '[aout]',
    '-acodec', 'pcm_s16le', '-ar', '44100', '-ac', '2',
    '-t', String(duration),
    mixedWav,
  ], { silent: true })

  try { fs.unlinkSync(voiceWav) } catch {}

  // 3) 영상 소스
  let videoInput = videoPath
  if (!videoInput && imagePath && fs.existsSync(imagePath)) {
    const imgVideo = path.join(outputDir, `img_${Date.now()}.mp4`)
    await runFfmpeg([
      '-y', '-loop', '1', '-i', imagePath,
      '-vf', TRUE_BLACK_CURVES,
      '-c:v', 'libx264', '-t', String(duration),
      '-pix_fmt', 'yuv420p', '-profile:v', 'baseline', '-level', '3.0',
      ...DLP_ENCODE_OPTS,
      imgVideo,
    ], { silent: true })
    videoInput = imgVideo
  }

  if (!videoInput) {
    const blackVideo = path.join(outputDir, `black_${Date.now()}.mp4`)
    await runFfmpeg([
      '-y', '-f', 'lavfi', '-i', `color=c=black:s=1280x720:d=${duration}`,
      '-c:v', 'libx264', '-pix_fmt', 'yuv420p',
      '-profile:v', 'baseline', '-level', '3.0',
      ...DLP_ENCODE_OPTS,
      blackVideo,
    ], { silent: true })
    videoInput = blackVideo
  }

  // 4) 최종 합성: H.264 + AAC, Pi Zero 2W 최적화 + True Black + DLP 프로젝터
  const scaleVf = `scale=trunc(iw/2)*2:min(${maxHeight},trunc(ih/2)*2)`
  const vfChain = `${scaleVf},${TRUE_BLACK_CURVES}`
  await runFfmpeg([
    '-y', '-i', videoInput,
    '-i', mixedWav,
    '-c:v', 'libx264',
    '-profile:v', 'baseline', '-level', '3.0',
    '-preset', 'ultrafast',
    '-b:v', '1M', '-maxrate', '1.5M', '-bufsize', '2M',
    '-vf', vfChain,
    '-c:a', 'aac', '-b:a', '128k', '-ar', '44100', '-ac', '2',
    '-shortest',
    ...DLP_ENCODE_OPTS,
    outputPath,
  ], { silent: true })

  try { fs.unlinkSync(mixedWav) } catch {}
  if (videoInput !== videoPath) {
    try { fs.unlinkSync(videoInput) } catch {}
  }

  return { outputPath }
}
