# device-renderer

Unity를 대체할 임베디드 렌더러입니다. CMake로 빌드되고, **모듈식 아키텍처**로 두 개의
교체 가능한 seam만 존재합니다:

- **입력**: `HardwareInterface` — GPIO/I2C를 직접 폴링하는 `Rk3566Hardware`/`Rpi5Hardware`,
  또는 **기존 Python 센서 브릿지(UDP)를 그대로 받는** `UdpBridgeHardware`.
- **출력**: `IPetRenderer` — 스켈레톤 애니메이션을 그리는 `SpineRenderer`,
  또는 **Unity `VideoLayer.cs`를 그대로 포팅한** 영상 재생 `VideoLayerRenderer`.

`AppController`(하드웨어 이벤트 → 액션 선택)와 `AssetManager`(콘텐츠 동기화)는 위 두 인터페이스
뒤에 있는 구체 구현이 뭔지 전혀 몰라도 되도록 설계되어 있습니다. 하드웨어는 `active_board` 값
하나로 고르지만, **렌더러는 이제 대부분 자동으로 고릅니다** — 서버의 `GET /v1/device/sync` 응답에
실린 `asset_type`(`"spine"`/`"video"`)과 로컬 `assets/` 디렉터리에 실제로 뭐가 있는지를 함께 보고
`AppController::Start()`가 매 sync마다 다시 결정합니다(`CreateRendererForAssetDir()` — 아래
"콘텐츠 기반 렌더러 자동 선택" 섹션 참고). `ETERNALBEAM_RENDERER_BACKEND` 환경변수는 이 자동
판단을 완전히 무시하고 강제로 고정하고 싶을 때(로컬 개발/테스트)만 씁니다. 배경/데이터 구조 분석은
[unity-to-spine-migration-analysis](../unity-to-spine-migration-analysis.canvas.tsx)를 참고하세요.

```
                 ┌─────────────────────┐        ┌──────────────────────┐
 (기존, 무변경)   │  Python 센서 브릿지    │  UDP   │  UdpBridgeHardware    │◄─┐
 pi_sensors_to_   │  (touch/voice/nfc)   │──────► │  (HardwareInterface)  │  │  둘 중 하나만
 unity_udp.py 등  └─────────────────────┘        └──────────────────────┘  │  active_board로 선택
                                                   ┌──────────────────────┐  │
                                     GPIO/I2C ────►│ Rk3566/Rpi5Hardware  │◄─┘
                                                   └──────────┬───────────┘
                                                              │ SensorEvent
                                                              ▼
                                                      ┌───────────────┐
 (신규 필드: asset_type)  GET /v1/device/sync ◄──────│ AppController │
 backend/routers/         HttpDeviceSyncClient        │  .Start()     │
 device_v1.py             (→ sync_meta.json 기록)     └──────┬────────┘
                                                              │ CreateRendererForAssetDir(asset_dir, declared_type)
                                                              ▼
                                                  ┌────────────────────────┐
                                                  │  1) skeleton.*  있음? → Spine (declared_type 무관, 항상 우선)
                                                  │  2) declared=spine인데 없음 → 경고 후 3)으로 폴백 (예외 처리)
                                                  │  3) video_manifest.json 있음? → Video
                                                  │  4) 둘 다 없음 → Stub + 에러 로그
                                                  └───────────┬────────────┘
                                        ┌─────────────────────┴──────────────────┐
                                        ▼                                        ▼
                                 SpineRenderer                          VideoLayerRenderer
                          (skeleton.json+.atlas)                    (video_manifest.json,
                                                                      FFmpeg 디코드)
```

메인 루프 쪽에서 보면 이렇게 씁니다 — `AppController`/`main.cpp`는 실제로 `std::unique_ptr`로
관리하지만(아래 참고), 개념은 정확히 이렇습니다:

```cpp
IPetRenderer* currentRenderer = useSpine ? static_cast<IPetRenderer*>(new SpineRenderer())
                                          : static_cast<IPetRenderer*>(new VideoLayerRenderer());
// 메인 루프 — currentRenderer가 실제로 뭔지 몰라도 됨
currentRenderer->render(frame);
```

## 폴더 구조

