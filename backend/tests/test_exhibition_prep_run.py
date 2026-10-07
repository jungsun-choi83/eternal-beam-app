"""
EXHIBITION_PREP_RUN — 실행 수명주기, 패키지 계약, 일반 펫 생성과의 격리.

ViTMatte 는 목업한다 (matte_fn 주입). 실제 가중치 통합 테스트는 범위 밖.
"""

from __future__ import annotations

import ast
import io
import json
import pathlib
import uuid

import cv2
import numpy as np
import pytest
from fastapi import FastAPI
from PIL import Image

from backend.services import exhibition_prep_service as svc
from backend.services import exhibition_prep_store as st
from backend.services.cutout_errors import SubjectNotDetectedError
from backend.tests.conftest import ASGITestClient
from backend.tests.test_exhibition_breathing_maps import clothed_dog, frontal_dog, round_blob, round_dog

_BACKEND = pathlib.Path(__file__).resolve().parents[1]


# ── 픽스처 ───────────────────────────────────────────────────────────────────


def _jpeg(w: int, h: int) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (w, h), (90, 140, 200)).save(buf, format="JPEG")
    return buf.getvalue()


def fake_matte(alpha_fn, *, offset=(140, 80), fallback=False, subject_class="dog"):
    """원본 크기 RGBA 를 돌려주는 가짜 ViTMatte. 원본 배경은 초록으로 남겨 둔다."""
    calls = []

    def _matte(image_bytes: bytes):
        img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
        w, h = img.size
        alpha = np.zeros((h, w), np.uint8)
        sub = alpha_fn()
        ox, oy = offset
        sh, sw = sub.shape
        alpha[oy : oy + sh, ox : ox + sw] = sub[: h - oy, : w - ox]
        rgb = np.zeros((h, w, 3), np.uint8)
        rgb[:] = (0, 255, 0)
        rgb[alpha > 0] = (150, 110, 70)
        buf = io.BytesIO()
        Image.fromarray(np.dstack([rgb, alpha]), "RGBA").save(buf, format="PNG")
        calls.append((w, h))
        return buf.getvalue(), {
            "method": "vitmatte",
            "segmenter": "grabcut" if fallback else "sam2",
            "segmenter_fallback": fallback,
            "subject_class": subject_class,
            "shadow_suppression": {"applied": False},
        }

    _matte.calls = calls
    return _matte


@pytest.fixture
def stores():
    return st.InMemoryRunStore(), st.InMemoryArtifactStore()


def _run(stores, raw, matte, **create_kw):
    store, artifacts = stores
    row = svc.create_run(raw, created_by="ops-1", store=store, artifacts=artifacts, **create_kw)
    claimed = store.claim_next("w1", stale_after_minutes=20)
    assert claimed["id"] == row["id"]
    svc.process_run(claimed, store=store, artifacts=artifacts, worker_id="w1", matte_fn=matte)
    return store.get(row["id"])


def _decode(data: bytes) -> np.ndarray:
    return cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_UNCHANGED)


# ── 수명주기 · 패키지 ─────────────────────────────────────────────────────────


