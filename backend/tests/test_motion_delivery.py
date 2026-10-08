"""Phase 7F: raw 회색 배경 Phase 6 영상 → packed-alpha 파생물 포장.

실코덱(ffmpeg) 없이 계약을 검증한다 — 디코드/인코드/업로드는 주입하고,
매트 추출은 **실제 기본 백엔드(bgmodel)** 를 합성 프레임에 그대로 돌린다.
"""

from __future__ import annotations

import anyio
import numpy as np
import pytest
from fastapi import FastAPI

from backend.routers import motion_videos_v1
from backend.services import asset_url_refresh
from backend.services import motion_delivery_service as delivery
from backend.services import motion_publication_service as publication
from backend.services import motion_video_service as motions
from backend.services import matte_hole_qa
from backend.services import pet_identity_service
from backend.services import pet_registry

from .conftest import ASGITestClient

USER = "phase7f@example.com"
OTHER = "other@example.com"
CONTENT = "6f0f2a13-671c-4f67-a842-66ab777bd777"
PET = f"pet_{CONTENT}"
VERSION_ID = "7f000000-0000-4000-8000-000000000001"
CANDIDATE_ID = "7f000000-0000-4000-8000-000000000002"
BUCKET = "user-assets"
RAW_PATH = f"{USER}/{CONTENT}/motions/breathing/v1/seedance_a1_raw.mp4"
PACKED_PATH = f"{USER}/{CONTENT}/motions/breathing/v1/seedance_a1_packed.mp4"

GRAY = np.array([200, 200, 200], dtype=np.uint8)
PET_COLOR = np.array([230, 120, 40], dtype=np.uint8)

W, H = 64, 96
FPS = 24.0


def _run(coro):
    return anyio.run(lambda: coro)


def _frame(with_pet: bool = True) -> np.ndarray:
    """중립 회색 배경 + (옵션) 주황 펫 사각형 — Phase 6 산출물의 최소 모형."""
    f = np.tile(GRAY, (H, W, 1)).astype(np.uint8)
    if with_pet:
        f[30:80, 16:48] = PET_COLOR
    return f


def _frames(n: int = 8) -> list[np.ndarray]:
    return [_frame() for _ in range(n)]


