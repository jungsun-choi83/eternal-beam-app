from pathlib import Path


MIGRATION = Path(__file__).parents[2] / "supabase" / "migrations" / "20261108000000_exhibition_pet_passport.sql"


def test_passport_photo_reference_cannot_be_deleted_or_duplicated():
    sql = MIGRATION.read_text(encoding="utf-8")

    assert "ON DELETE RESTRICT" in sql
    assert "UNIQUE (photo_run_id)" in sql
    assert "(photo_status = 'PHOTO_RECEIVED') = (photo_run_id IS NOT NULL)" in sql