def test_full_body_run_is_ready_with_complete_package(stores):
    store, artifacts = stores
    final = _run(stores, _jpeg(800, 800), fake_matte(frontal_dog))

    assert final["status"] == st.STATUS_READY
    assert final["review_reasons"] == []
    assert final["stage"] == st.STAGE_PACKAGE
    rid = final["id"]
    expected = {"subject_rgba.png", "breathing_weight.png", "locked_mask.png", "preview_overlay.png", "manifest.json"}
    assert set(final["outputs_json"]) == expected
    # 모든 산출물이 exhibition/{run_id}/ 아래에만 있다 (+ 원본).
    assert set(artifacts.objects) == {f"exhibition/{rid}/{n}" for n in expected | {"source.png"}}

    manifest = json.loads(artifacts.get(f"exhibition/{rid}/manifest.json"))
    assert manifest["schema_version"] == "EXHIBITION_RIG_INPUT_V1"
    assert manifest["map_method"] == "dt-torso-v2"
    assert manifest["subject_class"] == "dog"
    assert manifest["flags"] == {
        "touches_frame_edge": False,
        "head_low_confidence": False,
        "segmenter_fallback": False,
        "interior_holes": False,
    }
    hp = manifest["anchors"]["head_point"]
    assert hp["source"] == "auto" and hp["confidence"] >= 0.5 and len(hp["xy"]) == 2
    assert len(manifest["anchors"]["breathing_center"]) == 2
    assert len(manifest["anchors"]["breathing_axis"]) == 2
    assert isinstance(manifest["anchors"]["ground_y"], float)

    # 출력 크기 = RGBA 캔버스 = manifest.canvas_wh
    w, h = manifest["canvas_wh"]
    rgba = _decode(artifacts.get(f"exhibition/{rid}/subject_rgba.png"))
    weight = _decode(artifacts.get(f"exhibition/{rid}/breathing_weight.png"))
    locked = _decode(artifacts.get(f"exhibition/{rid}/locked_mask.png"))
    preview = _decode(artifacts.get(f"exhibition/{rid}/preview_overlay.png"))
    assert rgba.shape == (h, w, 4) and rgba.dtype == np.uint8
    assert weight.shape == (h, w) and weight.dtype == np.uint16
    assert locked.shape == (h, w) and locked.dtype == np.uint8
    assert preview.shape[:2] == (h, w)
    # crop rect 은 원본 좌표, 크기가 캔버스와 일치 (scale 1.0).
    x1, y1, x2, y2 = manifest["crop_rect_in_source"]
    assert (x2 - x1, y2 - y1) == (w, h)
    # 원본 배경(초록)이 패키지에 남지 않는다.
    assert not ((rgba[:, :, 3] == 0) & (rgba[:, :, 1] == 255)).any()

    # GET 뷰
    view = svc.public_view(store.get(rid), artifacts)
    assert view["status"] == "READY" and view["output_urls"]["manifest.json"].startswith("memory://")


def test_low_confidence_head_returns_needs_review_but_still_packages(stores):
    final = _run(stores, _jpeg(800, 800), fake_matte(round_blob))
    assert final["status"] == st.STATUS_NEEDS_REVIEW
    assert "head_low_confidence" in final["review_reasons"]
    m = final["manifest_json"]
    assert m["flags"]["head_low_confidence"] is True
    assert m["anchors"]["head_point"]["confidence"] < 0.5
    assert "manifest.json" in final["outputs_json"]


@pytest.mark.parametrize("shape", [round_dog, clothed_dog])
def test_weak_neck_real_like_pet_is_ready_with_fallback_head_lock(stores, shape):
    final = _run(stores, _jpeg(900, 1200), fake_matte(shape))
    assert final["status"] == st.STATUS_READY, final["review_reasons"]
    m = final["manifest_json"]
    assert m["map_method"] == "dt-torso-v2"
    hp = m["anchors"]["head_point"]
    assert hp["xy"] is not None and hp["source"] == "auto"
    assert hp["tier"] == "USABLE_FALLBACK" and 0.5 <= hp["confidence"] < 0.75
    assert m["diagnostics"]["head_tier"] == "USABLE_FALLBACK"
    assert m["diagnostics"]["head_estimate"]["reason"]


def test_no_head_review_manifest_reports_tier(stores):
    final = _run(stores, _jpeg(800, 800), fake_matte(round_blob))
    hp = final["manifest_json"]["anchors"]["head_point"]
    assert hp["xy"] is None and hp["tier"] == "NO_PLAUSIBLE_HEAD" and hp["method"] == "none"


def test_operator_head_hint_resolves_review(stores):
    # round_blob 의 (250, 120) 은 원본 좌표로 offset (140, 80) 만큼 이동.
    final = _run(stores, _jpeg(800, 800), fake_matte(round_blob), head_hint_xy=(390.0, 200.0))
    assert final["status"] == st.STATUS_READY
    hp = final["manifest_json"]["anchors"]["head_point"]
    assert hp["source"] == "operator" and hp["confidence"] == 1.0


