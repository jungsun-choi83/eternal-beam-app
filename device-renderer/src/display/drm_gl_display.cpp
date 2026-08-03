#include "display/drm_gl_display.h"

// Only compiled when ETERNALBEAM_WITH_DRM_GL=ON (see CMakeLists.txt) — this
// file is the one place in the whole project allowed to talk to
// libdrm/GBM/EGL/GLES directly. Everything above main.cpp only ever sees
// eb::display::DrmGlDisplay.
//
// Pipeline, matching Initialize()'s doc comment: KMS (find a connected
// display + a mode + a CRTC to drive it) -> GBM (a scanout-capable render
// target DRM can actually show) -> EGL (a GLES context that can render into
// that GBM surface) -> GLES (a program that draws IPetRenderer's
// FrameBuffer as a textured fullscreen quad) -> per-frame present (render,
// swap, page-flip, wait for vblank).

#include <cerrno>
#include <cstddef>
#include <cstdio>
#include <cstring>
#include <vector>

#include <fcntl.h>
#include <poll.h>
#include <unistd.h>

#include <drm_fourcc.h>
#include <xf86drm.h>
#include <xf86drmMode.h>

#include <gbm.h>

#include <EGL/egl.h>
#include <EGL/eglext.h>
#include <GLES2/gl2.h>

namespace eb::display {

namespace {

void LogGlErrors(const char *step) {
  for (GLenum err = glGetError(); err != GL_NO_ERROR; err = glGetError()) {
    std::fprintf(stderr, "[display:drm] GL error 0x%04x after %s\n", err, step);
  }
}

bool LogEglErrorIf(bool ok, const char *step) {
  if (!ok) {
    std::fprintf(stderr, "[display:drm] %s 실패 (eglGetError=0x%04x)\n", step, eglGetError());
  }
  return ok;
}

GLuint CompileShader(GLenum type, const char *source) {
  const GLuint shader = glCreateShader(type);
  glShaderSource(shader, 1, &source, nullptr);
  glCompileShader(shader);

  GLint compiled = GL_FALSE;
  glGetShaderiv(shader, GL_COMPILE_STATUS, &compiled);
  if (compiled == GL_FALSE) {
    GLint log_len = 0;
    glGetShaderiv(shader, GL_INFO_LOG_LENGTH, &log_len);
    std::vector<char> log(static_cast<std::size_t>(log_len) + 1, '\0');
    glGetShaderInfoLog(shader, log_len, nullptr, log.data());
    std::fprintf(stderr, "[display:drm] 셰이더 컴파일 실패: %s\n", log.data());
    glDeleteShader(shader);
    return 0;
  }
  return shader;
}

// GLSL ES 1.00 (GLES2) — deliberately not GLSL ES 3.00 so this runs on the
// widest range of embedded Mali/PowerVR/Vivante drivers, not just newer ones.
constexpr const char *kVertexShaderSrc = R"(
attribute vec2 a_pos;
attribute vec2 a_uv;
varying vec2 v_uv;
void main() {
  v_uv = a_uv;
  gl_Position = vec4(a_pos, 0.0, 1.0);
}
)";

constexpr const char *kFragmentShaderSrc = R"(
precision mediump float;
varying vec2 v_uv;
uniform sampler2D u_tex;
void main() {
  gl_FragColor = texture2D(u_tex, v_uv);
}
)";

// Fullscreen triangle strip in NDC, paired with UVs so that FrameBuffer row 0
// (documented as "top-left origin" in renderer/pet_renderer.h) lands at the
// top of the screen (NDC y = +1), not the bottom.
// Layout per vertex: [x, y, u, v].
constexpr float kQuadVertices[] = {
    -1.0F, 1.0F,  0.0F, 0.0F,  // top-left
    -1.0F, -1.0F, 0.0F, 1.0F,  // bottom-left
    1.0F,  1.0F,  1.0F, 0.0F,  // top-right
    1.0F,  -1.0F, 1.0F, 1.0F,  // bottom-right
};

}  // namespace

struct DrmGlDisplay::Impl {
  // --- Step 1: DRM/KMS ------------------------------------------------
  int drm_fd = -1;
  drmModeModeInfo mode{};
  std::uint32_t connector_id = 0;
  std::uint32_t encoder_id = 0;
  std::uint32_t crtc_id = 0;
  drmModeCrtc *saved_crtc = nullptr;  // original mode, restored on Shutdown()
  bool modeset_done = false;