```
device-renderer/
├── CMakeLists.txt          ← 최상위 빌드 스크립트
├── cmake/
│   ├── rk3566.toolchain.cmake   ← RK3566 크로스컴파일 툴체인
│   ├── rpi5.toolchain.cmake     ← RPi5 크로스컴파일 툴체인
│   └── CompilerWarnings.cmake
├── config/
│   └── hardware_config.yaml     ← python/hardware_config.yaml 와 동일한 스키마 (+ network 섹션)
├── include/
│   ├── hardware/            ← HardwareInterface, HardwareConfig, SensorEvent (public API)
│   ├── renderer/            ← IPetRenderer, FrameBuffer, RendererBackend (public API)
│   ├── app/                 ← AssetManager, AppController, DeviceSyncClient (public API)
│   └── display/             ← DrmGlDisplay (public API, ETERNALBEAM_WITH_DRM_GL=ON일 때만 빌드)
├── libs/
│   └── spine-cpp/           ← Spine-CPP 벤더링 자리 (README 참고, 기본 미포함)
├── src/
│   ├── app/                 ← AppController + AssetManager + device_sync_client.cpp(옵션) 구현
│   ├── hardware/            ← gpiod/i2c-dev 를 쓰는 유일한 위치(rk3566/rpi5). udp_bridge_impl.cpp는
│   │                          크로스플랫폼(Winsock/BSD sockets) — Windows/macOS 개발 머신에서도 동작.
│   ├── renderer/            ← stub_renderer.cpp(기본) / spine_renderer.cpp(옵션) / video_layer_renderer.cpp(옵션)
│   ├── display/             ← libdrm/GBM/EGL/GLES를 직접 쓰는 유일한 위치(drm_gl_display.cpp)
│   └── main.cpp
├── assets/                  ← 서버에서 받은 콘텐츠(리깅 or 영상 URL) 표준 저장 위치 (assets/README.md)
├── tests/                   ← Catch2 기반 단위 테스트 (옵션)
└── bin/                     ← 빌드 산출물 (git-ignored)
```

## IPetRenderer — 렌더러를 교체 가능하게 만드는 인터페이스

```cpp
class IPetRenderer {
 public:
  virtual bool loadAsset(const std::string &path) = 0;               // 리깅 데이터나 영상 매니페스트 로드
  virtual void playAction(const std::string &action_name, bool loop) = 0;  // 동작 재생
  virtual void render(FrameBuffer &frame_buffer) = 0;                 // 프레임 렌더링
  virtual void setDepth(float z) = 0;                                 // 깊이 설정
  virtual float depth() const = 0;
};
```

(`include/renderer/pet_renderer.h`.)

- **`SpineRenderer`** (`src/renderer/spine_renderer.*`, `ETERNALBEAM_WITH_SPINE=ON`): Unity의
  `VideoLayer` GameObject + `PetShader` 머티리얼이 하던 역할을 대신합니다. `loadAsset(dir)`은
  `dir/skeleton.json` + `dir/skeleton.atlas`를 읽고, `dir`의 상위 폴더명(`pet_id`)을 스킨 이름으로
  시도합니다(공용 템플릿 리그 + 스킨 교체 방식). `loadAsset()`은 `spine::AnimationStateData`에
  `idle`↔`touch`/`voice`/`nfc` 양방향 0.5초 크로스페이드(`setMix`)와 그 외 조합용 기본
  믹스(`setDefaultMix`, 동일 0.5초)를 등록합니다 — `playAction()`이 매번 부르는
  `AnimationState::setAnimation()`이 이 표를 자동으로 참고해 이전 애니메이션과 새 애니메이션을
  블렌딩하므로, `playAction()` 자체는 그대로 하드 컷처럼 보이는 `setAnimation(0, animation, loop)`
  호출 한 줄이지만 실제로는 부드럽게 겹쳐집니다.
- **`VideoLayerRenderer`** (`src/renderer/video_layer_renderer.*`, `ETERNALBEAM_WITH_FFMPEG=ON`):
  Unity `VideoLayer.cs` + `PythonBridge.cs`의 직접적인 C++ 포팅입니다. `loadAsset(dir)`은
  `dir/video_manifest.json`(액션별 video_url)을 읽고, `playAction`으로 지정된 클립을 FFmpeg로
  디코드해 `render()`가 매 틱마다 `FrameBuffer`에 RGBA로 그립니다. 별도 alpha 스트림이 없으면
  Unity의 "검은 배경 = 투명" 관례를 실제 알파 채널로 재현합니다(루마 기반).
- **`StubRenderer`**: 아무 백엔드도 켜지 않았을 때의 기본값 — 호출만 로그로 남기고 그립니다.

두 가지 팩토리가 있습니다:

- `CreateRenderer(RendererBackend)` — 컴파일 시점에 뭐가 켜져 있는지만 보고 고정 선택. 주로
  `ETERNALBEAM_RENDERER_BACKEND` 강제 override, 또는 아래 `CreateRendererForAssetDir()`가 내부적으로
  최종 구현체를 만들 때 씁니다.
- `CreateRendererForAssetDir(asset_dir, declared_type, forced_backend)` — **실제로 쓰이는 쪽.**
  `AppController::Start()`가 매 sync 이후 호출하며, 아래 섹션에서 자세히 설명합니다.

