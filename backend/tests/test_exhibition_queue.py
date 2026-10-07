"""
EXHIBITION QUEUE — 스태프 접수 번호 · 표시 상태 (WAITING → UP_NEXT → NOW_SHOWING → COMPLETE).

처리(누끼·맵)는 fake_matte 로, 핸드오프는 mock 모드로 돌린다.
"""

from __future__ import annotations

import concurrent.futures
import uuid

import pytest
from fastapi import FastAPI

from backend.services import exhibition_prep_service as svc
from backend.services import exhibition_prep_store as st
from backend.services import exhibition_queue_service as q
from backend.tests.conftest import ASGITestClient
from backend.tests.test_exhibition_breathing_maps import frontal_dog
from backend.tests.test_exhibition_prep_run import _FakeSupabase, _jpeg, fake_matte

EXPO = "expo-1"


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    for k in ("EXHIBITION_HANDOFF_MODE", "EXHIBITION_HANDOFF_URL", "EXHIBITION_DEFAULT_ID"):
        monkeypatch.delenv(k, raising=False)


@pytest.fixture
def stores():
    return st.InMemoryRunStore(), st.InMemoryArtifactStore()


def submit(stores, name=None, *, exhibition_id=EXPO):
    store, artifacts = stores
    return q.submit(_jpeg(800, 800), created_by="staff-1", store=store, artifacts=artifacts,
                    exhibition_id=exhibition_id, pet_name=name)


def process(stores, row, *, fallback=False, reject=False):
    """기존 워커가 하는 일: 클레임 → 처리. 결과 처리 status 를 돌려준다."""
    store, artifacts = stores
    claimed = store.claim_next("w1", stale_after_minutes=20)
    assert claimed["id"] == row["id"]

    def _reject(_b):
        from backend.services.cutout_errors import SubjectNotDetectedError

        raise SubjectNotDetectedError("No supported pet was detected in the image.")

    matte = _reject if reject else fake_matte(frontal_dog, fallback=fallback)
    svc.process_run(claimed, store=store, artifacts=artifacts, worker_id="w1", matte_fn=matte)
    return store.get(row["id"])["status"]


def view(stores, **kw):
    return q.staff_view(EXPO, store=stores[0], handoff_enabled=False, **kw)


def nums(items):
    return [i["queue_number"] for i in items]


def press_show_next(stores, **kw):
    v = view(stores, reconcile=False)
    return q.show_next(
        EXPO,
        expected_now_showing=(v["now_showing"] or {}).get("run_id"),
        expected_up_next=(v["up_next"] or {}).get("run_id"),
        store=stores[0],
        handoff_enabled=kw.get("handoff_enabled", False),
    )


# ── 번호 ─────────────────────────────────────────────────────────────────────


def test_queue_numbers_are_sequential_per_exhibition(stores):
    rows = [submit(stores, n) for n in ("Coco", "Bori", None)]
    assert [r["queue_number"] for r in rows] == [1, 2, 3]
    assert [r["pet_name"] for r in rows] == ["Coco", "Bori", None]
    assert all(r["display_status"] == "WAITING" and r["status"] == "QUEUED" for r in rows)
    assert all(r["run_id"] if "run_id" in r else r["id"] for r in rows)
    # 다른 전시는 1 부터 따로.
    assert submit(stores, "Leo", exhibition_id="expo-2")["queue_number"] == 1
    assert submit(stores, "Momo")["queue_number"] == 4


def test_concurrent_submissions_get_distinct_sequential_numbers(stores):
    with concurrent.futures.ThreadPoolExecutor(8) as pool:
        rows = list(pool.map(lambda i: submit(stores, f"pet{i}"), range(16)))
    assert sorted(r["queue_number"] for r in rows) == list(range(1, 17))


def test_bad_photo_is_rejected_without_consuming_a_number(stores):
    store, artifacts = stores
    with pytest.raises(svc.ExhibitionInputError):
        q.submit(b"not an image", created_by="s", store=store, artifacts=artifacts, exhibition_id=EXPO)
    assert store.rows == {} and store.counters == {}
    assert submit(stores)["queue_number"] == 1


