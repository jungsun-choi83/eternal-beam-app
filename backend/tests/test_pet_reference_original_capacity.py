"""Concurrency contract for the three-active-original database invariant."""

from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

import anyio
import pytest

from backend.services import pet_reference_service as refs
from backend.services import pet_registry

from .conftest import make_jpeg_bytes


ROOT = Path(__file__).resolve().parents[2]
BASE_MIGRATION = ROOT / "supabase" / "migrations" / "20261010000000_pet_reference_images.sql"
CAPACITY_MIGRATION = (
    ROOT
    / "supabase"
    / "migrations"
    / "20261106000000_pet_reference_original_capacity.sql"
)
USER_ID = "archive_capacity_test"
CONTENT_ID = "archive_capacity_test"
PET_ID = "pet_archive_capacity_test"


def _run(coro):
    return anyio.run(lambda: coro)


@pytest.fixture(autouse=True)
def _mock_store(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("HYBRID_USE_SUPABASE", "0")
    refs.__reset_for_tests()
    pet_registry.__reset_for_tests()

    from backend.services import supabase_assets

    async def fake_upload(path: str, data: bytes, content_type: str) -> str:
        return f"mock://{path}"

    monkeypatch.setattr(supabase_assets, "upload_asset_to_storage", fake_upload)
    yield
    refs.__reset_for_tests()
    pet_registry.__reset_for_tests()


async def _record(data: bytes, *, accepted: bool = True):
    return await refs.record_original(
        user_id=USER_ID,
        content_id=CONTENT_ID,
        data=data,
        mime_type="image/jpeg",
        acceptance_state=(refs.STATE_ACCEPTED if accepted else refs.STATE_REJECTED),
        rejection_code=(None if accepted else refs.REJECTION_SUPERSEDED_BY_USER),
    )


async def _seed(*, accepted: int, rejected: int = 0) -> tuple[list[bytes], list[bytes]]:
    accepted_bytes = [make_jpeg_bytes(80 + i, 60 + i) for i in range(accepted)]
    rejected_bytes = [make_jpeg_bytes(120 + i, 90 + i) for i in range(rejected)]
    for data in accepted_bytes:
        await _record(data)
    for data in rejected_bytes:
        await _record(data, accepted=False)
    return accepted_bytes, rejected_bytes


def _assert_one_limit(results: list[object]) -> None:
    errors = [result for result in results if isinstance(result, Exception)]
    successes = [result for result in results if not isinstance(result, Exception)]
    assert len(successes) == 1
    assert len(errors) == 1
    assert isinstance(errors[0], refs.PetReferenceError)
    assert errors[0].code == refs.ORIGINAL_CAPACITY_ERROR_CODE
    assert errors[0].status == 409


def test_concurrent_new_insert_and_new_insert_stays_at_three(monkeypatch):
    async def scenario():
        await _seed(accepted=2)

        from backend.services import supabase_assets

        both_started = asyncio.Event()
        release = asyncio.Event()
        started = 0

        async def synchronized_upload(path: str, data: bytes, content_type: str) -> str:
            nonlocal started
            started += 1
            if started == 2:
                both_started.set()
            await both_started.wait()
            await release.wait()
            return f"mock://{path}"

        monkeypatch.setattr(supabase_assets, "upload_asset_to_storage", synchronized_upload)
        tasks = [
            asyncio.create_task(_record(make_jpeg_bytes(160 + i, 110 + i)))
            for i in range(2)
        ]
        await both_started.wait()
        release.set()
        return await asyncio.gather(*tasks, return_exceptions=True)

    results = anyio.run(scenario)
    _assert_one_limit(results)
    ledger = _run(refs.list_references(user_id=USER_ID, pet_id=PET_ID))
    assert len(refs.active_originals(ledger)) == 3


def test_concurrent_reactivation_capacity_error_maps_to_409(monkeypatch):
    class CapacityError(Exception):
        message = refs.ORIGINAL_CAPACITY_ERROR_CODE

    async def rejected_by_trigger(*args, **kwargs):
        raise CapacityError("database trigger rejected concurrent reactivation")

    monkeypatch.setattr(refs, "_update_acceptance_rows", rejected_by_trigger)

    with pytest.raises(refs.PetReferenceError) as error:
        _run(refs._set_acceptance(PET_ID, ["original-id"], state=refs.STATE_ACCEPTED, rejection_code=None))

    assert error.value.code == refs.ORIGINAL_CAPACITY_ERROR_CODE
    assert error.value.status == 409


def test_concurrent_insert_capacity_error_maps_to_409(monkeypatch):
    class CapacityError(Exception):
        message = refs.ORIGINAL_CAPACITY_ERROR_CODE

    async def rejected_by_trigger(row):
        return False, CapacityError("database trigger rejected concurrent insert")

    monkeypatch.setattr(refs, "_insert_row", rejected_by_trigger)

    with pytest.raises(refs.PetReferenceError) as error:
        _run(_record(make_jpeg_bytes(200, 150)))

    assert error.value.code == refs.ORIGINAL_CAPACITY_ERROR_CODE
    assert error.value.status == 409


def test_concurrent_new_insert_and_reactivation_stays_at_three(monkeypatch):
    async def scenario():
        _, rejected = await _seed(accepted=2, rejected=1)

        from backend.services import supabase_assets

        upload_started = asyncio.Event()
        release_upload = asyncio.Event()

        async def delayed_upload(path: str, data: bytes, content_type: str) -> str:
            upload_started.set()
            await release_upload.wait()
            return f"mock://{path}"

        monkeypatch.setattr(supabase_assets, "upload_asset_to_storage", delayed_upload)
        new_task = asyncio.create_task(_record(make_jpeg_bytes(180, 130)))
        await upload_started.wait()
        reactivated = await _record(rejected[0])
        release_upload.set()
        inserted = await asyncio.gather(new_task, return_exceptions=True)
        return [reactivated, inserted[0]]

    results = anyio.run(scenario)
    _assert_one_limit(results)
    ledger = _run(refs.list_references(user_id=USER_ID, pet_id=PET_ID))
    assert len(refs.active_originals(ledger)) == 3


def test_duplicate_retry_still_succeeds_at_capacity():
    async def scenario():
        accepted, _ = await _seed(accepted=3)
        return await _record(accepted[0])

    duplicate = anyio.run(scenario)
    assert duplicate.deduplicated is True
    ledger = _run(refs.list_references(user_id=USER_ID, pet_id=PET_ID))
    assert len(refs.active_originals(ledger)) == 3


def test_capacity_migration_has_locked_insert_and_reactivation_guards():
    sql = CAPACITY_MIGRATION.read_text(encoding="utf-8").lower()
    assert "pg_advisory_xact_lock(hashtextextended(new.pet_id, 0))" in sql
    assert "new.role <> 'original'" in sql
    assert "new.acceptance_state <> 'accepted'" in sql
    assert "pri.id <> old.id" in sql
    assert "v_active_count >= 3" in sql
    assert "before insert" in sql
    assert "before update of pet_id, role, acceptance_state" in sql
    assert refs.ROLE_ORIGINAL == "original"
    assert refs.STATE_ACCEPTED == "accepted"
    assert refs.MAX_ORIGINALS_PER_PET == 3


def _pg_bin(name: str) -> str | None:
    found = shutil.which(name)
    if found:
        return found
    for base in (
        "/opt/homebrew/opt/postgresql@16/bin",
        "/usr/local/opt/postgresql@16/bin",
    ):
        candidate = os.path.join(base, name)
        if os.path.exists(candidate):
            return candidate
    return None


class _Postgres:
    def __init__(self, psql: str, socket_dir: str, port: int) -> None:
        self.base = [
            psql,
            "-h",
            socket_dir,
            "-p",
            str(port),
            "-U",
            "postgres",
            "-d",
            "postgres",
            "-X",
            "-q",
            "-v",
            "ON_ERROR_STOP=1",
        ]

    def run(self, sql: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [*self.base, "-At", "-c", sql],
            capture_output=True,
            text=True,
        )

    def ok(self, sql: str) -> str:
        result = self.run(sql)
        assert result.returncode == 0, result.stderr
        return result.stdout.strip()

    def file(self, path: Path) -> None:
        result = subprocess.run(
            [*self.base, "-f", str(path)],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stderr

    def start(self, sql: str) -> subprocess.Popen:
        return subprocess.Popen(
            [*self.base, "-At", "-c", sql],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )


@pytest.fixture(scope="module")
def postgres():
    initdb = _pg_bin("initdb")
    pg_ctl = _pg_bin("pg_ctl")
    psql = _pg_bin("psql")
    if not (initdb and pg_ctl and psql):
        pytest.skip("local PostgreSQL binaries are not available")

    root = tempfile.mkdtemp(prefix="eb-capacity-pg-")
    env = {**os.environ, "LC_ALL": "C", "LANG": "C"}
    env.pop("LC_CTYPE", None)
    data = os.path.join(root, "data")
    port = 57000 + os.getpid() % 1000
    try:
        subprocess.run(
            [
                initdb,
                "-D",
                data,
                "-U",
                "postgres",
                "-A",
                "trust",
                "--locale=C",
                "-E",
                "UTF8",
            ],
            check=True,
            capture_output=True,
            env=env,
        )
        subprocess.run(
            [
                pg_ctl,
                "-D",
                data,
                "-w",
                "-l",
                os.path.join(root, "postgres.log"),
                "-o",
                f"-c listen_addresses='' -c unix_socket_directories='{root}' -p {port}",
                "start",
            ],
            check=True,
            capture_output=True,
            env=env,
        )
        db = _Postgres(psql, root, port)
        db.ok("create extension if not exists pgcrypto")
        db.file(BASE_MIGRATION)
        db.file(CAPACITY_MIGRATION)
        yield db
    finally:
        subprocess.run(
            [pg_ctl, "-D", data, "-m", "immediate", "stop"],
            capture_output=True,
        )
        shutil.rmtree(root, ignore_errors=True)


def _uuid(number: int) -> str:
    return f"00000000-0000-0000-0000-{number:012d}"


def _insert_sql(number: int, *, version: int, state: str, content_hash: str) -> str:
    rejection = "null" if state == "accepted" else "'SUPERSEDED_BY_USER'"
    return f"""
      insert into public.pet_reference_images (
        id, pet_id, content_id, user_id, role, source, bucket, object_path,
        content_hash, acceptance_state, rejection_code, version
      ) values (
        '{_uuid(number)}', 'pet-race', 'race', 'owner', 'original', 'ops',
        'user-assets', 'owner/race/{number}.jpg', '{content_hash}', '{state}',
        {rejection}, {version}
      )
    """


def _seed_sql_rows(postgres: _Postgres, states: list[str]) -> None:
    postgres.ok("truncate table public.pet_reference_images")
    for index, state in enumerate(states, start=1):
        postgres.ok(
            _insert_sql(
                index,
                version=index,
                state=state,
                content_hash=f"hash-{index}",
            )
        )


def _race(postgres: _Postgres, first: str, second: str):
    first_process = postgres.start(f"begin; {first}; select pg_sleep(0.4); commit;")
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if first_process.poll() is not None:
            stdout, stderr = first_process.communicate()
            pytest.fail(f"first mutation exited before holding the lock: {stdout} {stderr}")
        if postgres.ok(
            "select count(*) from pg_locks where locktype='advisory' and granted"
        ) != "0":
            break
        time.sleep(0.02)
    else:
        first_process.terminate()
        pytest.fail("first mutation did not acquire the advisory lock")

    second_process = postgres.start(f"begin; {second}; commit;")
    first_stdout, first_stderr = first_process.communicate(timeout=5)
    second_stdout, second_stderr = second_process.communicate(timeout=5)
    assert first_process.returncode == 0, first_stderr or first_stdout
    return second_process.returncode, second_stdout, second_stderr


def _assert_db_limit(postgres: _Postgres, result) -> None:
    returncode, stdout, stderr = result
    assert returncode != 0, stdout
    assert refs.ORIGINAL_CAPACITY_ERROR_CODE in stderr
    assert postgres.ok(
        "select count(*) from public.pet_reference_images "
        "where pet_id='pet-race' and role='original' and acceptance_state='accepted'"
    ) == "3"


def test_postgres_concurrent_new_insert_and_new_insert(postgres: _Postgres):
    _seed_sql_rows(postgres, ["accepted", "accepted"])
    _assert_db_limit(
        postgres,
        _race(
            postgres,
            _insert_sql(3, version=3, state="accepted", content_hash="new-3"),
            _insert_sql(4, version=4, state="accepted", content_hash="new-4"),
        ),
    )


def test_postgres_concurrent_reactivation_and_reactivation(postgres: _Postgres):
    _seed_sql_rows(postgres, ["accepted", "accepted", "rejected", "rejected"])
    _assert_db_limit(
        postgres,
        _race(
            postgres,
            "update public.pet_reference_images set acceptance_state='accepted', "
            f"rejection_code=null where id='{_uuid(3)}'",
            "update public.pet_reference_images set acceptance_state='accepted', "
            f"rejection_code=null where id='{_uuid(4)}'",
        ),
    )


def test_postgres_concurrent_new_insert_and_reactivation(postgres: _Postgres):
    _seed_sql_rows(postgres, ["accepted", "accepted", "rejected"])
    _assert_db_limit(
        postgres,
        _race(
            postgres,
            _insert_sql(4, version=4, state="accepted", content_hash="new-4"),
            "update public.pet_reference_images set acceptance_state='accepted', "
            f"rejection_code=null where id='{_uuid(3)}'",
        ),
    )


def test_postgres_metadata_update_and_duplicate_retry_behavior(postgres: _Postgres):
    _seed_sql_rows(postgres, ["accepted", "accepted", "accepted"])

    assert postgres.ok(
        "update public.pet_reference_images set original_filename='renamed.jpg' "
        f"where id='{_uuid(1)}'; select original_filename from "
        f"public.pet_reference_images where id='{_uuid(1)}'"
    ) == "renamed.jpg"

    duplicate = postgres.run(
        _insert_sql(4, version=4, state="accepted", content_hash="hash-1")
    )
    assert duplicate.returncode != 0
    assert refs.ORIGINAL_CAPACITY_ERROR_CODE not in duplicate.stderr
    assert "pet_reference_images_original_hash_uidx" in duplicate.stderr