## 콘텐츠 기반 렌더러 자동 선택 (`asset_type`)

`GET /v1/device/sync` 응답에 새로 추가된 `asset_type` 필드(`"spine"` | `"video"`, 기본값
`"video"` — `backend/models/hybrid_business.py::DeviceSyncResponse`)를 기준으로
`AppController::Start()`가 **매 (pet_id, place_id) sync마다** `SpineRenderer`와
`VideoLayerRenderer` 중 무엇을 쓸지 다시 결정합니다. 바이너리를 재시작하거나
`ETERNALBEAM_RENDERER_BACKEND`를 바꿀 필요가 없습니다 — 어떤 반려동물이 방금 리깅됐다면 다음
sync에서 곧바로 `SpineRenderer`로 전환됩니다.

1. **파싱**: `HttpDeviceSyncClient::DownloadInto()`(`src/app/device_sync_client.cpp`)가 응답의
   `asset_type`을 `renderer::ParseAssetType()`으로 파싱해 `assets/<pet_id>/<place_id>/sync_meta.json`에
   저장합니다(`renderer::WriteSyncMeta()`, `include/renderer/asset_type.h`). 이 필드가 없는 오래된
   서버 응답은 `"video"`로 취급합니다(그 전까지는 항상 영상이었으므로). `FetchRemoteVersion()`의
   버전 지문에도 `asset_type`을 포함시켜서, 같은 action_id/video_url이라도 spine↔video 전환은
   "콘텐츠가 바뀌었다"로 감지됩니다.
2. **팩토리**: `AppController::Start()`가 `AssetManager::EnsureLocalAssets()`로 애셋 디렉터리를
   확정한 뒤, `renderer::ReadDeclaredAssetType(asset_dir)`로 위에서 저장한 값을 읽고
   `renderer::CreateRendererForAssetDir(asset_dir, declared_type)`을 호출합니다
   (`include/renderer/renderer_factory.h`).
3. **결정/예외 처리 순서** (`CreateRendererForAssetDir`의 실제 로직):
   1. `asset_dir`에 `skeleton.json` **과** `skeleton.atlas`가 실제로 존재하면 `declared_type`이
      뭐라고 하든 **무조건 `SpineRenderer`**. 리깅 데이터가 로컬에 이미 캐시돼 있는데 서버 플래그가
      아직 안 바뀐 경우까지 흡수합니다.
   2. 그 외에 `declared_type == spine`인데 리깅 파일이 없는 경우(리깅 파이프라인이 아직 해당
      콘텐츠를 만들지 못한 과도기 상태 — **요청하신 예외 처리**) — 경고 로그를 남기고 3번으로
      폴백합니다. `SpineRenderer`에게 로드 불가능한 디렉터리를 넘기지 않습니다.
   3. `asset_dir`에 `video_manifest.json`이 있으면 `VideoLayerRenderer`.
   4. 아무것도 없으면 `StubRenderer` + 에러 로그(지금 당장 콘텐츠가 전혀 없다는 뜻).
4. **override**: `forced_backend`(main.cpp에서 `ETERNALBEAM_RENDERER_BACKEND`로 채워짐)가
   `kAuto`가 아니면 위 로직 전체를 건너뛰고 `CreateRenderer(forced_backend)`로 직행합니다 — 로컬
   개발 중 강제로 `stub`을 쓰고 싶을 때 등.

`sync_meta.json`은 `video_manifest.json`/`skeleton.*`과 별도 파일입니다 — "서버가 뭐라고
선언했는지"와 "로컬에 실제로 뭐가 다운로드됐는지"를 분리해서, 위 2번 예외 케이스(선언은 spine인데
아직 파일이 없음)를 표현할 수 있게 하기 위함입니다. `AssetManager` 자체는 여전히 이 파일의 내용을
전혀 모릅니다 — 렌더러 판단은 순수하게 `renderer::` 네임스페이스에서만 일어납니다.

## 하드웨어 입력 — 기존 UDP 이벤트 처리를 그대로 유지

`UdpBridgeHardware`(`src/hardware/udp_bridge_impl.*`)는 `python/pi_sensors_to_unity_udp.py`,
`voice_to_unity.py`, `eternal_beam_pi.py`, `s23_bridge_simple.py` 등 **기존 Python 센서 브릿지를
전혀 건드리지 않고** 그대로 사용합니다 — 이 파일들이 이미 UDP로 보내는
`{"event":"touch","distance_mm":32}` / `{"event":"voice",...}` / `{"event":"nfc_tagged","uid":...}`
/ `{"event":"idle",...}` JSON 라인을 그대로 받아 `SensorEvent`로 변환합니다
(`ParseUdpSensorEvent()`, `tests/test_udp_bridge.cpp`로 파이썬 쪽 실제 페이로드를 고정 픽스처로
검증). `"approach"`/`"nfc_match"` 같은 정보성 이벤트는 4개 액션(Idle/Touch/Voice/Nfc)에 대응되지
않으므로 의도적으로 무시합니다.