@pytest.fixture(autouse=True)
def _isolated(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("HYBRID_USE_SUPABASE", "0")
    monkeypatch.setenv("ALLOW_INSECURE_TEST_AUTH", "1")
    motions.__reset_for_tests()
    publication.__reset_for_tests()
    pet_registry.__reset_for_tests()
    delivery.__reset_for_tests()
    pet_identity_service.__reset_for_tests()
    yield
    motions.__reset_for_tests()
    publication.__reset_for_tests()
    pet_registry.__reset_for_tests()
    delivery.__reset_for_tests()
    pet_identity_service.__reset_for_tests()


def _seed(*, decision: str = "PASS", raw_path: str = RAW_PATH):
    motions._MOCK_VERSIONS.append(
        {
            "id": VERSION_ID,
            "pet_id": PET,
            "user_id": USER,
            "motion_id": "BREATHING",
            "motion_class": "MICRO",
            "version": 1,
            "status": "complete",
            "selected_candidate_id": CANDIDATE_ID,
            "created_at": "2026-09-03T00:00:00+00:00",
        }
    )
    motions._MOCK_CANDIDATES.append(
        {
            "id": CANDIDATE_ID,
            "motion_version_id": VERSION_ID,
            "pet_id": PET,
            "user_id": USER,
            "motion_id": "BREATHING",
            "provider": "seedance",
            "attempt": 1,
            "raw_bucket": BUCKET,
            "raw_video_path": raw_path,
            "decision": decision,
            "selected": True,
            "generation_metadata": {},
            "created_at": "2026-09-03T00:00:00+00:00",
        }
    )


def _package(**kwargs):
    """실코덱 없이 포장 — 디코드/인코드 주입, 매트는 기본 bgmodel."""
    captured: dict = {}
    frames = kwargs.pop("frames", None)

    def decode(_: bytes):
        return (frames if frames is not None else _frames()), FPS

    def encode(packed, fps):
        captured["packed"] = packed
        captured["fps"] = fps
        return b"PACKED_MP4"

    result = _run(
        delivery.package_breathing_for_delivery(
            user_id=kwargs.pop("user_id", USER),
            pet_id=kwargs.pop("pet_id", PET),
            motion_version_id=kwargs.pop("motion_version_id", VERSION_ID),
            video_bytes=b"RAW_MP4",
            decode_fn=decode,
            encode_fn=encode,
            **kwargs,
        )
    )
    return result, captured


# ══════════════════════════════════════════════════════════════════════════
# 포장 — 파생물 계약
# ══════════════════════════════════════════════════════════════════════════


def test_packaging_stores_derived_and_keeps_raw_immutable():
    _seed()
    result, captured = _package()

    assert result.delivery_format == "packed_alpha"
    assert result.derived_video_path == PACKED_PATH
    assert result.derived_video_path.endswith("_packed.mp4")
    assert result.raw_video_path == RAW_PATH
    assert result.deduplicated is False
    assert result.frame_count == 8
    assert result.matte_backend == "bgmodel"

    cand = motions._MOCK_CANDIDATES[0]
    # raw 는 불변 — 경로도 버킷도 그대로다.
    assert cand["raw_video_path"] == RAW_PATH
    assert cand["raw_bucket"] == BUCKET
    assert cand["derived_video_path"] == PACKED_PATH
    assert cand["delivery_format"] == "packed_alpha"
    assert cand["decision"] == "PASS"  # 절대 변경 금지
    assert cand["generation_metadata"]["delivery"]["format"] == "packed_alpha"
    assert cand["generation_metadata"]["delivery"]["source_raw_video_path"] == RAW_PATH
    # 파생 객체가 (mock) 스토리지에 존재한다.
    assert PACKED_PATH in delivery._MOCK_DELIVERY_OBJECTS


def test_packed_frames_match_browser_contract():
    """vstack 기하 + 매트 절반 무채색 + 알파 방향/범위 — packed-alpha-canvas 거울."""
    _seed()
    _, captured = _package()
    packed = captured["packed"]
    assert len(packed) == 8

    mid = packed[len(packed) // 2]
    assert mid.shape == (2 * H, W, 3)  # 상단 RGB + 하단 알파, 높이 짝수
    assert (2 * H) / W >= 1.0  # 세로 필요조건

    top, bottom = mid[:H], mid[H:]
    assert delivery._avg_chroma(bottom) <= 0.01  # 알파 절반은 정확히 무채색
    assert delivery._avg_chroma(top) > 2.0 * max(0.01, delivery._avg_chroma(bottom))

    # 알파 범위/방향: 펫 중심 ≈ 255, 배경 ≈ 0. (하단 절반이 매트다.)
    alpha = bottom[:, :, 0]
    assert alpha[55, 32] > 230  # 펫 내부
    assert alpha[5, 5] < 10  # 배경 모서리

    # 프리멀티플라이 + 배경 오염 제거: 배경 픽셀은 0 에 가깝고 펫 픽셀은 원본색.
    assert int(top[5, 5].max()) < 8
    assert np.abs(top[55, 32].astype(int) - PET_COLOR.astype(int)).max() < 12


def test_theme_composition_a_b_c_no_gray_no_halo():
    """같은 packed 프레임 → 세 테마 — 회색 사각형 없음, 테마가 투명부에 보임."""
    _seed()
    _, captured = _package()
    mid = captured["packed"][4]
    color = mid[:H].astype(np.float32)
    alpha = (mid[H:, :, 0].astype(np.float32) / 255.0)[..., None]

    themes = {
        "A_night_blue": np.array([20, 30, 90], dtype=np.float32),
        "B_forest_green": np.array([24, 96, 48], dtype=np.float32),
        "C_warm_amber": np.array([180, 120, 30], dtype=np.float32),
    }
    for name, theme in themes.items():
        # 브라우저 합성의 수학적 등가: premult + theme·(1−α)
        out = np.clip(color + theme[None, None, :] * (1.0 - alpha), 0, 255)
        # 배경 영역 = 테마 그대로 (회색 사각형이 없다).
        assert np.abs(out[5, 5] - theme).max() < 2.0, name
        assert np.abs(out[H - 5, W - 5] - theme).max() < 2.0, name
        # 펫 중심 = 펫 원본색 (테마와 무관하게 유지 — 테마 독립성).
        assert np.abs(out[55, 32] - PET_COLOR.astype(np.float32)).max() < 12.0, name
        # 소스 배경 회색(200,200,200)이 그대로 남은 픽셀이 없다 — 회색 후광 금지.
        gray_leak = np.abs(out - GRAY.astype(np.float32)).max(axis=2) < 6.0
        pet_box = np.zeros((H, W), dtype=bool)
        pet_box[30:80, 16:48] = True
        assert not bool(gray_leak[~pet_box].any()), name


def _frame_with_contact_shadow() -> np.ndarray:
    """펫 + 발밑 접지 그림자(배경의 감광 버전) — 실제 산출물의 최소 모형."""
    f = _frame()
    f[80:88, 12:52] = (GRAY.astype(np.float32) * 0.65).astype(np.uint8)
    return f


def test_contact_shadow_does_not_become_foreground_alpha():
    """거리 키잉은 그림자를 불투명 전경으로 만들었다 — v3 가 색조로 걸러낸다."""
    frames = [_frame_with_contact_shadow() for _ in range(6)]
    alphas, diag = delivery.matte_bgmodel(frames)

    assert diag["shadow_reject"]["applied"] is True
    a = alphas[3]
    assert a[84, 32] < 0.05   # 발밑 그림자 — 전경이 아니다
    assert a[55, 32] > 0.95   # 펫 — 그대로 불투명
    assert a[5, 5] < 0.05     # 배경 모서리


def test_shadow_reject_never_erodes_a_pet_that_matches_the_background_hue():
    """배경과 같은 색조의 회색 펫: 지울 양이 과하면 클립 전체에서 포기한다."""
    frames = []
    for _ in range(4):
        f = np.tile(GRAY, (H, W, 1)).astype(np.uint8)
        f[20:90, 8:56] = (GRAY.astype(np.float32) * 0.7).astype(np.uint8)  # 회색 펫
        frames.append(f)
    alphas, diag = delivery.matte_bgmodel(frames)

    assert diag["shadow_reject"]["applied"] is False
    assert diag["shadow_reject"]["reason"] == "would_erode_subject"
    assert alphas[0][55, 32] > 0.95  # 펫은 한 픽셀도 깎이지 않는다


def test_shadow_reject_can_be_turned_off(monkeypatch):
    monkeypatch.setattr(delivery, "SHADOW_REJECT_ENABLED", False)
    alphas, diag = delivery.matte_bgmodel([_frame_with_contact_shadow()] * 3)
    assert diag["shadow_reject"] == {"applied": False, "reason": "disabled"}
    assert alphas[0][84, 32] > 0.95  # v2 거동: 그림자가 전경으로 남는다


def test_temporal_stabilization_fills_single_frame_hole():
    """한 프레임만 매트가 뚫려도(깜빡임) 시간 중앙값이 메운다."""
    frames = _frames(7)
    frames[3] = _frame(with_pet=False)  # 단일 프레임 구멍 — 매트가 완전히 빈다
    _seed()
    _, captured = _package(frames=frames)
    packed = captured["packed"]
    hole_alpha = packed[3][H:, :, 0]
    # 안정화 전이라면 0 — 이웃 프레임 중앙값 + EMA 로 되살아나야 한다.
    assert hole_alpha[55, 32] > 120


def test_stabilize_alpha_reduces_flicker():
    rng = np.random.default_rng(7)
    base = np.zeros((10, 10), dtype=np.float32)
    base[3:7, 3:7] = 1.0
    alphas = [np.clip(base + rng.normal(0, 0.15, base.shape).astype(np.float32), 0, 1) for _ in range(12)]
    stabilized, diag = delivery.stabilize_alpha(alphas)
    assert diag["temporal_median"] is True
    assert diag["mean_frame_delta_after"] < diag["mean_frame_delta_before"]
    assert len(stabilized) == len(alphas)


@pytest.mark.parametrize("count", [1, 2, 3, 4, 9])
def test_rolling_stabilizer_is_pixel_identical_to_batch(count):
    """스트리밍 경로가 기존 3-frame median + EMA 수학을 한 픽셀도 바꾸지 않는다."""
    rng = np.random.default_rng(100 + count)
    frames = [rng.integers(0, 256, (12, 10, 3), dtype=np.uint8) for _ in range(count)]
    alphas = [rng.random((12, 10), dtype=np.float32) for _ in range(count)]
    expected, expected_diag = delivery.stabilize_alpha(alphas)

    rolling = delivery._RollingAlphaStabilizer()
    actual_pairs = []
    for frame, alpha in zip(frames, alphas):
        actual_pairs.extend(rolling.push(frame, alpha))
    actual_pairs.extend(rolling.finish())

    assert len(actual_pairs) == count
    assert rolling.diagnostics() == expected_diag
    for index, (actual_frame, actual_alpha) in enumerate(actual_pairs):
        assert actual_frame is frames[index]
        np.testing.assert_array_equal(actual_alpha, expected[index])


# ══════════════════════════════════════════════════════════════════════════
# 멱등 / 게이트
# ══════════════════════════════════════════════════════════════════════════


def test_packaging_is_idempotent_and_force_repackages():
    _seed()
    first, _ = _package()
    assert first.deduplicated is False

    second, captured = _package()
    assert second.deduplicated is True
    assert second.derived_video_path == first.derived_video_path
    assert "packed" not in captured  # 재인코딩하지 않았다

    third, captured = _package(force=True)
    assert third.deduplicated is False
    assert third.derived_video_path == first.derived_video_path  # 결정론 경로
    assert "packed" in captured


def test_fail_candidate_is_not_packageable():
    _seed(decision="FAIL")
    with pytest.raises(delivery.MotionDeliveryError) as e:
        _package()
    assert e.value.code == "CANDIDATE_NOT_PACKAGEABLE"


def test_review_candidate_is_packageable_for_development():
    """REVIEW 는 포장 가능(개발 입력) — 발행 게이트(QA PASS)는 Phase 7A 소관."""
    _seed(decision="REVIEW")
    result, _ = _package()
    assert result.delivery_format == "packed_alpha"
    assert motions._MOCK_CANDIDATES[0]["decision"] == "REVIEW"  # 결정 불변


def test_wrong_user_rejected():
    _seed()
    with pytest.raises(delivery.MotionDeliveryError) as e:
        _package(user_id=OTHER)
    assert e.value.status == 403


def test_non_packageable_motion_rejected():
    """상용 목록 밖(미판매 신모션)은 포장하지 않는다 — Phase 7H 에서 상용 5종은 열렸다."""
    _seed()
    motions._MOCK_VERSIONS[0]["motion_id"] = "RUN"
    with pytest.raises(delivery.MotionDeliveryError) as e:
        _package()
    assert e.value.code == "MOTION_NOT_PACKAGEABLE"


def test_achromatic_pet_warns_but_packages():
    """무채색(회색) 펫 — 자동감지 대비가 낮아도 실패가 아니라 경고다."""
    frames = []
    for _ in range(6):
        f = np.tile(GRAY, (H, W, 1)).astype(np.uint8)
        f[30:80, 16:48] = np.array([90, 90, 90], dtype=np.uint8)  # 어두운 회색 펫
        frames.append(f)
    _seed()
    result, _ = _package(frames=frames)
    assert result.delivery_format == "packed_alpha"
    assert "packed_autodetect_uncertain" in result.warnings


# ══════════════════════════════════════════════════════════════════════════
# 포장 → 발행 연결 (Phase 7A 는 derived 를 우선한다)
# ══════════════════════════════════════════════════════════════════════════


def test_publication_prefers_packed_derived_and_reports_format(monkeypatch):
    _seed()
    _package()

    existing = {RAW_PATH, PACKED_PATH}
    monkeypatch.setattr(
        asset_url_refresh,
        "sign_object",
        lambda obj: (
            f"https://storage.test/{obj.bucket}/{obj.path}?token=fresh"
            if obj.path in existing
            else None
        ),
    )
    published = _run(
        publication.publish_breathing(
            user_id=USER, pet_id=PET, motion_version_id=VERSION_ID
        )
    )
    assert published.breathing_object_path == PACKED_PATH
    assert published.delivery_format == "packed_alpha"
    assert published.background_baked is False
    # 테마 정보는 포장/발행 계보 어디에도 없다 — 테마는 재생 시점 합성이다.
    delivery_meta = motions._MOCK_CANDIDATES[0]["generation_metadata"]["delivery"]
    assert "theme" not in str(delivery_meta).lower()


# ══════════════════════════════════════════════════════════════════════════
# 라우터 배선
# ══════════════════════════════════════════════════════════════════════════


@pytest.fixture
def client() -> ASGITestClient:
    app = FastAPI()
    app.include_router(motion_videos_v1.router, prefix="/api")
    return ASGITestClient(app)


def _auth(user: str = USER) -> dict[str, str]:
    return {"Authorization": f"Bearer test:{user}"}


def test_package_endpoint_plumbs_auth_and_body(client: ASGITestClient, monkeypatch):
    seen: dict = {}

    async def fake_package(**kwargs):
        seen.update(kwargs)
        return delivery.MotionDeliveryResult(
            motion_version_id=VERSION_ID,
            candidate_id=CANDIDATE_ID,
            pet_id=PET,
            delivery_format="packed_alpha",
            derived_bucket=BUCKET,
            derived_video_path=PACKED_PATH,
            raw_video_path=RAW_PATH,
            frame_count=8,
            fps=24.0,
            matte_backend="bgmodel",
        )

    monkeypatch.setattr(delivery, "package_breathing_for_delivery", fake_package)
    response = client.post(
        f"/api/v1/pet/motions/{PET}/BREATHING/package",
        json={"motion_version_id": VERSION_ID},
        headers=_auth(),
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["delivery_format"] == "packed_alpha"
    assert body["derived_video_path"] == PACKED_PATH
    assert body["raw_video_path"] == RAW_PATH
    assert seen["user_id"] == USER
    assert seen["pet_id"] == PET
    assert seen["motion_version_id"] == VERSION_ID


def test_package_endpoint_requires_auth(client: ASGITestClient):
    response = client.post(
        f"/api/v1/pet/motions/{PET}/BREATHING/package",
        json={"motion_version_id": VERSION_ID},
    )
    assert response.status_code in (401, 403)


# ══════════════════════════════════════════════════════════════════════════
# 실코덱 통합 — ffmpeg 왕복 (없으면 skip)
# ══════════════════════════════════════════════════════════════════════════


def _has_ffmpeg() -> bool:
    import shutil

    return bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))


@pytest.mark.skipif(not _has_ffmpeg(), reason="ffmpeg/ffprobe 필요")
def test_real_ffmpeg_roundtrip_produces_browser_detectable_packed():
    """합성 회색 배경 클립 → 실제 인코딩 → 디코드 후 브라우저 임계값 재검증."""
    _seed()
    src_bytes = delivery.encode_video(_frames(10), FPS)

    result, _ = _run_real_package(src_bytes)
    assert result.delivery_format == "packed_alpha"
    assert result.derived_video_path == PACKED_PATH
    assert result.frame_count == 10

    packed_bytes = _UPLOADED["bytes"]
    frames, fps = delivery.decode_video(packed_bytes)
    assert frames, "packed mp4 디코딩 실패"
    assert abs(fps - FPS) < 0.5
    mid = frames[len(frames) // 2]
    assert mid.shape == (2 * H, W, 3)

    # H.264 압축 후에도 브라우저 판정(packed-alpha-canvas.ts)과 같은 방향이어야
    # 한다: 매트 절반 chroma < 6.0, 컬러 절반은 그보다 2배 이상.
    top, bottom = mid[:H], mid[H:]
    bottom_chroma = delivery._avg_chroma(bottom)
    top_chroma = delivery._avg_chroma(top)
    assert bottom_chroma < delivery.ALPHA_MATTE_MAX_CHROMA
    assert top_chroma >= bottom_chroma * delivery.MIN_COLOR_TO_MATTE_RATIO

    # 알파/RGB 동기: 압축을 거친 뒤에도 펫 내부 알파는 높고 배경은 낮다.
    alpha = bottom[:, :, 0]
    assert alpha[55, 32] > 200
    assert alpha[5, 5] < 30


@pytest.mark.skipif(not _has_ffmpeg(), reason="ffmpeg/ffprobe 필요")
def test_streamed_vitmatte_pack_matches_batch_pixels_and_codec_contract(tmp_path):
    """모델만 결정론 함수로 주입해 batch/stream의 나머지 전 과정을 실제 ffmpeg로 비교."""
    source_frames = _frames(10)
    # 시간 중앙값이 실제로 일하도록 프레임마다 작은 알파 변화를 만든다.
    for i, frame in enumerate(source_frames):
        frame[30:80, 16 + (i % 3):48] = PET_COLOR
    source = delivery.encode_video(source_frames, FPS)
    decoded, fps = delivery.decode_video(source)

    def matte_frame(frame):
        # H.264 디코드 뒤 실제 픽셀에서 결정되는 float32 알파.
        return (frame[:, :, 0].astype(np.float32) / 255.0)

    batch_alphas = [matte_frame(frame) for frame in decoded]
    batch_alphas, batch_diag = delivery.stabilize_alpha(batch_alphas)
    batch_packed = delivery.build_packed_frames(decoded, batch_alphas)
    batch_warnings = delivery.validate_packed_frames(batch_packed)
    batch_bytes = delivery.encode_video(batch_packed, fps)

    streamed_path = tmp_path / "streamed.mp4"
    streamed = delivery.stream_vitmatte_to_packed_file(
        source,
        str(streamed_path),
        matte_frame_fn=matte_frame,
    )
    streamed_frames, streamed_fps = delivery.decode_video(streamed_path.read_bytes())
    batch_frames, batch_fps = delivery.decode_video(batch_bytes)

    assert streamed.frame_count == len(decoded) == 10
    assert streamed.width == W and streamed.height == 2 * H
    assert streamed.fps == pytest.approx(fps)
    assert streamed_fps == pytest.approx(batch_fps)
    assert streamed.stabilization == batch_diag
    assert streamed.warnings == batch_warnings
    assert len(streamed_frames) == len(batch_frames) == 10
    for actual, expected in zip(streamed_frames, batch_frames):
        np.testing.assert_array_equal(actual, expected)


@pytest.mark.skipif(not _has_ffmpeg(), reason="ffmpeg/ffprobe 필요")
def test_package_uses_streaming_only_for_uninjected_vitmatte(monkeypatch):
    _seed()
    monkeypatch.setenv("MOTION_DELIVERY_MATTE_BACKEND", "vitmatte")
    monkeypatch.setattr(
        delivery,
        "_matte_vitmatte_frame",
        lambda frame: (frame[:, :, 0].astype(np.float32) / 255.0),
    )
    source = delivery.encode_video(_frames(5), FPS)
    uploaded = {}

    async def upload(path, local_path):
        uploaded["path"] = path
        uploaded["local_path"] = local_path
        assert isinstance(local_path, str)
        with open(local_path, "rb") as f:
            uploaded["data"] = f.read()
        delivery._MOCK_DELIVERY_OBJECTS.add(path)

    monkeypatch.setattr(delivery, "_upload_derived", upload)

    result = _run(
        delivery.package_breathing_for_delivery(
            user_id=USER,
            pet_id=PET,
            motion_version_id=VERSION_ID,
            video_bytes=source,
        )
    )

    assert result.matte_backend == "vitmatte"
    assert result.frame_count == 5
    assert uploaded["path"] == PACKED_PATH
    assert uploaded["local_path"].endswith("out.mp4")
    packed, fps = delivery.decode_video(uploaded["data"])
    assert len(packed) == 5 and packed[0].shape == (2 * H, W, 3)
    assert fps == pytest.approx(FPS)


_UPLOADED: dict = {}


def _run_real_package(src_bytes: bytes):
    _UPLOADED.clear()

    def upload(path: str, data: bytes):
        _UPLOADED["path"] = path
        _UPLOADED["bytes"] = data
        delivery._MOCK_DELIVERY_OBJECTS.add(path)

    result = _run(
        delivery.package_breathing_for_delivery(
            user_id=USER,
            pet_id=PET,
            motion_version_id=VERSION_ID,
            video_bytes=src_bytes,
            upload_fn=upload,
        )
    )
    return result, _UPLOADED


# ══════════════════════════════════════════════════════════════════════════
# bgmodel → 내부 구멍 QA → ViTMatte 폴백
# ══════════════════════════════════════════════════════════════════════════

DARK_BG = np.array([89, 89, 89], dtype=np.uint8)
WHITE_COAT = np.array([235, 235, 235], dtype=np.uint8)
DARK_MARK = np.array([55, 55, 55], dtype=np.uint8)  # 무채색·배경보다 어두움 → bgmodel 이 그림자로 본다
SPECKLES = [(36, 20), (36, 30), (36, 40), (48, 22), (48, 34), (60, 26), (68, 38)]

#: 테스트가 쓰는 임계 — 운영 기본값과 같다 (env 로 덮지 않는다).
QA_CFG = matte_hole_qa.HoleQaConfig()


def _white_pet_frame(*, speckles: bool = True, gap: bool = False) -> np.ndarray:
    """어두운 중립 배경 위 흰 펫 + (옵션) 어두운 반점 / 다리 사이 같은 큰 빈틈."""
    f = np.tile(DARK_BG, (H, W, 1)).astype(np.uint8)
    f[30:80, 16:48] = WHITE_COAT
    if speckles:
        for y, x in SPECKLES:
            f[y : y + 4, x : x + 4] = DARK_MARK
    if gap:
        f[45:57, 26:36] = DARK_BG  # 실루엣에 둘러싸인 큰 빈틈 (꼬리 고리/다리 사이)
    return f


def _clean_alpha() -> np.ndarray:
    a = np.zeros((H, W), dtype=np.float32)
    a[30:80, 16:48] = 1.0
    return a


def _holey_alpha() -> np.ndarray:
    a = _clean_alpha()
    for y, x in SPECKLES:
        a[y : y + 4, x : x + 4] = 0.0
    return a


def _seed_coat(palette: list[tuple[str, float]]) -> None:
    pet_identity_service._MOCK_PROFILES.append(
        {
            "id": "idp-1",
            "pet_id": PET,
            "user_id": USER,
            "version": 1,
            "visual_identity": {
                "coat": {
                    "status": "measured",
                    "palette": [{"name": n, "fraction": f} for n, f in palette],
                }
            },
        }
    )


class _FakeVitmatte:
    def __init__(self, *, fail: bool = False):
        self.calls = 0
        self.fail = fail

    def __call__(self, frames):
        self.calls += 1
        if self.fail:
            raise delivery.MotionDeliveryError("MATTE_BACKEND_FAILED", "boom", status=503)
        return [_clean_alpha() for _ in frames], {"backend": "vitmatte"}


def _package_counting(monkeypatch, frames, *, vitmatte=None):
    vit = vitmatte or _FakeVitmatte()
    monkeypatch.setattr(delivery, "matte_vitmatte", vit)
    uploads: list[str] = []

    def upload(path, data):
        uploads.append(path)
        delivery._MOCK_DELIVERY_OBJECTS.add(path)

    result, captured = _package(frames=frames, upload_fn=upload)
    return result, captured, vit, uploads


# --- QA 단위 ------------------------------------------------------------------


def test_hole_qa_clean_matte_passes():
    qa = matte_hole_qa.evaluate([_clean_alpha() for _ in range(8)], QA_CFG)
    assert qa["passed"] is True
    assert qa["failed_frames"] == [] and qa["max_hole_count"] == 0
    assert qa["sampled_frames"] == [0, 2, 4, 5, 7]  # 균등 5장 (양 끝 포함)


def test_hole_qa_many_small_enclosed_holes_fails():
    qa = matte_hole_qa.evaluate([_holey_alpha() for _ in range(8)], QA_CFG)
    assert qa["passed"] is False
    assert len(qa["failed_frames"]) == 5
    assert qa["max_hole_count"] == len(SPECKLES)
    assert qa["max_hole_fraction"] >= QA_CFG.frame_min_fraction


def test_hole_qa_ignores_one_large_legitimate_gap():
    a = _clean_alpha()
    a[45:57, 26:36] = 0.0  # 실루엣의 7.5% — max_region_fraction(2%) 초과 → 정당한 빈틈
    m = matte_hole_qa.measure_frame(a, QA_CFG)
    assert m["ignored_large_gaps"] == 1 and m["hole_count"] == 0 and m["failed"] is False
    assert matte_hole_qa.evaluate([a] * 6, QA_CFG)["passed"] is True


def test_hole_qa_border_touching_gap_is_not_a_hole():
    a = _clean_alpha()
    a[60:80, 28:36] = 0.0  # 아래로 열린 다리 사이 — 바깥 배경과 이어진다
    assert matte_hole_qa.measure_frame(a, QA_CFG)["hole_count"] == 0


def test_hole_qa_single_bad_frame_does_not_fail_clip():
    alphas = [_clean_alpha() for _ in range(8)]
    alphas[4] = _holey_alpha()  # 표본 프레임 하나만 불량
    qa = matte_hole_qa.evaluate(alphas, QA_CFG)
    assert qa["failed_frames"] == [4]
    assert qa["passed"] is True


# --- 포장 통합 (실제 bgmodel) -------------------------------------------------


def test_clean_bgmodel_matte_keeps_bgmodel(monkeypatch):
    _seed()
    _seed_coat([("brown", 1.0)])
    result, _, vit, uploads = _package_counting(monkeypatch, _frames())

    assert result.matte_backend == "bgmodel"
    assert vit.calls == 0 and len(uploads) == 1
    fb = motions._MOCK_CANDIDATES[0]["generation_metadata"]["delivery"]["matte_fallback"]
    assert fb["requested_backend"] == "bgmodel" and fb["final_backend"] == "bgmodel"
    assert fb["fallback_used"] is False and fb["fallback_reason"] is None
    assert fb["coat_risk"] == "not_light"
    assert fb["failed_frames"] == []


def test_light_coat_alone_does_not_trigger_vitmatte(monkeypatch):
    _seed()
    _seed_coat([("white", 0.85), ("black", 0.15)])
    frames = [_white_pet_frame(speckles=False) for _ in range(8)]
    result, _, vit, _ = _package_counting(monkeypatch, frames)

    assert result.matte_backend == "bgmodel" and vit.calls == 0
    fb = motions._MOCK_CANDIDATES[0]["generation_metadata"]["delivery"]["matte_fallback"]
    assert fb["coat_risk"] == "light_or_white" and fb["fallback_used"] is False


def test_white_pet_with_dark_markings_falls_back_to_vitmatte_once(monkeypatch):
    _seed()
    _seed_coat([("white", 0.85), ("black", 0.15)])
    frames = [_white_pet_frame() for _ in range(8)]
    result, captured, vit, uploads = _package_counting(monkeypatch, frames)

    assert vit.calls == 1  # 반복 실패여도 정확히 한 번
    assert result.matte_backend == "vitmatte"
    # 업로드는 최종(ViTMatte) 포장 한 번뿐 — 임시 bgmodel 포장은 어디에도 남지 않는다.
    assert uploads == [PACKED_PATH]
    assert delivery._MOCK_DELIVERY_OBJECTS == {PACKED_PATH}
    # 최종 매트는 반점 자리까지 불투명이다.
    y, x = SPECKLES[0]
    assert captured["packed"][4][H + y + 1, x + 1, 0] > 250

    delivery_meta = motions._MOCK_CANDIDATES[0]["generation_metadata"]["delivery"]
    fb = delivery_meta["matte_fallback"]
    assert delivery_meta["matte_backend"] == "vitmatte"
    assert fb["requested_backend"] == "bgmodel"
    assert fb["final_backend"] == "vitmatte"
    assert fb["fallback_used"] is True
    assert fb["fallback_reason"] == "enclosed_internal_holes"
    assert fb["coat_risk"] == "light_or_white"
    assert fb["sampled_frames"] == [0, 2, 4, 5, 7]
    assert len(fb["failed_frames"]) >= 2
    assert fb["max_hole_count"] >= QA_CFG.frame_min_count
    assert fb["max_hole_fraction"] >= QA_CFG.frame_min_fraction


def test_fallback_does_not_duplicate_candidates_or_versions(monkeypatch):
    _seed()
    frames = [_white_pet_frame() for _ in range(8)]
    _package_counting(monkeypatch, frames)

    assert len(motions._MOCK_CANDIDATES) == 1
    assert len(motions._MOCK_VERSIONS) == 1
    cand = motions._MOCK_CANDIDATES[0]
    assert cand["derived_video_path"] == PACKED_PATH and cand["raw_video_path"] == RAW_PATH
    assert cand["decision"] == "PASS" and cand["selected"] is True
    # 재호출은 멱등 — ViTMatte 를 다시 돌리지 않는다.
    vit = _FakeVitmatte()
    monkeypatch.setattr(delivery, "matte_vitmatte", vit)
    again, _ = _package(frames=frames)
    assert again.deduplicated is True and vit.calls == 0


def test_large_legitimate_gap_does_not_fall_back(monkeypatch):
    _seed()
    frames = [_white_pet_frame(speckles=False, gap=True) for _ in range(8)]
    result, _, vit, _ = _package_counting(monkeypatch, frames)
    assert result.matte_backend == "bgmodel" and vit.calls == 0


def test_vitmatte_fallback_failure_does_not_return_to_bgmodel(monkeypatch):
    _seed()
    frames = [_white_pet_frame() for _ in range(8)]
    vit = _FakeVitmatte(fail=True)
    with pytest.raises(delivery.MotionDeliveryError) as exc:
        _package_counting(monkeypatch, frames, vitmatte=vit)

    assert exc.value.code == "MATTE_BACKEND_FAILED"
    assert "폴백" in exc.value.message
    assert vit.calls == 1
    assert delivery._MOCK_DELIVERY_OBJECTS == set()  # 구멍 난 bgmodel 포장도 올리지 않는다
    cand = motions._MOCK_CANDIDATES[0]
    assert "delivery" not in cand["generation_metadata"]
    assert not cand.get("derived_video_path")


def test_hole_qa_can_be_disabled(monkeypatch):
    monkeypatch.setenv("MOTION_DELIVERY_HOLE_QA", "0")
    _seed()
    frames = [_white_pet_frame() for _ in range(8)]
    result, _, vit, _ = _package_counting(monkeypatch, frames)
    assert result.matte_backend == "bgmodel" and vit.calls == 0
    fb = motions._MOCK_CANDIDATES[0]["generation_metadata"]["delivery"]["matte_fallback"]
    assert fb["hole_qa"] is None and fb["coat_risk"] is None


def test_injected_or_vitmatte_backends_skip_hole_qa(monkeypatch):
    _seed()
    vit = _FakeVitmatte()
    frames = [_white_pet_frame() for _ in range(8)]
    result, _ = _package(frames=frames, matte_fn=delivery.matte_bgmodel)
    assert result.matte_backend == "bgmodel"  # 주입 백엔드는 QA/폴백 대상이 아니다
    fb = motions._MOCK_CANDIDATES[0]["generation_metadata"]["delivery"]["matte_fallback"]
    assert fb["requested_backend"] == "injected" and fb["fallback_used"] is False
    assert vit.calls == 0


def test_streamed_production_fallback_uploads_once(monkeypatch):
    """운영 경로(코덱 주입 없음): 실패 판정 후 raw 를 ViTMatte 로 스트리밍, 업로드 1회."""
    _seed()
    calls = {"frames": 0}

    def fake_frame(frame):
        calls["frames"] += 1
        return _clean_alpha()

    monkeypatch.setattr(delivery, "_matte_vitmatte_frame", fake_frame)
    uploads: list[str] = []

    def upload(path, data):
        uploads.append(path)
        delivery._MOCK_DELIVERY_OBJECTS.add(path)

    source = delivery.encode_video([_white_pet_frame() for _ in range(6)], FPS)
    result = _run(
        delivery.package_breathing_for_delivery(
            user_id=USER,
            pet_id=PET,
            motion_version_id=VERSION_ID,
            video_bytes=source,
            upload_fn=upload,
        )
    )
    assert result.matte_backend == "vitmatte"
    assert calls["frames"] == 6  # 클립을 정확히 한 번 매팅
    assert uploads == [PACKED_PATH]
    fb = motions._MOCK_CANDIDATES[0]["generation_metadata"]["delivery"]["matte_fallback"]
    assert fb["fallback_used"] is True and fb["coat_risk"] == "unknown"