def test_pet_name_and_exhibition_id_are_normalized(stores):
    row = submit(stores, "  Coco   the\tdog  " + "x" * 60)
    assert row["pet_name"].startswith("Coco the dog") and len(row["pet_name"]) == q.PET_NAME_MAX
    assert submit(stores, "   ")["pet_name"] is None
    with pytest.raises(q.QueueError) as e:
        q.normalize_exhibition_id("../etc")
    assert e.value.code == "INVALID_EXHIBITION_ID" and e.value.status == 400


# ── 자격 · 승격 ──────────────────────────────────────────────────────────────


def test_only_ready_runs_enter_display_queue(stores):
    a = submit(stores, "Coco")
    b = submit(stores, "Bori")
    v = view(stores)
    assert v["up_next"] is None and v["ready"] == []
    assert nums(v["preparing"]) == [1, 2]
    assert {i["processing_status"] for i in v["preparing"]} == {"QUEUED"}

    stores[0].update(a["id"], {"status": st.STATUS_RUNNING})
    v = view(stores)
    assert v["up_next"] is None
    assert [i["processing_status"] for i in v["preparing"]] == ["PROCESSING", "QUEUED"]

    stores[0].update(a["id"], {"status": st.STATUS_QUEUED})
    assert process(stores, a) == "READY"
    v = view(stores)
    assert v["up_next"]["queue_number"] == 1 and v["up_next"]["processing_status"] == "READY"
    assert nums(v["preparing"]) == [2]
    assert b["id"] not in {v["up_next"]["run_id"]}


def test_first_ready_item_becomes_up_next_even_if_not_lowest_number(stores):
    a = submit(stores, "Coco")
    b = submit(stores, "Bori")
    store, _ = stores
    # #2 이 먼저 READY 가 된다 (#1 은 아직 처리 중).
    store.rows[b["id"]]["status"] = st.STATUS_READY
    v = view(stores)
    assert v["up_next"]["queue_number"] == 2
    assert nums(v["preparing"]) == [1]
    # #1 이 READY 가 되어도 UP_NEXT 를 빼앗지 않는다 — READY 목록에서 기다린다.
    store.rows[a["id"]]["status"] = st.STATUS_READY
    v = view(stores)
    assert v["up_next"]["queue_number"] == 2 and nums(v["ready"]) == [1]


def test_processing_failure_never_enters_display_queue(stores):
    ok = submit(stores, "Coco")
    failed = submit(stores, "Bori")
    review = submit(stores, "Momo")
    assert process(stores, ok) == "READY"
    assert process(stores, failed, reject=True) == "FAILED"
    assert process(stores, review, fallback=True) == "NEEDS_REVIEW"

    v = view(stores)
    assert v["up_next"]["queue_number"] == 1
    assert nums(v["needs_attention"]) == [2, 3]
    assert [i["processing_status"] for i in v["needs_attention"]] == ["FAILED", "FAILED"]
    assert v["needs_attention"][0]["detail"] == "SUBJECT_NOT_DETECTED"
    assert v["needs_attention"][1]["detail"].startswith("NEEDS_REVIEW")
    assert v["ready"] == []

    # 끝까지 넘겨도 실패 건은 UP_NEXT/NOW_SHOWING 이 되지 않는다.
    press_show_next(stores)  # #1 → NOW_SHOWING
    press_show_next(stores)  # #1 → COMPLETE
    for row in (failed, review):
        assert stores[0].get(row["id"])["display_status"] == "WAITING"
    pub = q.public_view(EXPO, store=stores[0], handoff_enabled=False)
    assert pub == {"now_showing": None, "up_next": []}


def test_handoff_must_be_confirmed_when_handoff_is_enabled(stores):
    a = submit(stores, "Coco")
    stores[0].rows[a["id"]]["status"] = st.STATUS_READY
    v = q.staff_view(EXPO, store=stores[0], handoff_enabled=True)
    assert v["up_next"] is None and nums(v["preparing"]) == [1]

    stores[0].rows[a["id"]]["handoff_status"] = st.HANDOFF_FAILED
    v = q.staff_view(EXPO, store=stores[0], handoff_enabled=True)
    assert v["up_next"] is None and nums(v["needs_attention"]) == [1]

    stores[0].rows[a["id"]]["handoff_status"] = st.HANDOFF_CONFIRMED
    v = q.staff_view(EXPO, store=stores[0], handoff_enabled=True)
    assert v["up_next"]["queue_number"] == 1