  // --- Step 2: GBM -----------------------------------------------------
  struct gbm_device *gbm_dev = nullptr;
  struct gbm_surface *gbm_surface = nullptr;
  struct gbm_bo *current_bo = nullptr;  // buffer currently on screen

  // --- Step 3: EGL -------------------------------------------------------
  EGLDisplay egl_display = EGL_NO_DISPLAY;
  EGLContext egl_context = EGL_NO_CONTEXT;
  EGLSurface egl_surface = EGL_NO_SURFACE;

  // --- Step 4: GLES ------------------------------------------------------
  GLuint program = 0;
  GLuint vbo = 0;
  GLuint texture = 0;
  GLint attr_pos = -1;
  GLint attr_uv = -1;
  GLint uniform_tex = -1;
  int texture_width = 0;   // last size actually glTexImage2D'd (0 = unallocated)
  int texture_height = 0;

  // --- Per-frame page-flip bookkeeping -----------------------------------
  bool page_flip_pending = false;

  static void OnPageFlipComplete(int /*fd*/, unsigned int /*frame*/, unsigned int /*sec*/,
                                  unsigned int /*usec*/, void *data) {
    auto *self = static_cast<Impl *>(data);
    self->page_flip_pending = false;
  }
};

namespace {

// Small per-gbm_bo cache: each buffer object needs exactly one DRM
// framebuffer id, created once via drmModeAddFB2 and reused for every
// subsequent flip of the same buffer (GBM round-robins a small, fixed pool
// of buffers, so this ends up allocating only 2-3 FB ids total, not one per
// frame). Attached directly to the gbm_bo via gbm_bo_set_user_data so it is
// freed automatically when GBM eventually destroys the buffer.
struct FbUserData {
  std::uint32_t fb_id;
};

void DestroyFbUserData(struct gbm_bo *bo, void *data) {
  auto *fb = static_cast<FbUserData *>(data);
  if (fb == nullptr) {
    return;
  }
  const int drm_fd = gbm_device_get_fd(gbm_bo_get_device(bo));
  drmModeRmFB(drm_fd, fb->fb_id);
  delete fb;
}

/// Returns the DRM framebuffer id for `bo`, creating (and caching) it on
/// first use. Returns 0 on failure.
std::uint32_t GetOrCreateFbId(struct gbm_bo *bo) {
  auto *cached = static_cast<FbUserData *>(gbm_bo_get_user_data(bo));
  if (cached != nullptr) {
    return cached->fb_id;
  }

  const std::uint32_t width = gbm_bo_get_width(bo);
  const std::uint32_t height = gbm_bo_get_height(bo);
  const std::uint32_t stride = gbm_bo_get_stride(bo);
  const std::uint32_t handle = gbm_bo_get_handle(bo).u32;
  const int drm_fd = gbm_device_get_fd(gbm_bo_get_device(bo));

  std::uint32_t handles[4] = {handle, 0, 0, 0};
  std::uint32_t strides[4] = {stride, 0, 0, 0};
  std::uint32_t offsets[4] = {0, 0, 0, 0};

  std::uint32_t fb_id = 0;
  const int ret =
      drmModeAddFB2(drm_fd, width, height, DRM_FORMAT_XRGB8888, handles, strides, offsets, &fb_id, 0);
  if (ret != 0) {
    std::fprintf(stderr, "[display:drm] drmModeAddFB2 실패: %s\n", strerror(-ret));
    return 0;
  }

  gbm_bo_set_user_data(bo, new FbUserData{fb_id}, DestroyFbUserData);
  return fb_id;
}

/// Prefers a currently-connected HDMI-A output, then any other connected
/// digital panel (DSI/eDP/DPI — the usual on-board LCD interfaces),
/// otherwise falls back to the first connected connector of any type.
/// `requested_id`, when non-zero, short-circuits straight to that specific
/// connector (still validated as connected).
drmModeConnector *PickConnector(int drm_fd, drmModeRes *resources, std::uint32_t requested_id) {
  drmModeConnector *fallback = nullptr;
  drmModeConnector *dsi_like = nullptr;

  for (int i = 0; i < resources->count_connectors; ++i) {
    drmModeConnector *connector = drmModeGetConnector(drm_fd, resources->connectors[i]);
    if (connector == nullptr) {
      continue;
    }
    if (connector->connection != DRM_MODE_CONNECTED || connector->count_modes <= 0) {
      drmModeFreeConnector(connector);
      continue;
    }

    if (requested_id != 0 && connector->connector_id == requested_id) {
      return connector;  // Exact match requested — done, ignore type preference.
    }
    if (connector->connector_type == DRM_MODE_CONNECTOR_HDMIA) {
      if (fallback != nullptr) drmModeFreeConnector(fallback);
      if (dsi_like != nullptr) drmModeFreeConnector(dsi_like);
      return connector;  // HDMI-A wins immediately — highest preference.
    }
    if ((connector->connector_type == DRM_MODE_CONNECTOR_DSI || connector->connector_type == DRM_MODE_CONNECTOR_eDP ||
         connector->connector_type == DRM_MODE_CONNECTOR_DPI) &&
        dsi_like == nullptr) {
      dsi_like = connector;
      continue;
    }
    if (fallback == nullptr) {
      fallback = connector;
      continue;
    }
    drmModeFreeConnector(connector);
  }

  if (dsi_like != nullptr) {
    if (fallback != nullptr) drmModeFreeConnector(fallback);
    return dsi_like;
  }
  return fallback;  // May be nullptr — caller checks and reports "no connected display".
}

/// Picks the connector's preferred mode (DRM_MODE_TYPE_PREFERRED), falling
/// back to modes[0] — every connector with count_modes > 0 has at least one
/// usable entry, and drivers list the native/preferred mode first even when
/// the preferred flag is (rarely) unset.
const drmModeModeInfo &PickMode(const drmModeConnector &connector) {
  for (int i = 0; i < connector.count_modes; ++i) {
    if ((connector.modes[i].type & DRM_MODE_TYPE_PREFERRED) != 0) {
      return connector.modes[i];
    }
  }
  return connector.modes[0];
}

/// Finds a CRTC the given encoder can actually drive, via the encoder's
/// `possible_crtcs` bitmask (bit i set => resources->crtcs[i] is usable).
/// Prefers the encoder's already-assigned CRTC (encoder->crtc_id) when set,
/// which is the common case on a freshly-booted system with nothing else
/// holding DRM master.
std::uint32_t PickCrtc(int drm_fd, drmModeRes *resources, drmModeEncoder *encoder) {
  if (encoder->crtc_id != 0) {
    return encoder->crtc_id;
  }
  for (int i = 0; i < resources->count_crtcs; ++i) {
    if ((encoder->possible_crtcs & (1U << i)) != 0) {
      return resources->crtcs[i];
    }
  }
  (void)drm_fd;
  return 0;
}

}  // namespace