`config/hardware_config.yaml`에서 `active_board: udp_bridge` (또는 `HARDWARE_BOARD=udp_bridge`
환경변수)로 선택하면 GPIO/I2C를 전혀 만지지 않고 이 경로로 동작합니다 — Rk3566Hardware /
Rpi5Hardware(직접 폴링)와 완전히 대체 가능하며, `AppController`는 어느 쪽이 활성인지 모릅니다.
크로스플랫폼 소켓(Winsock2/BSD)이라 Windows/macOS 개발 머신에서도 실제 Python 발신 스크립트를
대상으로 테스트할 수 있습니다.

### `Vl53l0xTouchThread` — VL53L0X 전용 별도 스레드 (선택, `ETERNALBEAM_VL53L0X_TOUCH_THREAD=1`)

위 두 입력 경로(직접 폴링 / UDP 브릿지)와는 별개로, VL53L0X 근접 센서만 자기 스레드에서 독립된
주기로 폴링해 `IPetRenderer::playAction("touch", ...)`를 **직접** 호출하는 최소 지연 경로입니다
(`include/hardware/vl53l0x_touch_thread.h`, `src/hardware/vl53l0x_touch_thread.cpp`,
`ETERNALBEAM_HAS_LINUX_HARDWARE`일 때만 빌드). `HardwareInterface::Poll()` /
`AppController::OnSensorEvent()` / `TriggerAction()`을 완전히 건너뛰므로, `AppController`의 30
FPS 렌더 틱과 무관한 자체 주기(기본 50ms)로 반응할 수 있습니다.

```cpp
eb::hardware::Vl53l0xTouchThread touch_thread(
    *controller.Renderer(),  // AppController::Start() 이후에만 유효
    eb::hardware::Vl53l0xTouchThreadConfig::FromHardwareConfig(hw_config));
touch_thread.Start();
// ... 메인 루프에서 controller.Tick()을 touch_thread.renderer_mutex()로 감싸기 (아래 참고) ...
touch_thread.Stop();
```

- **레인징**: 캘리브레이션 없이(`LinuxCommonHardware::ReadDistanceMm()`와 동일한
  TODO(sensor-bringup) 제약) `SYSRANGE_START`에 단발 레인징을 트리거하고, 공장 기본 타이밍
  버짓(~30ms)만큼 대기한 뒤 `RESULT_RANGE_STATUS+10` 2바이트를 읽습니다.
- **스레드 안전성**: spine-cpp의 `AnimationState`/`Skeleton`은 내부적으로 스레드 안전하지 않습니다.
  이 클래스는 자신이 부르는 `playAction()` 호출을 `renderer_mutex()`로 감싸는데, **같은
  렌더러 인스턴스를 다른 스레드(전형적으로 메인 루프의 `AppController::Tick()`)에서도 건드린다면
  그 호출도 반드시 같은 락으로 감싸야 합니다** — `main.cpp`가 실제로 이렇게 합니다(위
  `ETERNALBEAM_HAS_LINUX_HARDWARE` 블록 참고).
- **중복 트리거 주의**: 같은 물리 VL53L0X 센서에 대해 이 스레드와
  `LinuxCommonHardware::Poll()`의 자체 VL53L0X 경로를 동시에 켜면 터치가 두 경로로 두 번
  트리거될 수 있습니다 — 지금은 `Poll()` 쪽이 아직 미구현 스텁이라 문제없지만, 나중에 그쪽도
  구현하게 되면 둘 중 하나만 쓰세요.

## 서버 동기화 — 기존 `GET /v1/device/sync`를 그대로 호출

`HttpDeviceSyncClient`(`src/app/device_sync_client.*`, `ETERNALBEAM_WITH_CURL=ON`)는
`backend/routers/device_v1.py`의 **엔드포인트/응답 스키마를 그대로** 호출합니다 — Unity가
호출하던 것과 동일한 `GET /v1/device/sync?user_id=&place_id=&pet_id=` 이며, 응답의
`motions[].{action_id,video_url}`을 `assets/<pet_id>/<place_id>/video_manifest.json`으로,
`asset_type`을 `sync_meta.json`으로 저장합니다(영상 바이트 자체는 다운로드하지 않고 URL 스트리밍 —
Unity `VideoPlayer.url`과 동일). `AssetManager`는 `IAssetSyncClient` 인터페이스와 파일 존재
여부만 알기 때문에, 나중에 Spine 리깅 애셋(`skeleton.json`/`.atlas`)을 실제로 내려주는 엔드포인트가
추가되면 `DownloadInto()`에 다운로드 코드만 추가하면 됩니다 — `AssetManager`/`AppController` 쪽은
변경이 필요 없습니다(위 "콘텐츠 기반 렌더러 자동 선택" 섹션 참고).