def test_mock_handoff_end_to_end_makes_run_eligible(stores, monkeypatch):
    monkeypatch.setenv("EXHIBITION_HANDOFF_MODE", "mock")
    a = submit(stores, "Coco")
    assert process(stores, a) == "READY"
    assert q.staff_view(EXPO, store=stores[0])["up_next"] is None  # 아직 핸드오프 전
    svc.hand_off_run(a["id"], store=stores[0], artifacts=stores[1])
    assert q.staff_view(EXPO, store=stores[0])["up_next"]["queue_number"] == 1


def test_promote_is_idempotent(stores):
    for name in ("Coco", "Bori"):
        r = submit(stores, name)
        stores[0].rows[r["id"]]["status"] = st.STATUS_READY
    first = q.promote_up_next(EXPO, store=stores[0], handoff_enabled=False)
    assert first["queue_number"] == 1
    assert q.promote_up_next(EXPO, store=stores[0], handoff_enabled=False) is None
    statuses = sorted(r["display_status"] for r in stores[0].rows.values())
    assert statuses == ["UP_NEXT", "WAITING"]


# ── SHOW NEXT ────────────────────────────────────────────────────────────────


def _ready_queue(stores, names):
    rows = []
    for n in names:
        r = submit(stores, n)
        stores[0].rows[r["id"]]["status"] = st.STATUS_READY
        rows.append(r)
    view(stores)  # 첫 READY → UP_NEXT
    return rows


def test_show_next_transitions_and_complete(stores):
    coco, bori, momo = _ready_queue(stores, ["Coco", "Bori", "Momo"])
    v = view(stores)
    assert v["now_showing"] is None and v["up_next"]["pet_name"] == "Coco" and nums(v["ready"]) == [2, 3]

    v = press_show_next(stores)
    assert v["now_showing"]["pet_name"] == "Coco"
    assert v["up_next"]["pet_name"] == "Bori"
    assert nums(v["ready"]) == [3]

    v = press_show_next(stores)
    # current NOW_SHOWING → COMPLETE, UP_NEXT → NOW_SHOWING, next READY → UP_NEXT
    assert stores[0].get(coco["id"])["display_status"] == "COMPLETE"
    assert v["now_showing"]["pet_name"] == "Bori"
    assert v["up_next"]["pet_name"] == "Momo"
    assert v["ready"] == [] and nums(v["recently_complete"]) == [1]

    v = press_show_next(stores)
    assert v["now_showing"]["pet_name"] == "Momo" and v["up_next"] is None

    v = press_show_next(stores)  # 마지막 건도 COMPLETE 로
    assert v["now_showing"] is None and v["up_next"] is None
    assert nums(v["recently_complete"]) == [3, 2, 1]
    assert {stores[0].get(r["id"])["display_status"] for r in (coco, bori, momo)} == {"COMPLETE"}

    with pytest.raises(q.QueueError) as e:
        press_show_next(stores)
    assert e.value.code == "QUEUE_EMPTY"


def test_next_ready_item_becomes_up_next_when_it_finishes_processing(stores):
    _ready_queue(stores, ["Coco"])
    late = submit(stores, "Bori")
    v = press_show_next(stores)
    assert v["now_showing"]["pet_name"] == "Coco" and v["up_next"] is None
    assert nums(v["preparing"]) == [2]
    # #2 처리 완료 → 다음 폴링에서 UP_NEXT.
    assert process(stores, late) == "READY"
    assert view(stores)["up_next"]["pet_name"] == "Bori"


def test_stale_screen_cannot_double_advance(stores):
    _ready_queue(stores, ["Coco", "Bori", "Momo"])
    v = view(stores, reconcile=False)
    expected = dict(
        expected_now_showing=(v["now_showing"] or {}).get("run_id"),
        expected_up_next=(v["up_next"] or {}).get("run_id"),
    )
    q.show_next(EXPO, store=stores[0], handoff_enabled=False, **expected)
    with pytest.raises(q.QueueError) as e:  # 같은 화면에서 두 번 누름
        q.show_next(EXPO, store=stores[0], handoff_enabled=False, **expected)
    assert e.value.code == "QUEUE_STATE_CHANGED" and e.value.status == 409
    assert view(stores)["now_showing"]["pet_name"] == "Coco"