DrmGlDisplay::DrmGlDisplay() : impl_(std::make_unique<Impl>()) {}

DrmGlDisplay::~DrmGlDisplay() { Shutdown(); }

// ---------------------------------------------------------------------------
// Step 1: open the DRM node, pick connector + mode + encoder + CRTC.
// ---------------------------------------------------------------------------
bool DrmGlDisplay::Initialize(const DrmGlDisplayConfig &config) {
  const auto log = [&](const char *msg) {
    if (config.verbose_init_logging) {
      std::fprintf(stderr, "[display:drm] %s\n", msg);
    }
  };

  log("1/5 DRM 오픈 + 커넥터/모드/CRTC 탐색");
  impl_->drm_fd = open(config.drm_device_path.c_str(), O_RDWR | O_CLOEXEC);
  if (impl_->drm_fd < 0) {
    std::fprintf(stderr, "[display:drm] %s 오픈 실패: %s\n", config.drm_device_path.c_str(), strerror(errno));
    return false;
  }

  drmModeRes *resources = drmModeGetResources(impl_->drm_fd);
  if (resources == nullptr) {
    std::fprintf(stderr, "[display:drm] drmModeGetResources 실패: %s\n", strerror(errno));
    Shutdown();
    return false;
  }

  drmModeConnector *connector = PickConnector(impl_->drm_fd, resources, config.connector_id);
  if (connector == nullptr) {
    std::fprintf(stderr, "[display:drm] 연결된(connected) 디스플레이 커넥터를 찾지 못했습니다 "
                          "(HDMI/DSI 케이블 연결 확인)\n");
    drmModeFreeResources(resources);
    Shutdown();
    return false;
  }
  impl_->connector_id = connector->connector_id;
  impl_->mode = PickMode(*connector);
  width_ = static_cast<int>(impl_->mode.hdisplay);
  height_ = static_cast<int>(impl_->mode.vdisplay);

  if (connector->encoder_id == 0) {
    std::fprintf(stderr, "[display:drm] 커넥터에 연결된 encoder가 없습니다 (connector_id=%u)\n",
                 impl_->connector_id);
    drmModeFreeConnector(connector);
    drmModeFreeResources(resources);
    Shutdown();
    return false;
  }
  drmModeEncoder *encoder = drmModeGetEncoder(impl_->drm_fd, connector->encoder_id);
  if (encoder == nullptr) {
    std::fprintf(stderr, "[display:drm] drmModeGetEncoder(id=%u) 실패\n", connector->encoder_id);
    drmModeFreeConnector(connector);
    drmModeFreeResources(resources);
    Shutdown();
    return false;
  }
  impl_->encoder_id = encoder->encoder_id;
  impl_->crtc_id = PickCrtc(impl_->drm_fd, resources, encoder);
  drmModeFreeEncoder(encoder);

  if (impl_->crtc_id == 0) {
    std::fprintf(stderr, "[display:drm] encoder(id=%u)를 구동할 수 있는 CRTC를 찾지 못했습니다\n",
                 impl_->encoder_id);
    drmModeFreeConnector(connector);
    drmModeFreeResources(resources);
    Shutdown();
    return false;
  }

  // Save whatever mode was active before we touch anything, so Shutdown()
  // can hand the CRTC back in the state we found it (important if a boot
  // splash / earlier process is still relying on it).
  impl_->saved_crtc = drmModeGetCrtc(impl_->drm_fd, impl_->crtc_id);

  std::fprintf(stderr, "[display:drm] 커넥터=%u encoder=%u crtc=%u 모드=%dx%d@%d\n", impl_->connector_id,
               impl_->encoder_id, impl_->crtc_id, width_, height_, impl_->mode.vrefresh);

  drmModeFreeConnector(connector);
  drmModeFreeResources(resources);

  if (!InitGbm(config) || !InitEgl(config) || !InitGl(config)) {
    Shutdown();
    return false;
  }

  // Step 5: modeset onto a first (blank) buffer now, rather than waiting for
  // the first Present() — this way the panel is already actively scanning
  // out (backlight/sync locked) by the time real frames start arriving, and
  // Present() only ever needs the (cheaper, vblank-synced) page-flip path.
  log("5/5 초기 modeset (빈 프레임)");
  glClearColor(0.0F, 0.0F, 0.0F, 1.0F);
  glClear(GL_COLOR_BUFFER_BIT);
  if (!LogEglErrorIf(eglSwapBuffers(impl_->egl_display, impl_->egl_surface) == EGL_TRUE, "초기 eglSwapBuffers")) {
    Shutdown();
    return false;
  }
  impl_->current_bo = gbm_surface_lock_front_buffer(impl_->gbm_surface);
  if (impl_->current_bo == nullptr) {
    std::fprintf(stderr, "[display:drm] gbm_surface_lock_front_buffer 실패 (초기 프레임)\n");
    Shutdown();
    return false;
  }
  const std::uint32_t fb_id = GetOrCreateFbId(impl_->current_bo);
  if (fb_id == 0) {
    Shutdown();
    return false;
  }
  const int ret = drmModeSetCrtc(impl_->drm_fd, impl_->crtc_id, fb_id, 0, 0, &impl_->connector_id, 1, &impl_->mode);
  if (ret != 0) {
    std::fprintf(stderr, "[display:drm] drmModeSetCrtc 실패: %s\n", strerror(-ret));
    Shutdown();
    return false;
  }
  impl_->modeset_done = true;

  log("초기화 완료 — 화면이 활성화되었습니다");
  return true;
}

