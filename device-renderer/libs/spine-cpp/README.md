# libs/spine-cpp

이 폴더는 [Spine Runtimes](https://github.com/EsotericSoftware/spine-runtimes)의
`spine-cpp` 런타임을 벤더링하기 위한 자리입니다. 라이선스 문제로 이 저장소에는
소스를 포함하지 않았습니다 — Spine 에디터로 제작한 리깅 데이터를 재생하려면
Esoteric Software의 Spine 런타임 라이선스가 필요합니다.

## 설치 방법

1. `spine-runtimes` 저장소에서 `spine-cpp/spine-cpp` 디렉터리만 이 폴더 밑에
   `spine-cpp/` 이름으로 복사하거나 git submodule로 추가합니다:

   ```bash
   git submodule add https://github.com/EsotericSoftware/spine-runtimes.git libs/spine-runtimes
   ```

   이후 최상위 `CMakeLists.txt`의 `ETERNALBEAM_WITH_SPINE` 옵션을 `ON`으로 켜고,
   `SPINE_CPP_SOURCE_DIR`을 벤더링한 실제 경로로 지정하세요.

2. `cmake -DETERNALBEAM_WITH_SPINE=ON -DSPINE_CPP_SOURCE_DIR=libs/spine-runtimes/spine-cpp/spine-cpp -B build`

3. 벤더링 전까지는 `src/renderer/stub_renderer.cpp`가 기본으로 빌드되어, 애니메이션
   전환 로직과 하드웨어 이벤트 파이프라인을 Spine 없이도 개발/테스트할 수 있습니다.

`include/renderer/pet_renderer.h`의 `IPetRenderer`만 구현하면 되므로, Spine 대신
다른 2D 스켈레톤 런타임(예: DragonBones)으로 교체하는 것도 동일한 확장 지점을 씁니다 —
`SpineRenderer`/`VideoLayerRenderer`와 나란히 세 번째 구현체를 추가하고 `renderer_factory.cpp`에
분기 하나만 더하면 됩니다.
