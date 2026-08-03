#include "video_layer_renderer.h"

#include <algorithm>
#include <cctype>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <filesystem>
#include <fstream>

#include <nlohmann/json.hpp>

extern "C" {
#include <libavcodec/avcodec.h>
#include <libavformat/avformat.h>
#include <libswscale/swscale.h>
}

namespace eb::renderer {

namespace {

std::string toLowerCopy(std::string s) {
  std::transform(s.begin(), s.end(), s.begin(), [](unsigned char c) { return std::tolower(c); });
  return s;
}

}  // namespace

// --------------------------------------------------------------------------
// DecodeStream
// --------------------------------------------------------------------------

DecodeStream::~DecodeStream() { close(); }

bool DecodeStream::open(const std::string &url) {
  close();

  if (avformat_open_input(&format_ctx, url.c_str(), nullptr, nullptr) < 0) {
    std::fprintf(stderr, "[renderer:video] 열기 실패: %s\n", url.c_str());
    return false;
  }
  if (avformat_find_stream_info(format_ctx, nullptr) < 0) {
    std::fprintf(stderr, "[renderer:video] 스트림 정보 조회 실패: %s\n", url.c_str());
    close();
    return false;
  }

  video_stream_index = av_find_best_stream(format_ctx, AVMEDIA_TYPE_VIDEO, -1, -1, nullptr, 0);
  if (video_stream_index < 0) {
    std::fprintf(stderr, "[renderer:video] 비디오 스트림 없음: %s\n", url.c_str());
    close();
    return false;
  }

  const AVCodecParameters *params = format_ctx->streams[video_stream_index]->codecpar;
  const AVCodec *decoder = avcodec_find_decoder(params->codec_id);
  if (decoder == nullptr) {
    std::fprintf(stderr, "[renderer:video] 디코더 없음 (codec_id=%d): %s\n",
                 static_cast<int>(params->codec_id), url.c_str());
    close();
    return false;
  }

  codec_ctx = avcodec_alloc_context3(decoder);
  if (avcodec_parameters_to_context(codec_ctx, params) < 0 || avcodec_open2(codec_ctx, decoder, nullptr) < 0) {
    std::fprintf(stderr, "[renderer:video] 디코더 열기 실패: %s\n", url.c_str());
    close();
    return false;
  }

  const AVRational rate = format_ctx->streams[video_stream_index]->avg_frame_rate;
  if (rate.num > 0 && rate.den > 0) {
    frame_period_seconds = static_cast<double>(rate.den) / static_cast<double>(rate.num);
  }

  frame = av_frame_alloc();
  rgba_frame = av_frame_alloc();
  packet = av_packet_alloc();
  ended = false;
  return true;
}

void DecodeStream::close() {
  if (sws_ctx != nullptr) {
    sws_freeContext(sws_ctx);
    sws_ctx = nullptr;
  }
  if (packet != nullptr) {
    av_packet_free(&packet);
  }
  if (frame != nullptr) {
    av_frame_free(&frame);
  }
  if (rgba_frame != nullptr) {
    av_frame_free(&rgba_frame);
  }
  if (codec_ctx != nullptr) {
    avcodec_free_context(&codec_ctx);
  }
  if (format_ctx != nullptr) {
    avformat_close_input(&format_ctx);
  }
  video_stream_index = -1;
  ended = false;
}

void DecodeStream::seekToStart() {
  if (format_ctx == nullptr || video_stream_index < 0) {
    return;
  }
  av_seek_frame(format_ctx, video_stream_index, 0, AVSEEK_FLAG_BACKWARD);
  if (codec_ctx != nullptr) {
    avcodec_flush_buffers(codec_ctx);
  }
  ended = false;
}

bool DecodeStream::decodeNextFrame(int dst_w, int dst_h) {
  if (format_ctx == nullptr || codec_ctx == nullptr) {
    return false;
  }

  for (;;) {
    const int read_ret = av_read_frame(format_ctx, packet);
    if (read_ret < 0) {
      ended = true;
      return false;  // EOF or error — caller decides whether to loop.
    }
    if (packet->stream_index != video_stream_index) {
      av_packet_unref(packet);
      continue;
    }

    const int send_ret = avcodec_send_packet(codec_ctx, packet);
    av_packet_unref(packet);
    if (send_ret < 0) {
      continue;
    }

    const int recv_ret = avcodec_receive_frame(codec_ctx, frame);
    if (recv_ret == AVERROR(EAGAIN) || recv_ret == AVERROR_EOF) {
      continue;  // Decoder needs more packets before it can emit a frame.
    }
    if (recv_ret < 0) {
      return false;
    }
    break;  // Got a decoded frame.
  }

  // (Re)build the scaler if this is the first frame or the source format
  // changed (variable-bitrate streams can do this mid-stream).
  const AVPixelFormat dst_fmt = AV_PIX_FMT_RGBA;
  if (sws_ctx == nullptr || rgba_frame->width != dst_w || rgba_frame->height != dst_h) {
    if (sws_ctx != nullptr) {
      sws_freeContext(sws_ctx);
    }
    sws_ctx = sws_getContext(frame->width, frame->height, static_cast<AVPixelFormat>(frame->format), dst_w, dst_h,
                              dst_fmt, SWS_BILINEAR, nullptr, nullptr, nullptr);
    av_frame_unref(rgba_frame);
    rgba_frame->format = dst_fmt;
    rgba_frame->width = dst_w;
    rgba_frame->height = dst_h;
    av_frame_get_buffer(rgba_frame, 32);
  }
  if (sws_ctx == nullptr) {
    return false;
  }

  sws_scale(sws_ctx, frame->data, frame->linesize, 0, frame->height, rgba_frame->data, rgba_frame->linesize);
  return true;
}

// --------------------------------------------------------------------------
// VideoLayerRenderer
// --------------------------------------------------------------------------

VideoLayerRenderer::VideoLayerRenderer() = default;
VideoLayerRenderer::~VideoLayerRenderer() = default;

bool VideoLayerRenderer::loadAsset(const std::string &path) {
  const std::filesystem::path manifest_path = std::filesystem::path(path) / "video_manifest.json";
  std::ifstream in(manifest_path);
  if (!in) {
    std::fprintf(stderr, "[renderer:video] video_manifest.json 없음: %s\n", manifest_path.string().c_str());
    return false;
  }

  nlohmann::json doc;
  try {
    in >> doc;
  } catch (const nlohmann::json::parse_error &e) {
    std::fprintf(stderr, "[renderer:video] video_manifest.json 파싱 실패: %s\n", e.what());
    return false;
  }

  clips_.clear();
  const nlohmann::json &motions = doc.contains("motions") ? doc["motions"] : doc;
  if (!motions.is_object()) {
    std::fprintf(stderr, "[renderer:video] video_manifest.json 형식 오류 (motions 객체 필요)\n");
    return false;
  }

  for (const auto &[action_id, value] : motions.items()) {
    ActionClip clip;
    if (value.is_string()) {
      clip.video_url = value.get<std::string>();
    } else if (value.is_object()) {
      clip.video_url = value.value("video_url", "");
      clip.alpha_video_url = value.value("alpha_video_url", "");
    }
    if (!clip.video_url.empty()) {
      clips_[toLowerCopy(action_id)] = clip;
    }
  }

  if (clips_.empty()) {
    std::fprintf(stderr, "[renderer:video] video_manifest.json 에 유효한 동작이 없습니다: %s\n",
                 manifest_path.string().c_str());
    return false;
  }
  return true;
}

bool VideoLayerRenderer::switchToAction(const std::string &action_name) {
  const auto it = clips_.find(action_name);
  if (it == clips_.end()) {
    std::fprintf(stderr, "[renderer:video] action '%s'에 대한 클립이 없습니다\n", action_name.c_str());
    return false;
  }

  if (!rgb_stream_) {
    rgb_stream_ = std::make_unique<DecodeStream>();
  }
  if (!rgb_stream_->open(it->second.video_url)) {
    return false;
  }

  alpha_stream_.reset();
  if (!it->second.alpha_video_url.empty()) {
    alpha_stream_ = std::make_unique<DecodeStream>();
    if (!alpha_stream_->open(it->second.alpha_video_url)) {
      alpha_stream_.reset();  // Fall back to derived alpha rather than failing outright.
    }
  }

  current_action_ = action_name;
  ended_ = false;
  has_stream_start_time_ = false;
  return true;
}

void VideoLayerRenderer::playAction(const std::string &action_name, bool loop) {
  const std::string lower = toLowerCopy(action_name);
  loop_ = loop;
  if (lower == current_action_ && rgb_stream_ && !rgb_stream_->ended) {
    return;  // Already playing this action — don't restart mid-clip.
  }
  switchToAction(lower);
}

void VideoLayerRenderer::render(FrameBuffer &frame_buffer) {
  frame_buffer.clear();

  if (!rgb_stream_ || rgb_stream_->format_ctx == nullptr) {
    return;  // Nothing loaded yet.
  }

  const auto now = std::chrono::steady_clock::now();
  if (!has_stream_start_time_) {
    stream_start_time_ = now;  // Doubles as "time of last decoded frame" below.
    has_stream_start_time_ = true;
  }

  if (ended_ && !loop_) {
    blitLastFrame(frame_buffer);
    return;
  }

  // Pace decoding to the clip's own frame rate (frame_period_seconds, read
  // from the container) rather than the app's tick rate (main.cpp runs at a
  // fixed 30 FPS) — otherwise a 24fps clip would visibly speed up. If the
  // tick rate is *slower* than the clip's fps we necessarily under-sample;
  // that's an accepted limitation (see README.md "확장 지점"), not a bug.
  const double elapsed = std::chrono::duration<double>(now - stream_start_time_).count();
  if (elapsed < rgb_stream_->frame_period_seconds) {
    blitLastFrame(frame_buffer);
    return;
  }
  stream_start_time_ = now;

  decodeAndBlit(frame_buffer);
}

void VideoLayerRenderer::decodeAndBlit(FrameBuffer &frame_buffer) {
  const int dst_w = frame_buffer.width;
  const int dst_h = frame_buffer.height;
  if (dst_w <= 0 || dst_h <= 0) {
    return;
  }

  bool ok = rgb_stream_->decodeNextFrame(dst_w, dst_h);
  if (!ok && rgb_stream_->ended) {
    if (loop_) {
      rgb_stream_->seekToStart();
      if (alpha_stream_) {
        alpha_stream_->seekToStart();
      }
      ok = rgb_stream_->decodeNextFrame(dst_w, dst_h);
    } else {
      ended_ = true;
      blitLastFrame(frame_buffer);
      return;
    }
  }
  if (!ok) {
    blitLastFrame(frame_buffer);
    return;
  }

  if (alpha_stream_) {
    bool alpha_ok = alpha_stream_->decodeNextFrame(dst_w, dst_h);
    if (!alpha_ok && alpha_stream_->ended && loop_) {
      alpha_stream_->seekToStart();
      alpha_stream_->decodeNextFrame(dst_w, dst_h);
    }
  }

  blitLastFrame(frame_buffer);
}

void VideoLayerRenderer::blitLastFrame(FrameBuffer &frame_buffer) const {
  if (!rgb_stream_ || rgb_stream_->rgba_frame == nullptr || rgb_stream_->rgba_frame->data[0] == nullptr) {
    return;
  }

  const AVFrame *rgb = rgb_stream_->rgba_frame;
  const AVFrame *alpha = (alpha_stream_ && alpha_stream_->rgba_frame && alpha_stream_->rgba_frame->data[0])
                              ? alpha_stream_->rgba_frame
                              : nullptr;

  const int w = std::min(frame_buffer.width, rgb->width);
  const int h = std::min(frame_buffer.height, rgb->height);
  const int dst_stride = frame_buffer.effectiveStride();

  for (int y = 0; y < h; ++y) {
    const std::uint8_t *src_row = rgb->data[0] + static_cast<std::ptrdiff_t>(y) * rgb->linesize[0];
    const std::uint8_t *alpha_row =
        alpha != nullptr ? alpha->data[0] + static_cast<std::ptrdiff_t>(y) * alpha->linesize[0] : nullptr;
    std::uint8_t *dst_row = frame_buffer.pixels + static_cast<std::ptrdiff_t>(y) * dst_stride;

    for (int x = 0; x < w; ++x) {
      const std::uint8_t r = src_row[x * 4 + 0];
      const std::uint8_t g = src_row[x * 4 + 1];
      const std::uint8_t b = src_row[x * 4 + 2];

      std::uint8_t a;
      if (alpha_row != nullptr) {
        // alpha_stream_ was scaled to RGBA too (sws always outputs RGBA
        // here) — its luma lives equally in R/G/B for a grayscale source.
        a = alpha_row[x * 4 + 0];
      } else if (derive_alpha_from_black_bg_) {
        // VideoLayer.cs's original convention: a black background *is* the
        // transparent region ("subject_only: 배경 없이 피사체만"). Luma as
        // alpha reproduces that with an actual alpha channel instead of
        // relying on DLP optics.
        a = static_cast<std::uint8_t>((static_cast<int>(r) * 30 + static_cast<int>(g) * 59 +
                                        static_cast<int>(b) * 11) /
                                       100);
      } else {
        a = 255;
      }

      // Premultiplied alpha, matching FrameBuffer's documented convention
      // and PetShader.shader's blend mode.
      dst_row[x * 4 + 0] = static_cast<std::uint8_t>((static_cast<int>(r) * a) / 255);
      dst_row[x * 4 + 1] = static_cast<std::uint8_t>((static_cast<int>(g) * a) / 255);
      dst_row[x * 4 + 2] = static_cast<std::uint8_t>((static_cast<int>(b) * a) / 255);
      dst_row[x * 4 + 3] = a;
    }
  }
}

void VideoLayerRenderer::setDepth(float z) { depth_ = z; }

}  // namespace eb::renderer