// ---------------------------------------------------------------------------
// Step 2: GBM device + scanout-capable surface sized to the chosen mode.
// ---------------------------------------------------------------------------
bool DrmGlDisplay::InitGbm(const DrmGlDisplayConfig &config) {
  if (config.verbose_init_logging) {
    std::fprintf(stderr, "[display:drm] 2/5 GBM 디바이스/서피스 생성\n");
  }

  impl_->gbm_dev = gbm_create_device(impl_->drm_fd);
  if (impl_->gbm_dev == nullptr) {
    std::fprintf(stderr, "[display:drm] gbm_create_device 실패\n");
    return false;
  }

  impl_->gbm_surface = gbm_surface_create(impl_->gbm_dev, static_cast<std::uint32_t>(width_),
                                           static_cast<std::uint32_t>(height_), GBM_FORMAT_XRGB8888,
                                           GBM_BO_USE_SCANOUT | GBM_BO_USE_RENDERING);
  if (impl_->gbm_surface == nullptr) {
    std::fprintf(stderr, "[display:drm] gbm_surface_create 실패 (%dx%d, XRGB8888) — 이 GPU 드라이버가 "
                          "스캔아웃 가능한 GBM 서피스를 지원하지 않을 수 있습니다\n",
                 width_, height_);
    return false;
  }
  return true;
}