def test_show_next_does_not_touch_processing_package_or_handoff(stores, monkeypatch):
    rows = _ready_queue(stores, ["Coco", "Bori"])
    store, artifacts = stores
    objects = dict(artifacts.objects)
    before = {r["id"]: {k: v for k, v in store.get(r["id"]).items() if not k.startswith("display_")} for r in rows}

    def _boom(*a, **k):
        raise AssertionError("queue transitions must not regenerate cutout/maps or resend handoff")

    monkeypatch.setattr(svc, "build_package", _boom)
    monkeypatch.setattr(svc, "process_run", _boom)
    monkeypatch.setattr(svc, "hand_off_run", _boom)
    press_show_next(stores)
    press_show_next(stores)
    after = {r["id"]: {k: v for k, v in store.get(r["id"]).items() if not k.startswith("display_")} for r in rows}
    assert after == before and artifacts.objects == objects


def test_public_view_shows_only_numbers_and_names(stores):
    _ready_queue(stores, ["Coco", "Bori", "Momo"])
    submit(stores, "Ruby")  # 처리 중 — 공개 목록에 없다
    press_show_next(stores)
    pub = q.public_view(EXPO, store=stores[0], handoff_enabled=False)
    assert pub == {
        "now_showing": {"queue_number": 1, "pet_name": "Coco"},
        "up_next": [{"queue_number": 2, "pet_name": "Bori"}, {"queue_number": 3, "pet_name": "Momo"}],
    }


def test_ops_runs_without_queue_number_stay_out_of_the_queue(stores):
    store, artifacts = stores
    row = svc.create_run(_jpeg(400, 400), created_by="ops", store=store, artifacts=artifacts, exhibition_id=EXPO)
    assert row.get("queue_number") is None and row.get("display_status") is None
    store.rows[row["id"]]["status"] = st.STATUS_READY
    v = view(stores)
    assert v["up_next"] is None and v["ready"] == [] and v["preparing"] == []


# ── API ──────────────────────────────────────────────────────────────────────


def _app(stores, *, ops: bool):
    from backend.auth import AuthedUser, require_user
    from backend.routers import exhibition_prep_v1, exhibition_queue_v1
    from backend.services.shaker_ops import require_ops

    app = FastAPI()
    app.include_router(exhibition_queue_v1.router, prefix="/api")
    app.dependency_overrides[require_user] = lambda: AuthedUser(user_id="staff-1")
    if ops:
        app.dependency_overrides[require_ops] = lambda: AuthedUser(user_id="staff-1")
    app.dependency_overrides[exhibition_prep_v1.get_stores] = lambda: stores
    return ASGITestClient(app)


def test_api_staff_flow_and_public_queue(stores):
    client = _app(stores, ops=True)
    ids = []
    for name in ("Coco", "Bori"):
        res = client.post(
            "/api/exhibition/staff/submissions",
            files={"file": ("dog.jpg", _jpeg(640, 480), "image/jpeg")},
            data={"pet_name": name, "exhibition_id": EXPO},
        )
        assert res.status_code == 200, res.text
        body = res.json()
        assert body["display_status"] == "WAITING" and body["processing_status"] == "QUEUED"
        assert set(body) >= {"run_id", "queue_number", "pet_name", "created_at", "display_status"}
        ids.append(body["run_id"])
    assert [stores[0].get(i)["queue_number"] for i in ids] == [1, 2]
    for i in ids:
        stores[0].rows[i]["status"] = st.STATUS_READY

    staff = client.get(f"/api/exhibition/staff/queue?exhibition_id={EXPO}")
    assert staff.status_code == 200 and staff.headers["cache-control"] == "no-store"
    s = staff.json()
    assert s["up_next"]["pet_name"] == "Coco" and nums(s["ready"]) == [2]

    res = client.post("/api/exhibition/staff/queue/show-next", json={
        "exhibition_id": EXPO, "expected_now_showing_run_id": None, "expected_up_next_run_id": s["up_next"]["run_id"],
    })
    assert res.status_code == 200, res.text
    assert res.json()["now_showing"]["pet_name"] == "Coco" and res.json()["up_next"]["pet_name"] == "Bori"

    again = client.post("/api/exhibition/staff/queue/show-next", json={
        "exhibition_id": EXPO, "expected_now_showing_run_id": None, "expected_up_next_run_id": s["up_next"]["run_id"],
    })
    assert again.status_code == 409 and again.json()["detail"]["code"] == "QUEUE_STATE_CHANGED"

    pub = client.get(f"/api/exhibition/queue?exhibition_id={EXPO}")
    assert pub.status_code == 200
    assert pub.json() == {"now_showing": {"queue_number": 1, "pet_name": "Coco"},
                          "up_next": [{"queue_number": 2, "pet_name": "Bori"}]}
    assert "run_id" not in pub.text

    bad = client.get("/api/exhibition/queue?exhibition_id=../x")
    assert bad.status_code == 400