## 빌드

```bash
# 호스트(Linux)용 기본 빌드 — 실제 GPIO/I2C 없이 mock 하드웨어 + StubRenderer로 동작 확인
cmake -B build
cmake --build build
./bin/eternal_beam_device

# 기존 UDP 브릿지 + VideoLayerRenderer 조합 (당장 배포 가능한 조합 — 리깅 애셋 없이도 동작)
# ETERNALBEAM_RENDERER_BACKEND를 생략하면 서버 asset_type(현재는 항상 "video")을 보고
# 자동으로 VideoLayerRenderer를 고릅니다 — 아래 env var는 그 자동 판단을 생략하고 강제하는 예시.
sudo apt install ffmpeg libavformat-dev libavcodec-dev libswscale-dev libavutil-dev libcurl4-openssl-dev
cmake -B build -DETERNALBEAM_WITH_FFMPEG=ON -DETERNALBEAM_WITH_CURL=ON
cmake --build build
HARDWARE_BOARD=udp_bridge ETERNALBEAM_RENDERER_BACKEND=video \
  ETERNALBEAM_USER_ID=<user_id> ETERNALBEAM_PET_ID=<pet_id> ETERNALBEAM_PLACE_ID=<place_id> \
  ./bin/eternal_beam_device

# Spine 리깅이 준비된 이후: 위와 동일하지만 -DETERNALBEAM_WITH_SPINE=ON 도 켜고
# ETERNALBEAM_RENDERER_BACKEND는 아예 지정하지 않습니다 — 그러면 pet/place별로 서버가
# asset_type=spine을 보내는 것들만 자동으로 SpineRenderer를 쓰고, 나머지는 그대로
# VideoLayerRenderer를 씁니다(하나의 바이너리로 혼재 가능).

# RK3566 / RPi5 크로스컴파일 (렌더러는 spine 또는 video 중 선택)
export RK3566_SYSROOT=/path/to/rk3566/sysroot
cmake -B build-rk3566 -DCMAKE_TOOLCHAIN_FILE=cmake/rk3566.toolchain.cmake \
  -DETERNALBEAM_WITH_FFMPEG=ON -DETERNALBEAM_WITH_CURL=ON
cmake --build build-rk3566

# 테스트 포함 빌드
cmake -B build -DETERNALBEAM_BUILD_TESTS=ON
cmake --build build
ctest --test-dir build
```

Windows/macOS 개발 머신에서는 `src/hardware/rk3566_impl.cpp` 등 Linux 전용 소스가 아예
컴파일 대상에서 빠지지만(`ETERNALBEAM_HAS_LINUX_HARDWARE=OFF`), `UdpBridgeHardware`는
크로스플랫폼이라 그대로 빌드/실행되며, `MockHardware` + `StubRenderer`만으로도
`AppController`/`AssetManager` 로직을 빌드·테스트할 수 있습니다.

첫 `cmake -B build` 실행 시 `yaml-cpp`, `nlohmann_json`(그리고 테스트를 켰다면 `Catch2`)을
FetchContent로 내려받으므로 인터넷 연결이 필요합니다. 이미 설치된 시스템 패키지가 있으면 그걸
우선 사용합니다. FFmpeg/libcurl은 FetchContent로 빌드하지 않고 시스템 패키지를 찾습니다
(`find_package`/`pkg_check_modules`) — 빌드 시간이 몇 시간씩 걸리는 걸 피하기 위함입니다.

## RK3566 보드 없이 지금 RPi5로 테스트하기

`HardwareInterface`/`IPetRenderer` 추상화 덕분에, 이 C++ 프로젝트에서 정말 RK3566 전용인 코드는
`rk3566_impl.cpp`의 (지금은 로그만 찍는) `OnInitialize()`와 `cmake/rk3566.toolchain.cmake`
뿐입니다 — 나머지(`AppController`/`AssetManager`/`IPetRenderer`/`DrmGlDisplay`/
`Vl53l0xTouchThread`/`HttpDeviceSyncClient`)는 전부 보드-무관 Linux 코드이고, `Rpi5Hardware`도
이미 구현/기본값(`active_board: rpi5`)으로 설정되어 있습니다. 즉 python 브릿지들이 이미 돌고
있는 그 RPi5에 이 프로젝트를 그대로 올려서 **네이티브로**(크로스컴파일 불필요 — Pi 위에서 직접
`cmake`/`g++` 실행) 빌드/실행할 수 있습니다.