def test_subject_touching_frame_edge_needs_review(stores):
    # 600px 높이의 개를 500px 원본에 넣어 다리가 아래로 잘리게 한다.
    final = _run(stores, _jpeg(700, 500), fake_matte(frontal_dog, offset=(100, 0)))
    assert final["status"] == st.STATUS_NEEDS_REVIEW
    assert final["manifest_json"]["flags"]["touches_frame_edge"] is True
    assert "bottom" in final["manifest_json"]["diagnostics"]["frame_edge_sides"]


def test_segmenter_fallback_needs_review(stores):
    final = _run(stores, _jpeg(800, 800), fake_matte(frontal_dog, fallback=True))
    assert final["status"] == st.STATUS_NEEDS_REVIEW
    assert final["review_reasons"] == ["segmenter_fallback"]


def test_cutout_rejection_fails_run(stores):
    def _reject(_b):
        raise SubjectNotDetectedError("No supported pet was detected in the image.")

    final = _run(stores, _jpeg(400, 400), _reject)
    assert final["status"] == st.STATUS_FAILED
    assert final["error_code"] == "SUBJECT_NOT_DETECTED"
    assert not final.get("outputs_json")


def test_large_input_is_capped_and_crop_maps_back_to_source(stores, monkeypatch):
    monkeypatch.setenv("EXHIBITION_MAX_INPUT_SIDE", "1000")
    matte = fake_matte(frontal_dog)
    final = _run(stores, _jpeg(2000, 1600), matte)
    assert matte.calls == [(1000, 800)]
    m = final["manifest_json"]
    assert m["source"]["processing_scale"] == 0.5
    assert m["source"]["source_wh"] == [2000, 1600]
    x1, y1, x2, y2 = m["crop_rect_in_source"]
    w, h = m["canvas_wh"]
    assert (x2 - x1, y2 - y1) == (w * 2, h * 2)


def test_bad_upload_is_rejected_before_any_write(stores):
    store, artifacts = stores
    with pytest.raises(svc.ExhibitionInputError):
        svc.create_run(b"not an image", created_by="ops", store=store, artifacts=artifacts)
    assert store.rows == {} and artifacts.objects == {}


def test_attempts_exhausted_fails(stores):
    store, artifacts = stores
    row = svc.create_run(_jpeg(300, 300), created_by="ops", store=store, artifacts=artifacts)
    run = dict(store.get(row["id"]), attempts=st.MAX_ATTEMPTS + 1)
    out = svc.process_run(run, store=store, artifacts=artifacts, matte_fn=fake_matte(frontal_dog))
    assert out["error_code"] == "WORKER_RECOVERY_EXHAUSTED"


def test_default_matte_reuses_vitmatte_service_directly(monkeypatch):
    from backend.services import vitmatte_service

    seen = {}

    def _fake(image_bytes, **kw):
        seen["bytes"] = image_bytes
        return b"png", {"method": "vitmatte"}

    monkeypatch.setattr(vitmatte_service, "matte_foreground_with_meta", _fake)
    assert svc._default_matte(b"abc") == (b"png", {"method": "vitmatte"})
    assert seen["bytes"] == b"abc"


# ── 격리 ─────────────────────────────────────────────────────────────────────

_EXHIBITION_MODULES = [
    "services/exhibition_breathing_maps.py",
    "services/exhibition_prep_store.py",
    "services/exhibition_prep_service.py",
    "routers/exhibition_prep_v1.py",
    "workers/exhibition_prep_worker.py",
    "services/exhibition_handoff_client.py",
    "services/exhibition_queue_service.py",
    "routers/exhibition_queue_v1.py",
]
#: 외부 전시 시스템에 POST 하는 단 하나의 모듈 — 여기만 HTTP 클라이언트를 쓴다.
_HTTP_ALLOWED = {"services/exhibition_handoff_client.py": {"requests"}}