// ---------------------------------------------------------------------------
// Step 3: EGL display/context/window-surface over the GBM surface above.
// ---------------------------------------------------------------------------
bool DrmGlDisplay::InitEgl(const DrmGlDisplayConfig &config) {
  if (config.verbose_init_logging) {
    std::fprintf(stderr, "[display:drm] 3/5 EGL 디스플레이/컨텍스트/서피스 생성\n");
  }

  // Prefer the explicit GBM platform extension when the driver advertises
  // it (every modern Mesa build does); fall back to the legacy
  // eglGetDisplay() overload some vendor (non-Mesa) EGL implementations
  // still expect a native display handle through — both end up pointing
  // EGL at the same struct gbm_device*.
  auto get_platform_display =
      reinterpret_cast<PFNEGLGETPLATFORMDISPLAYEXTPROC>(eglGetProcAddress("eglGetPlatformDisplayEXT"));
  if (get_platform_display != nullptr) {
    impl_->egl_display = get_platform_display(EGL_PLATFORM_GBM_KHR, impl_->gbm_dev, nullptr);
  }
  if (impl_->egl_display == EGL_NO_DISPLAY) {
    impl_->egl_display = eglGetDisplay(reinterpret_cast<EGLNativeDisplayType>(impl_->gbm_dev));
  }
  if (impl_->egl_display == EGL_NO_DISPLAY) {
    std::fprintf(stderr, "[display:drm] eglGetPlatformDisplay(EXT)/eglGetDisplay 모두 실패\n");
    return false;
  }

  EGLint egl_major = 0;
  EGLint egl_minor = 0;
  if (!LogEglErrorIf(eglInitialize(impl_->egl_display, &egl_major, &egl_minor) == EGL_TRUE, "eglInitialize")) {
    return false;
  }
  if (!LogEglErrorIf(eglBindAPI(EGL_OPENGL_ES_API) == EGL_TRUE, "eglBindAPI(EGL_OPENGL_ES_API)")) {
    return false;
  }

  const EGLint config_attribs[] = {
      EGL_SURFACE_TYPE, EGL_WINDOW_BIT,
      EGL_RENDERABLE_TYPE, EGL_OPENGL_ES2_BIT,
      EGL_RED_SIZE, 8,
      EGL_GREEN_SIZE, 8,
      EGL_BLUE_SIZE, 8,
      EGL_ALPHA_SIZE, 0,  // Matches GBM_FORMAT_XRGB8888 — no alpha in the scanout buffer itself.
      EGL_NONE,
  };
  EGLConfig egl_config = nullptr;
  EGLint num_configs = 0;
  if (!LogEglErrorIf(eglChooseConfig(impl_->egl_display, config_attribs, &egl_config, 1, &num_configs) == EGL_TRUE &&
                          num_configs > 0,
                      "eglChooseConfig")) {
    return false;
  }

  const EGLint context_attribs[] = {EGL_CONTEXT_CLIENT_VERSION, 2, EGL_NONE};
  impl_->egl_context = eglCreateContext(impl_->egl_display, egl_config, EGL_NO_CONTEXT, context_attribs);
  if (impl_->egl_context == EGL_NO_CONTEXT) {
    std::fprintf(stderr, "[display:drm] eglCreateContext 실패 (eglGetError=0x%04x)\n", eglGetError());
    return false;
  }

  impl_->egl_surface = eglCreateWindowSurface(impl_->egl_display, egl_config,
                                               reinterpret_cast<EGLNativeWindowType>(impl_->gbm_surface), nullptr);
  if (impl_->egl_surface == EGL_NO_SURFACE) {
    std::fprintf(stderr, "[display:drm] eglCreateWindowSurface 실패 (eglGetError=0x%04x)\n", eglGetError());
    return false;
  }

  if (!LogEglErrorIf(eglMakeCurrent(impl_->egl_display, impl_->egl_surface, impl_->egl_surface,
                                     impl_->egl_context) == EGL_TRUE,
                      "eglMakeCurrent")) {
    return false;
  }
  return true;
}