```powershell
# Windows PC에서 — python/setup_ssh_once.ps1로 키를 이미 등록했다면 비밀번호 불필요
.\device-renderer\sync_to_pi.ps1
```

```bash
# Pi 5 SSH 터미널에서
ssh pi@eternalbeam.local
cd ~/eternal-beam/device-renderer
sudo apt install build-essential cmake git libgpiod-dev \
  libdrm-dev libgbm-dev libegl1-mesa-dev libgles2-mesa-dev \
  ffmpeg libavformat-dev libavcodec-dev libswscale-dev libavutil-dev \
  libcurl4-openssl-dev

cmake -B build -DETERNALBEAM_WITH_FFMPEG=ON -DETERNALBEAM_WITH_CURL=ON \
  -DETERNALBEAM_WITH_DRM_GL=ON -DETERNALBEAM_BUILD_TESTS=ON
cmake --build build -j4
ctest --test-dir build   # 하드웨어 없이도 통과하는 순수 로직 테스트들

# 1) 먼저 headless로 — 화면/센서 없이도 AppController/렌더러 파이프라인 자체를 확인
ETERNALBEAM_DISABLE_DISPLAY=1 ETERNALBEAM_DUMP_FRAME_PPM=/tmp/frame.ppm \
  ETERNALBEAM_USER_ID=<uid> ETERNALBEAM_PET_ID=<pet_id> ETERNALBEAM_PLACE_ID=<place_id> \
  ./bin/eternal_beam_device
# (다른 터미널에서) scp pi@eternalbeam.local:/tmp/frame.ppm . 로 받아서 열어보기

# 2) 실제 HDMI 출력까지 — 데스크톱(Wayfire/labwc/X)이 DRM을 이미 잡고 있으면
#    eglGetPlatformDisplay/drmModeSetCrtc가 실패합니다. raspi-config에서
#    "Boot Options → Console"로 두거나 데스크톱 세션을 끄고 콘솔/SSH로 실행하세요.
HARDWARE_BOARD=rpi5 ETERNALBEAM_VL53L0X_TOUCH_THREAD=1 \
  ETERNALBEAM_USER_ID=<uid> ETERNALBEAM_PET_ID=<pet_id> ETERNALBEAM_PLACE_ID=<place_id> \
  ./bin/eternal_beam_device
```

**지금 시점에 실제로 뭐가 눈에 보이는지 미리 알아두세요**: `SpineRenderer::drawIntoFrameBuffer()`가
아직 TODO라서, Spine 애셋(`asset_type=spine`)으로 뜨면 화면은 매 프레임 `clear()`된 채로
**검은 화면**만 나옵니다(파이프라인 자체는 정상 — modeset/page-flip이 되는지는 확인 가능). 실제로
그림이 보이는 조합은 `VideoLayerRenderer`(`asset_type=video`, 기본값)이므로, RPi5에서
"화면에 뭔가 나오는지"를 검증하려면 서버에 아직 리깅 안 된(즉 영상 URL이 있는) pet_id로
테스트하세요.

**RPi5 vs RK3566에서 유일하게 검증이 필요한 부분**:
- `Vl53l0xTouchThread`/`LinuxCommonHardware`의 I2C 버스/주소(`config/hardware_config.yaml`의
  `boards.rpi5.i2c.bus: 1`, `common.i2c.vl53l0x_address`) — 실제 배선과 `i2cdetect -y 1`로
  주소가 잡히는지 먼저 확인하세요.
- GPIO 칩 이름(`boards.rpi5.gpio.chip: gpiochip0`) — Pi 5는 GPIO를 RP1 사우스브릿지가
  처리하지만 커널이 그대로 `gpiochip0`으로 노출합니다(`rpi5_impl.cpp` 주석 참고). `gpiodetect`로
  실제 이름이 다르면(커널 버전에 따라 달라질 수 있음) 이 값만 고치면 됩니다.
- `DrmGlDisplay`는 RK3566 전용 로직이 전혀 없는 순수 libdrm/GBM/EGL/GLES 코드라 RPi5의
  `vc4-kms-v3d`(Bookworm 이상 기본 활성화된 V3D Mesa 드라이버) 위에서도 동일하게 동작해야
  하지만, 이 환경에서는 실제 하드웨어 검증을 못 했습니다 — RPi5가 바로 그 첫 검증 기회입니다.

## 보드 포팅 절차 (RK3566 예시)

1. `config/hardware_config.yaml`의 `boards.rk3566` 아래 `i2c.bus` / `gpio.chip` /
   `audio.alsa_card` 값을 실제 보드 값으로 채웁니다(`i2cdetect -l`, `gpiodetect` 로 확인).