_FORBIDDEN_IMPORT_FRAGMENTS = (
    "pet_generation", "generation_runs", "generation_queue", "generation_reconciler",
    "pet_reference", "pet_registry", "pet_identity", "canonical", "keyframe", "motion_",
    "video_generation", "business_qa", "qa_shadow", "publication", "delivery", "credit",
    "wallet", "billing", "premium", "device_", "luma", "routers.matting", "routers.cutout",
)


def _imports(path: pathlib.Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            names.append(base)
            names += [f"{base}.{a.name}" for a in node.names]
    return names


@pytest.mark.parametrize("rel", _EXHIBITION_MODULES)
def test_exhibition_modules_do_not_import_normal_pipeline(rel):
    imported = _imports(_BACKEND / rel)
    bad = [n for n in imported if any(f in n for f in _FORBIDDEN_IMPORT_FRAGMENTS)]
    assert bad == [], f"{rel} imports normal-pipeline modules: {bad}"
    # HTTP 로 누끼 라우터를 부르지 않는다 — HTTP 클라이언트 자체를 쓰지 않는다.
    allowed = _HTTP_ALLOWED.get(rel, set())
    http = [n for n in imported if n.split(".")[0] in {"httpx", "requests", "aiohttp", "urllib"} - allowed]
    assert http == [], f"{rel} imports an HTTP client: {http}"


def test_run_does_not_touch_pet_references_user_assets_or_generation_state(stores, monkeypatch):
    from backend.services import pet_reference_service, supabase_assets

    def _boom(*a, **k):
        raise AssertionError("exhibition run must not write normal pet state")

    monkeypatch.setattr(pet_reference_service, "record_derived", _boom)
    monkeypatch.setattr(supabase_assets, "ensure_user_asset_row", _boom)
    monkeypatch.setattr(supabase_assets, "upload_asset_to_storage", _boom)
    final = _run(stores, _jpeg(800, 800), fake_matte(frontal_dog))
    assert final["status"] == st.STATUS_READY


class _FakeQuery:
    def __init__(self, client, table):
        self.client, self.table, self.op, self.payload, self.filters = client, table, "select", None, []

    def select(self, *_a):
        return self

    def insert(self, row):
        self.op, self.payload = "insert", row
        return self

    def update(self, fields):
        self.op, self.payload = "update", fields
        return self

    def eq(self, k, v):
        self.filters.append((k, v))
        return self

    def in_(self, k, v):
        self.filters.append((k, tuple(v)))
        return self

    def is_(self, k, v):
        assert v == "null"
        self.filters.append((k, None))
        return self

    def order(self, *_a, **_k):
        return self

    def limit(self, *_a):
        return self

    def execute(self):
        self.client.ops.append((self.table, self.op, self.payload))
        rows = self.client.rows
        if self.op == "insert":
            rows[self.payload["id"]] = {"attempts": 0, "created_at": "2026-10-05T00:00:00+00:00", **self.payload}
            return type("R", (), {"data": [rows[self.payload["id"]]]})()
        match = [r for r in rows.values() if all(r.get(k) == v or (isinstance(v, tuple) and r.get(k) in v) for k, v in self.filters)]
        if self.op == "update":
            for r in match:
                r.update(self.payload)
        return type("R", (), {"data": [dict(r) for r in match]})()


class _FakeBucket:
    def __init__(self, client, name):
        self.client, self.name = client, name

    def upload(self, path, data, _opts):
        self.client.objects[(self.name, path)] = data

    def download(self, path):
        return self.client.objects[(self.name, path)]

    def create_signed_url(self, path, _ttl):
        return {"signedURL": f"https://signed/{path}"}


class _FakeSupabase:
    def __init__(self):
        self.ops, self.rows, self.objects = [], {}, {}
        self.storage = type("S", (), {"from_": lambda _s, name: _FakeBucket(self, name)})()

    def table(self, name):
        return _FakeQuery(self, name)


def test_supabase_store_only_writes_exhibition_table_and_prefix(monkeypatch):
    client = _FakeSupabase()
    store, artifacts = st.SupabaseRunStore(client), st.SupabaseArtifactStore(client, bucket="b")
    row = svc.create_run(_jpeg(800, 800), created_by="ops", store=store, artifacts=artifacts)
    claimed = store.claim_next("w1", stale_after_minutes=20)
    svc.process_run(claimed, store=store, artifacts=artifacts, worker_id="w1", matte_fn=fake_matte(frontal_dog))

    tables = {t for t, _op, _p in client.ops}
    assert tables == {"exhibition_prep_runs"}
    assert "pet_generation_runs" not in tables
    assert all(path.startswith(f"exhibition/{row['id']}/") for (_b, path) in client.objects)
    assert client.rows[row["id"]]["status"] == st.STATUS_READY
    with pytest.raises(ValueError):
        artifacts.put("user-1/content/cutout_vitmatte.png", b"x", "image/png")


def test_stale_worker_cannot_overwrite_reclaimed_run(stores):
    store, artifacts = stores
    row = svc.create_run(_jpeg(400, 400), created_by="ops", store=store, artifacts=artifacts)
    claimed = store.claim_next("w-old", stale_after_minutes=20)
    store.rows[row["id"]]["claimed_by"] = "w-new"  # 다른 워커가 다시 잡았다
    svc.process_run(claimed, store=store, artifacts=artifacts, worker_id="w-old", matte_fn=fake_matte(frontal_dog))
    assert store.get(row["id"])["status"] == st.STATUS_RUNNING


def test_object_paths_reject_traversal():
    with pytest.raises(ValueError):
        st.run_object_path("../etc", "x.png")
    with pytest.raises(ValueError):
        st.run_object_path(str(uuid.uuid4()), "../x.png")


# ── API ──────────────────────────────────────────────────────────────────────


def _app(stores, *, ops: bool):
    from backend.auth import AuthedUser, require_user
    from backend.routers import exhibition_prep_v1
    from backend.services.shaker_ops import require_ops

    app = FastAPI()
    app.include_router(exhibition_prep_v1.router, prefix="/api")
    app.dependency_overrides[require_user] = lambda: AuthedUser(user_id="ops-1")
    if ops:
        app.dependency_overrides[require_ops] = lambda: AuthedUser(user_id="ops-1")
    app.dependency_overrides[exhibition_prep_v1.get_stores] = lambda: stores
    return ASGITestClient(app)


def test_api_create_and_get_run(stores):
    client = _app(stores, ops=True)
    res = client.post(
        "/api/exhibition/prep-runs",
        files={"file": ("dog.jpg", _jpeg(640, 480), "image/jpeg")},
        data={"exhibition_id": "expo-1", "head_hint_x": "10", "head_hint_y": "20"},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["status"] == "QUEUED"
    row = stores[0].get(body["run_id"])
    assert row["exhibition_id"] == "expo-1" and row["created_by"] == "ops-1"
    assert row["head_hint"] == {"xy_in_source": [10.0, 20.0]}

    got = client.get(f"/api/exhibition/prep-runs/{body['run_id']}")
    assert got.status_code == 200 and got.json()["status"] == "QUEUED"
    assert client.get(f"/api/exhibition/prep-runs/{uuid.uuid4()}").status_code == 404
    bad = client.post("/api/exhibition/prep-runs", files={"file": ("x.jpg", b"nope", "image/jpeg")})
    assert bad.status_code == 400 and bad.json()["detail"]["code"] == "UNREADABLE_IMAGE"


def test_api_requires_ops(stores, monkeypatch):
    monkeypatch.delenv("SHAKER_OPS_USER_IDS", raising=False)
    monkeypatch.delenv("OPS_USER_IDS", raising=False)
    client = _app(stores, ops=False)
    res = client.post(
        "/api/exhibition/prep-runs", files={"file": ("dog.jpg", _jpeg(64, 64), "image/jpeg")}
    )
    assert res.status_code == 403
    assert stores[0].rows == {}
