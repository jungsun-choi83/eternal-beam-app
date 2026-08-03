#pragma once

// Only compiled when ETERNALBEAM_WITH_FFMPEG=ON (see CMakeLists.txt) —
// everything in here talks to libavformat/libavcodec/libswscale directly.
//
// This is the direct C++ successor to
// EternalBeam/Assets/Scripts/VideoLayer.cs + PythonBridge.cs: it decodes the
// per-action MP4 (URL or local file) named in video_manifest.json (written
// by DeviceSyncClient from the *unchanged*
// GET /v1/device/sync endpoint response) and blits RGBA frames into the
// shared FrameBuffer, at the right wall-clock pace, looping or not per
// playAction()'s `loop` flag.

#include <chrono>
#include <map>
#include <memory>
#include <string>

#include "renderer/pet_renderer.h"

struct AVFormatContext;
struct AVCodecContext;
struct AVFrame;
struct AVPacket;
struct SwsContext;

namespace eb::renderer {

/// One decodable action clip. `alpha_url` is optional — the current
/// GET /v1/device/sync payload only carries one video_url per action
/// (backend/models/hybrid_business.py::DeviceMotionItem), so most entries
/// leave it empty and VideoLayerRenderer derives alpha from near-black
/// pixels (see derive_alpha_from_black_bg_), matching VideoLayer.cs's original
/// "검은 배경 = DLP 투명(subject_only)" convention — just done with a real
/// alpha channel instead of relying on projector optics. A future backend
/// version that serves a genuinely separate alpha-matte stream (see
/// backend/services/vitmatte_service.py) can populate alpha_url and this
/// class will decode+combine both, same as Unity's dual-VideoPlayer path.
struct ActionClip {
  std::string video_url;
  std::string alpha_video_url;  // optional, empty = derive from black bg
};

/// Internal per-stream decode state — one for the RGB clip, optionally one
/// more for a separate alpha clip. Defined here (not in the .cpp) only
/// because VideoLayerRenderer needs two of them; all FFmpeg types stay
/// opaque pointers so this header never requires <libavformat/avformat.h>
/// et al.
struct DecodeStream {
  AVFormatContext *format_ctx = nullptr;
  AVCodecContext *codec_ctx = nullptr;
  AVFrame *frame = nullptr;
  AVFrame *rgba_frame = nullptr;
  SwsContext *sws_ctx = nullptr;
  AVPacket *packet = nullptr;
  int video_stream_index = -1;
  double frame_period_seconds = 1.0 / 30.0;
  bool ended = false;

  ~DecodeStream();
  bool open(const std::string &url);
  void close();
  /// Decodes exactly one frame, scaled to (dst_w, dst_h) RGBA, into
  /// `rgba_frame`. Returns false at end-of-stream (caller decides whether
  /// to loop). No-op successful return if nothing new is available yet.
  bool decodeNextFrame(int dst_w, int dst_h);
  void seekToStart();
};

/// IPetRenderer implementation that plays one per-action MP4 at a time —
/// the drop-in alternative to SpineRenderer for pets/places that haven't
/// been rigged yet (see docs/매팅_및_리깅_AI_조사.md for why automatic
/// rigging isn't available end-to-end today).
class VideoLayerRenderer : public IPetRenderer {
 public:
  VideoLayerRenderer();
  ~VideoLayerRenderer() override;

  /// `path` is the asset directory (assets/<pet_id>/<place_id>/, same
  /// contract as SpineRenderer::loadAsset) containing video_manifest.json —
  /// see DeviceSyncClient / assets/README.md.
  bool loadAsset(const std::string &path) override;
  void playAction(const std::string &action_name, bool loop) override;
  void render(FrameBuffer &frame_buffer) override;
  void setDepth(float z) override;
  float depth() const override { return depth_; }

 private:
  bool switchToAction(const std::string &action_name);
  void decodeAndBlit(FrameBuffer &frame_buffer);
  void blitLastFrame(FrameBuffer &frame_buffer) const;

  std::map<std::string, ActionClip> clips_;  // keyed by lowercase action name
  std::string current_action_;
  bool loop_ = true;
  bool ended_ = false;

  std::unique_ptr<DecodeStream> rgb_stream_;
  std::unique_ptr<DecodeStream> alpha_stream_;

  float depth_ = 0.0f;
  std::chrono::steady_clock::time_point stream_start_time_{};
  bool has_stream_start_time_ = false;

  // See ActionClip comment — true derives alpha from luminance instead of
  // requiring a dedicated alpha_video_url. Exposed as a field (not just a
  // constant) so a future config knob can flip it per deployment.
  bool derive_alpha_from_black_bg_ = true;
};

}  // namespace eb::renderer