2. `active_board: rk3566` 로 바꾸거나, 실행 시 `HARDWARE_BOARD=rk3566` 환경변수로 override.
   (센서를 Python UDP 브릿지에 맡기고 싶다면 대신 `udp_bridge`를 선택하세요 — 위 섹션 참고.)
3. `src/hardware/rk3566_impl.cpp`는 이미 존재합니다 — GPIO/I2C 값이 전부 config에서
   오므로, 디스플레이 브링업처럼 정말 SoC 전용인 로직만 여기에 추가하면 됩니다.
4. 필요하면 `cmake/rk3566.toolchain.cmake` 로 크로스컴파일.

새 보드를 추가하는 경우도 동일합니다: `hardware_config.yaml`에 `boards.<name>` 추가 +
`src/hardware/<name>_impl.cpp` 하나 + `hardware_factory.cpp`에 분기 한 줄.

## 디스플레이 출력 (`DrmGlDisplay`, `ETERNALBEAM_WITH_DRM_GL=ON`)

`AppController::LastFrame()`이 매 틱 채워주는 `FrameBuffer`(RGBA8)를 실제 HDMI/DSI 패널로
직접 밀어넣는 경로입니다. X11/Wayland/데스크톱 컴포지터 없이, KMS(커널 DRM) → GBM(스캔아웃
가능한 렌더 타깃) → EGL/GLES(그 GBM 서피스에 그리는 컨텍스트) 순서로 초기화합니다
(`src/display/drm_gl_display.*`, `include/display/drm_gl_display.h`).

`main.cpp`에서의 사용은 이렇습니다 — `Initialize()`가 성공하면 발견된 모드 해상도를
`AppConfig::render_width/height`에 그대로 넣어 `AppController`의 `FrameBuffer`를 그 크기로
맞추고, 매 틱 `Tick()` 이후 `Present()` 한 번만 호출하면 됩니다:

```cpp
eb::display::DrmGlDisplay display;
if (display.Initialize()) {
  app_config.render_width = display.width();
  app_config.render_height = display.height();
}
...
while (running) {
  controller.Tick(delta_seconds);
  if (display_ready) display.Present(controller.LastFrame());  // 업로드+draw+swap+page-flip까지 한 번에
}
display.Shutdown();
```

`Initialize()` 단계 (자세한 설명은 `drm_gl_display.cpp` 상단 주석과 각 단계 함수의 주석 참고):

1. **DRM 오픈 + 탐색** — `/dev/dri/card0` 오픈 → `drmModeGetResources`로 커넥터 목록 조회 →
   연결된(connected) 커넥터 중 HDMI-A 우선, 없으면 DSI/eDP/DPI 우선으로 선택(`PickConnector`) →
   그 커넥터의 preferred 모드(`PickMode`) → encoder → 그 encoder가 구동 가능한 CRTC(`PickCrtc`).
2. **GBM** — `gbm_create_device(drm_fd)` → 모드 해상도로 `gbm_surface_create(...,
   GBM_FORMAT_XRGB8888, GBM_BO_USE_SCANOUT | GBM_BO_USE_RENDERING)`.
3. **EGL** — `eglGetPlatformDisplayEXT(EGL_PLATFORM_GBM_KHR, gbm_dev, ...)`(없으면
   `eglGetDisplay`로 폴백) → `eglInitialize` → `eglBindAPI(EGL_OPENGL_ES_API)` →
   `eglChooseConfig`(RGB888, alpha 0 — XRGB8888과 매칭) → `eglCreateContext`(GLES 2.0) →
   `eglCreateWindowSurface(..., gbm_surface)` → `eglMakeCurrent`.
4. **GLES** — 풀스크린 텍스처드 쿼드용 셰이더(GLSL ES 1.00) 컴파일/링크, VBO, 스트리밍
   텍스처 하나 생성.
5. **첫 modeset** — 빈 프레임을 한 번 그려서 `gbm_surface_lock_front_buffer` →
   `drmModeAddFB2`로 DRM FB id 발급 → `drmModeSetCrtc`로 패널을 즉시 활성화(이후
   `Present()`는 더 가벼운 `drmModePageFlip` 경로만 탑니다).

매 `Present()`는: `FrameBuffer` → `glTexSubImage2D`(크기가 안 바뀌면) 업로드 → 쿼드
드로우 → `eglSwapBuffers` → `gbm_surface_lock_front_buffer`로 다음 버퍼 획득 → (캐시 없으면)
`drmModeAddFB2` → `drmModePageFlip(..., DRM_MODE_PAGE_FLIP_EVENT, ...)` → `drmHandleEvent`로
vblank 이벤트를 받을 때까지 대기(패널 실제 주사율에 맞춰 페이싱됨) → 이전 버퍼
`gbm_surface_release_buffer`로 반납.