// ---------------------------------------------------------------------------
// Step 4: GLES program (textured fullscreen quad) + streaming texture.
// ---------------------------------------------------------------------------
bool DrmGlDisplay::InitGl(const DrmGlDisplayConfig &config) {
  if (config.verbose_init_logging) {
    std::fprintf(stderr, "[display:drm] 4/5 GLES 셰이더/쿼드/텍스처 준비\n");
  }

  const GLuint vertex_shader = CompileShader(GL_VERTEX_SHADER, kVertexShaderSrc);
  const GLuint fragment_shader = CompileShader(GL_FRAGMENT_SHADER, kFragmentShaderSrc);
  if (vertex_shader == 0 || fragment_shader == 0) {
    return false;
  }

  impl_->program = glCreateProgram();
  glAttachShader(impl_->program, vertex_shader);
  glAttachShader(impl_->program, fragment_shader);
  glLinkProgram(impl_->program);
  glDeleteShader(vertex_shader);
  glDeleteShader(fragment_shader);

  GLint linked = GL_FALSE;
  glGetProgramiv(impl_->program, GL_LINK_STATUS, &linked);
  if (linked == GL_FALSE) {
    GLint log_len = 0;
    glGetProgramiv(impl_->program, GL_INFO_LOG_LENGTH, &log_len);
    std::vector<char> log(static_cast<std::size_t>(log_len) + 1, '\0');
    glGetProgramInfoLog(impl_->program, log_len, nullptr, log.data());
    std::fprintf(stderr, "[display:drm] 프로그램 링크 실패: %s\n", log.data());
    return false;
  }

  impl_->attr_pos = glGetAttribLocation(impl_->program, "a_pos");
  impl_->attr_uv = glGetAttribLocation(impl_->program, "a_uv");
  impl_->uniform_tex = glGetUniformLocation(impl_->program, "u_tex");

  glGenBuffers(1, &impl_->vbo);
  glBindBuffer(GL_ARRAY_BUFFER, impl_->vbo);
  glBufferData(GL_ARRAY_BUFFER, sizeof(kQuadVertices), kQuadVertices, GL_STATIC_DRAW);

  glGenTextures(1, &impl_->texture);
  glBindTexture(GL_TEXTURE_2D, impl_->texture);
  glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_LINEAR);
  glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_LINEAR);
  glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_S, GL_CLAMP_TO_EDGE);
  glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_T, GL_CLAMP_TO_EDGE);

  glViewport(0, 0, width_, height_);
  LogGlErrors("InitGl");
  return true;
}

