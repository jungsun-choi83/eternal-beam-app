from pathlib import Path


ROOT = Path(__file__).parents[2]
HARDENING = ROOT / "supabase" / "migrations" / "20261111000000_exhibition_privilege_hardening.sql"
PREFLIGHT = ROOT / "supabase" / "preflight" / "20261110_exhibition_pet_passport_production_preflight_single_result.sql"


def test_hardening_removes_visitor_table_and_trigger_helper_privileges():
    sql = HARDENING.read_text(encoding="utf-8")

    for table in ("exhibition_prep_runs", "exhibition_queue_counters"):
        assert f"REVOKE ALL ON TABLE public.{table}" in sql
        assert f"GRANT ALL ON TABLE public.{table} TO service_role" in sql

    for function in (
        "set_exhibition_prep_runs_updated_at()",
        "set_exhibition_pet_passport_updated_at()",
    ):
        assert f"REVOKE ALL ON FUNCTION public.{function}" in sql
        assert f"GRANT EXECUTE ON FUNCTION public.{function}" in sql

    assert "FROM PUBLIC, anon, authenticated" in sql
    assert "TO service_role" in sql


def test_preflight_resolves_functions_by_canonical_postgres_signature():
    sql = PREFLIGHT.read_text(encoding="utf-8")

    assert "to_regprocedure(e.function_signature)" in sql
    assert "public.exhibition_next_queue_number(text)" in sql
    assert "identity_arguments = e.identity_arguments" not in sql