```bash
sudo apt install libdrm-dev libgbm-dev libegl1-mesa-dev libgles2-mesa-dev
cmake -B build-rk3566 -DCMAKE_TOOLCHAIN_FILE=cmake/rk3566.toolchain.cmake \
  -DETERNALBEAM_WITH_FFMPEG=ON -DETERNALBEAM_WITH_CURL=ON -DETERNALBEAM_WITH_DRM_GL=ON
cmake --build build-rk3566
# 화면 없이(headless) 확인하고 싶을 때 — DRM 초기화를 건너뛰고 PPM 덤프만 사용
ETERNALBEAM_DISABLE_DISPLAY=1 ETERNALBEAM_DUMP_FRAME_PPM=/tmp/frame.ppm ./bin/eternal_beam_device
```

**범위/제약** (아직 남은 부분):

- 한 KMS 플레인에 `IPetRenderer`가 그린 레이어 하나만 출력합니다. Unity `HologramController`의
  다중 레이어 Z-order(배경 영상 + 반려동물 + 글로우 합성)는 아직 없습니다 — 필요해지면 두 번째
  GBM/EGL 서피스나 DRM 오버레이 플레인을 추가해 합성하는 방향으로 확장합니다.
- 실제 RK3566 보드(및 그 GPU 벤더 드라이버)에서 컴파일/실행 검증은 아직 못했습니다 — 이
  Windows 개발 머신에는 CMake/g++/libdrm 헤더가 전혀 없어서, 표준 kmscube 스타일 DRM+GBM+EGL
  시퀀스를 그대로 따르되 실제 타깃에서의 첫 빌드/부팅 테스트가 필요합니다. 특히 일부 Rockchip
  벤더 BSP는 Mesa 대신 자체 `libmali.so`를 쓰는데, `EGL_KHR_platform_gbm`/
  `eglGetPlatformDisplayEXT` 확장을 지원하지 않으면 `eglGetDisplay(gbm_dev)` 폴백 경로가
  타는지 확인이 필요합니다.
- GLES 2.0(GLSL ES 1.00)만 씁니다 — 가장 폭넓은 임베디드 드라이버 호환성을 위한 선택으로,
  RK3566 Mali-G52는 GLES 3.2까지 지원하지만 이 경로는 그 이상의 기능을 요구하지 않습니다.

## 확장 지점 (아직 완성되지 않은 부분)
- **VL53L0X / PN532 센서 브링업** (GPIO 직결 경로만 해당 — udp_bridge 경로는 필요 없음) —
  `src/hardware/linux_common_hardware.cpp`의 `ReadDistanceMm()` / `ReadNfcUid()`는 I2C 연결만
  되어 있고, ST/PN532 초기화 시퀀스는 `adafruit-circuitpython-vl53l0x` / `-pn532` 드라이버에서
  포팅해야 합니다.
- **Spine-CPP 실제 렌더링** — `src/renderer/spine_renderer.cpp`는 스켈레톤 로드/애니메이션
  전환 로직까지는 실제 spine-cpp API를 쓰지만, 드로우 콜(래스터화 또는 GL 텍스처 업로드)은
  아직 TODO입니다.
- **리깅 애셋 자체 / 자동 리깅** — 이건 코드 문제가 아니라 애셋 제작 파이프라인 결정이
  필요합니다. `unity-to-spine-migration-analysis` 캔버스와
  [`docs/매팅_및_리깅_AI_조사.md`](../docs/매팅_및_리깅_AI_조사.md)(자동 리깅 서비스 조사 결론)를
  참고하세요 — 현재는 `VideoLayerRenderer` 경로(리깅 불필요, 기존 Luma 영상 파이프라인 그대로
  재사용)가 즉시 배포 가능한 유일한 조합입니다. `backend`는 이미 `asset_type` 필드를 보낼 준비가
  됐지만(기본값 `"video"`), 실제로 `"spine"`을 내려주려면 리깅 파이프라인 자체가 먼저 필요합니다.
- **Spine 리깅 애셋 다운로드** — `HttpDeviceSyncClient::DownloadInto()`는 지금 `video_manifest.json`
  + `sync_meta.json`만 씁니다. 서버가 실제로 `skeleton.json`/`.atlas` URL을 내려주게 되면, 그
  함수에 다운로드 로직을 추가하기만 하면 됩니다(TODO 주석이 정확한 위치를 가리킵니다) —
  `AssetManager`/`AppController`/`CreateRendererForAssetDir()`는 이미 그 파일들이 로컬에 있는지만
  보고 동작하므로 추가 변경이 필요 없습니다.