def test_api_staff_endpoints_require_ops_but_public_queue_does_not(stores, monkeypatch):
    monkeypatch.delenv("SHAKER_OPS_USER_IDS", raising=False)
    monkeypatch.delenv("OPS_USER_IDS", raising=False)
    client = _app(stores, ops=False)
    assert client.post("/api/exhibition/staff/submissions",
                       files={"file": ("d.jpg", _jpeg(64, 64), "image/jpeg")}).status_code == 403
    assert client.get("/api/exhibition/staff/queue").status_code == 403
    assert client.post("/api/exhibition/staff/queue/show-next", json={}).status_code == 403
    assert stores[0].rows == {}
    assert client.get("/api/exhibition/queue").status_code == 200


def test_default_exhibition_id_from_env(stores, monkeypatch):
    monkeypatch.setenv("EXHIBITION_DEFAULT_ID", "seoul-2026")
    assert submit(stores, exhibition_id=None)["exhibition_id"] == "seoul-2026"


# ── 격리 ─────────────────────────────────────────────────────────────────────


class _FakeSupabaseWithRpc(_FakeSupabase):
    def __init__(self):
        super().__init__()
        self.counters: dict[str, int] = {}
        self.rpcs: list[tuple[str, dict]] = []

    def rpc(self, name, params):
        self.rpcs.append((name, params))
        eid = params["p_exhibition_id"]
        self.counters[eid] = self.counters.get(eid, 0) + 1
        n = self.counters[eid]
        return type("Q", (), {"execute": lambda _s: type("R", (), {"data": n})()})()


def test_queue_touches_only_exhibition_state_never_normal_pipeline(monkeypatch):
    from backend.services import pet_reference_service, supabase_assets

    def _boom(*a, **k):
        raise AssertionError("exhibition queue must not write normal pet state")

    monkeypatch.setattr(pet_reference_service, "record_derived", _boom)
    monkeypatch.setattr(supabase_assets, "ensure_user_asset_row", _boom)
    monkeypatch.setattr(supabase_assets, "upload_asset_to_storage", _boom)

    client = _FakeSupabaseWithRpc()
    store, artifacts = st.SupabaseRunStore(client), st.SupabaseArtifactStore(client, bucket="b")
    rows = [q.submit(_jpeg(800, 800), created_by="s", store=store, artifacts=artifacts,
                     exhibition_id=EXPO, pet_name=n) for n in ("Coco", "Bori")]
    assert [r["queue_number"] for r in rows] == [1, 2]
    for r in rows:
        claimed = store.claim_next("w1", stale_after_minutes=20)
        svc.process_run(claimed, store=store, artifacts=artifacts, worker_id="w1", matte_fn=fake_matte(frontal_dog))
    q.staff_view(EXPO, store=store, handoff_enabled=False)
    v = q.staff_view(EXPO, store=store, handoff_enabled=False, reconcile=False)
    q.show_next(EXPO, expected_now_showing=None, expected_up_next=v["up_next"]["run_id"],
                store=store, handoff_enabled=False)

    assert {t for t, _op, _p in client.ops} == {"exhibition_prep_runs"}
    assert {name for name, _p in client.rpcs} == {"exhibition_next_queue_number"}
    assert all(path.startswith("exhibition/") for (_b, path) in client.objects)
    assert client.rows[rows[0]["id"]]["display_status"] == "NOW_SHOWING"
    assert client.rows[rows[1]["id"]]["display_status"] == "UP_NEXT"