// ---------------------------------------------------------------------------
// Per-frame: upload FrameBuffer -> draw quad -> swap -> page-flip -> wait.
// ---------------------------------------------------------------------------
namespace {

/// Uploads `frame` into `texture`, reallocating the GL texture storage only
/// when the source size actually changed (the common case — AppController
/// allocates one fixed-size FrameBuffer for the whole run — stays on the
/// cheap glTexSubImage2D path). Handles a non-tightly-packed stride (GLES2
/// has no GL_UNPACK_ROW_LENGTH) by falling back to one glTexSubImage2D call
/// per row instead of a single bulk upload.
void UploadFrameBufferToTexture(const eb::renderer::FrameBuffer &frame, GLuint texture, int *cached_width,
                                 int *cached_height) {
  glBindTexture(GL_TEXTURE_2D, texture);

  const bool needs_realloc = frame.width != *cached_width || frame.height != *cached_height;
  const int tight_stride = frame.width * 4;
  const bool tightly_packed = frame.effectiveStride() == tight_stride;

  if (needs_realloc) {
    glTexImage2D(GL_TEXTURE_2D, 0, GL_RGBA, frame.width, frame.height, 0, GL_RGBA, GL_UNSIGNED_BYTE,
                 tightly_packed ? frame.pixels : nullptr);
    *cached_width = frame.width;
    *cached_height = frame.height;
    if (tightly_packed) {
      return;  // glTexImage2D above already uploaded the pixels.
    }
  }

  if (tightly_packed) {
    glTexSubImage2D(GL_TEXTURE_2D, 0, 0, 0, frame.width, frame.height, GL_RGBA, GL_UNSIGNED_BYTE, frame.pixels);
    return;
  }

  const int stride = frame.effectiveStride();
  for (int y = 0; y < frame.height; ++y) {
    const std::uint8_t *row = frame.pixels + static_cast<std::ptrdiff_t>(y) * stride;
    glTexSubImage2D(GL_TEXTURE_2D, 0, 0, y, frame.width, 1, GL_RGBA, GL_UNSIGNED_BYTE, row);
  }
}

}  // namespace

bool DrmGlDisplay::Present(const eb::renderer::FrameBuffer &frame) {
  if (impl_->egl_display == EGL_NO_DISPLAY || frame.pixels == nullptr) {
    return false;
  }

  // --- draw -------------------------------------------------------------
  glBindFramebuffer(GL_FRAMEBUFFER, 0);
  glViewport(0, 0, width_, height_);

  UploadFrameBufferToTexture(frame, impl_->texture, &impl_->texture_width, &impl_->texture_height);

  glUseProgram(impl_->program);
  glBindBuffer(GL_ARRAY_BUFFER, impl_->vbo);

  glEnableVertexAttribArray(static_cast<GLuint>(impl_->attr_pos));
  glVertexAttribPointer(static_cast<GLuint>(impl_->attr_pos), 2, GL_FLOAT, GL_FALSE, 4 * sizeof(float),
                        reinterpret_cast<const void *>(0));
  glEnableVertexAttribArray(static_cast<GLuint>(impl_->attr_uv));
  glVertexAttribPointer(static_cast<GLuint>(impl_->attr_uv), 2, GL_FLOAT, GL_FALSE, 4 * sizeof(float),
                        reinterpret_cast<const void *>(2 * sizeof(float)));

  glActiveTexture(GL_TEXTURE0);
  glBindTexture(GL_TEXTURE_2D, impl_->texture);
  glUniform1i(impl_->uniform_tex, 0);

  glDrawArrays(GL_TRIANGLE_STRIP, 0, 4);
  LogGlErrors("Present draw");

  // --- swap ---------------------------------------------------------------
  if (!LogEglErrorIf(eglSwapBuffers(impl_->egl_display, impl_->egl_surface) == EGL_TRUE, "eglSwapBuffers")) {
    return false;
  }

  // --- lock next buffer + page-flip ---------------------------------------
  struct gbm_bo *next_bo = gbm_surface_lock_front_buffer(impl_->gbm_surface);
  if (next_bo == nullptr) {
    std::fprintf(stderr, "[display:drm] gbm_surface_lock_front_buffer 실패\n");
    return false;
  }
  const std::uint32_t fb_id = GetOrCreateFbId(next_bo);
  if (fb_id == 0) {
    gbm_surface_release_buffer(impl_->gbm_surface, next_bo);
    return false;
  }

  impl_->page_flip_pending = true;
  const int flip_ret =
      drmModePageFlip(impl_->drm_fd, impl_->crtc_id, fb_id, DRM_MODE_PAGE_FLIP_EVENT, impl_.get());
  if (flip_ret != 0) {
    std::fprintf(stderr, "[display:drm] drmModePageFlip 실패: %s\n", strerror(-flip_ret));
    impl_->page_flip_pending = false;
    gbm_surface_release_buffer(impl_->gbm_surface, next_bo);
    return false;
  }

  // --- wait for the flip to actually land (paces us to the panel's vblank,
  // exactly like eglSwapInterval(1) would on a windowed EGL surface) -------
  drmEventContext event_ctx{};
  event_ctx.version = DRM_EVENT_CONTEXT_VERSION;
  event_ctx.page_flip_handler = Impl::OnPageFlipComplete;

  while (impl_->page_flip_pending) {
    pollfd pfd{impl_->drm_fd, POLLIN, 0};
    const int poll_ret = poll(&pfd, 1, 1000);  // 1s timeout — never block forever on a wedged driver.
    if (poll_ret <= 0) {
      std::fprintf(stderr, "[display:drm] page-flip 이벤트 대기 timeout/오류 — 다음 틱에서 재시도합니다\n");
      impl_->page_flip_pending = false;
      break;
    }
    drmHandleEvent(impl_->drm_fd, &event_ctx);
  }

  // Now that the new buffer is confirmed on screen, the previously-shown one
  // can go back to GBM's pool for reuse.
  if (impl_->current_bo != nullptr) {
    gbm_surface_release_buffer(impl_->gbm_surface, impl_->current_bo);
  }
  impl_->current_bo = next_bo;
  return true;
}

