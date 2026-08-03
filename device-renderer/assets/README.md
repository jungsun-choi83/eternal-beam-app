# assets/ — 표준화된 콘텐츠 저장 위치 (렌더러 무관)

`AssetManager`는 **어떤 `IPetRenderer`가 활성인지 전혀 모릅니다** — `assets/<pet_id>/<place_id>/`
디렉터리가 존재하고 비어있지 않으면 그대로 `IPetRenderer::loadAsset()`에 넘길 뿐입니다.
디렉터리 안에 무엇이 들어있는지는 활성 렌더러가 정합니다:

```
assets/
├── manifest.json                 ← 로컬에 내려받은 콘텐츠의 버전 기록 (AssetManager가 관리)
└── <pet_id>/
    └── <place_id>/
        │
        │  HttpDeviceSyncClient가 매 sync마다 기록 (렌더러 무관 — CreateRendererForAssetDir()이 읽음):
        ├── sync_meta.json         ← {"asset_type": "video" | "spine"} (서버가 선언한 콘텐츠 종류)
        │
        │  SpineRenderer(ETERNALBEAM_WITH_SPINE=ON)가 활성일 때:
        ├── skeleton.json          ← Spine 스켈레톤 (본 계층, 슬롯, 스킨, 애니메이션)
        ├── skeleton.atlas         ← 텍스처 아틀라스 좌표
        ├── skeleton.png           ← 아틀라스 페이지 이미지
        │
        │  VideoLayerRenderer(ETERNALBEAM_WITH_FFMPEG=ON)가 활성일 때 (HttpDeviceSyncClient가 생성):
        └── video_manifest.json    ← {"motions": {"idle": {"video_url": "..."}, ...}}
```

- `pet_id`, `place_id`는 백엔드(`backend/scenarios/pet_scenarios.py`)의 명명 규칙과
  동일하게 맞춰서, 기존 `theme_key` / `place_key` 값을 그대로 재사용합니다.
- `sync_meta.json`은 `GET /v1/device/sync` 응답의 `asset_type` 필드를 그대로 옮겨 적은
  것입니다(`../include/renderer/asset_type.h`). `skeleton.*`/`video_manifest.json`이 실제로
  존재하는지와는 별개의 파일입니다 — "서버는 spine이라 했는데 로컬엔 아직 리깅 파일이 없는"
  과도기 상태를 표현할 수 있어야 렌더러 팩토리(`CreateRendererForAssetDir`, `device-renderer/README.md`
  "콘텐츠 기반 렌더러 자동 선택" 섹션)가 안전하게 video로 폴백할 수 있기 때문입니다.
- `video_manifest.json`은 **영상 바이트를 담지 않습니다** — `GET /v1/device/sync`가 돌려준
  URL만 저장하고, `VideoLayerRenderer`가 그 URL을 직접 스트리밍합니다(Unity `VideoPlayer.url`과 동일한
  방식). 반면 Spine 경로는 실제 스켈레톤/텍스처 파일을 이 폴더 안에 내려받아 둡니다(다운로드
  구현은 아직 TODO — device-renderer/README.md "확장 지점" 참고).
- 배경(장소) 비디오는 이 폴더에 포함하지 않습니다 — 배경은 기존처럼 별도 비디오 재생
  경로를 유지하거나(하이브리드), 필요 시 별도 폴더로 분리합니다.

## manifest.json 스키마 (예시)

`AssetEntry`는 렌더러 무관하게 `version` 문자열 하나만 들고 있습니다 — 실제 파일 목록은
디렉터리 자체가 진실의 원천이고(렌더러가 필요한 파일명을 알아서 찾음), manifest는 오직
"다시 내려받아야 하는지"만 판단하는 용도입니다.

```json
{
  "schema_version": 1,
  "entries": {
    "pet123::snow_forest": {
      "version": "2026-07-18T02:10:00Z"
    }
  }
}
```

이 파일은 커밋하지 않습니다(`.gitignore` 참고) — 실제 내용은 기기별로 다운로드된
런타임 데이터이기 때문입니다. 이 문서와 `.gitkeep`만 저장소에 유지합니다.