void DrmGlDisplay::Shutdown() {
  if (impl_->egl_display != EGL_NO_DISPLAY) {
    // GL object deletion needs a current context — must happen *before*
    // eglMakeCurrent(..., EGL_NO_CONTEXT) below detaches it, otherwise these
    // calls are silently no-ops and the GPU-side allocations just leak.
    if (impl_->egl_context != EGL_NO_CONTEXT) {
      eglMakeCurrent(impl_->egl_display, impl_->egl_surface, impl_->egl_surface, impl_->egl_context);
      if (impl_->texture != 0) glDeleteTextures(1, &impl_->texture);
      if (impl_->vbo != 0) glDeleteBuffers(1, &impl_->vbo);
      if (impl_->program != 0) glDeleteProgram(impl_->program);
      impl_->texture = impl_->vbo = impl_->program = 0;
    }
    eglMakeCurrent(impl_->egl_display, EGL_NO_SURFACE, EGL_NO_SURFACE, EGL_NO_CONTEXT);

    if (impl_->egl_surface != EGL_NO_SURFACE) {
      eglDestroySurface(impl_->egl_display, impl_->egl_surface);
      impl_->egl_surface = EGL_NO_SURFACE;
    }
    if (impl_->egl_context != EGL_NO_CONTEXT) {
      eglDestroyContext(impl_->egl_display, impl_->egl_context);
      impl_->egl_context = EGL_NO_CONTEXT;
    }
    eglTerminate(impl_->egl_display);
    impl_->egl_display = EGL_NO_DISPLAY;
  }

  if (impl_->current_bo != nullptr && impl_->gbm_surface != nullptr) {
    gbm_surface_release_buffer(impl_->gbm_surface, impl_->current_bo);
    impl_->current_bo = nullptr;
  }
  if (impl_->gbm_surface != nullptr) {
    gbm_surface_destroy(impl_->gbm_surface);
    impl_->gbm_surface = nullptr;
  }
  if (impl_->gbm_dev != nullptr) {
    gbm_device_destroy(impl_->gbm_dev);
    impl_->gbm_dev = nullptr;
  }

  if (impl_->modeset_done && impl_->saved_crtc != nullptr && impl_->saved_crtc->mode_valid != 0 &&
      impl_->drm_fd >= 0) {
    // Best-effort restore of whatever was on screen before Initialize() —
    // e.g. a boot splash — rather than leaving the display in whatever
    // state our last frame left it. Skipped if the CRTC had no valid mode
    // to begin with (e.g. nothing was driving the display yet at boot).
    drmModeSetCrtc(impl_->drm_fd, impl_->saved_crtc->crtc_id, impl_->saved_crtc->buffer_id, 0, 0,
                    &impl_->connector_id, 1, &impl_->saved_crtc->mode);
  }
  if (impl_->saved_crtc != nullptr) {
    drmModeFreeCrtc(impl_->saved_crtc);
    impl_->saved_crtc = nullptr;
  }
  impl_->modeset_done = false;

  if (impl_->drm_fd >= 0) {
    close(impl_->drm_fd);
    impl_->drm_fd = -1;
  }

  width_ = 0;
  height_ = 0;
}

}  // namespace eb::display
