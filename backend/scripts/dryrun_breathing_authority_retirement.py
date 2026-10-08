"""
Dry run — retire legacy QA authority for BREATHING (stored rows only).

    python -m backend.scripts.dryrun_breathing_authority_retirement --input state.json

state.json = {"candidates": [...], "runs": [...], "versions": [...], "publications": [...]}
dumped with read-only SELECTs. No provider/VLM calls, no DB writes.

Existing runs carry no authority stamp, so they stay on legacy authority when
resumed or retried: their real outcome does not change. The "business" column is
the hypothetical outcome of the same stored lineage under a run stamped
BREATHING_QA_AUTHORITY=business (i.e. what a new run producing the same
candidates would get). The QA-stage branch order mirrors
pet_generation_run_service._execute; delivery checks call the real helpers.
"""

from __future__ import annotations

import argparse
import collections
import json
import sys
from pathlib import Path
from typing import Any, Optional

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from backend.services import business_qa  # noqa: E402
from backend.services import motion_publication_service as publication  # noqa: E402
from backend.services import motion_video_service as mv  # noqa: E402

LEGACY, BUSINESS = business_qa.QA_AUTHORITY_LEGACY, business_qa.QA_AUTHORITY_BUSINESS


def _sql_gate(mode: str, candidate: dict[str, Any], *, enrolled: bool, in_run: bool) -> bool:
    """Mirror of publish_phase6_breathing after 20261104 (see the SQL tests)."""

    receipt = business_qa.receipt(candidate.get("qa_result") or {})
    delivers = bool(
        receipt
        and receipt.get("delivery_action") in ("DELIVER", "DELIVER_WITH_ADVISORY")
        and receipt.get("integrity_status") != "FAIL"
    )
    if mode == BUSINESS:
        return delivers or (not in_run and publication._is_grandfathered_legacy_pass(candidate))
    return candidate.get("decision") == "PASS" or (enrolled and delivers)


def _run_outcome(mode: str, version: dict[str, Any], candidates: list[dict[str, Any]], enrolled: bool) -> str:
    with business_qa.qa_authority_scope(mode):
        retired = business_qa.legacy_authority_retired("BREATHING")
        selected_id = str(version.get("selected_candidate_id") or "")
        chosen = next(
            (c for c in candidates if str(c["id"]) == selected_id and c.get("selected")
             and mv.candidate_is_publishable(c)),
            None,
        )
        deliverable = chosen if (retired or version.get("status") == mv.STATUS_COMPLETE or (
            chosen is not None and mv.severity_gate_mode() == mv.SEVERITY_GATE_INTEGRITY_ONLY
        )) else None
        if deliverable is not None:
            if _sql_gate(mode, deliverable, enrolled=enrolled, in_run=True):
                return "PUBLISHED (DELIVERED_GENERATED)"
            return "FAILED (CANDIDATE_NOT_PASS at publication)"
        if business_qa.fallback_receipt(candidates) is not None:
            return "DELIVERED_FALLBACK (budget exhausted)"
        evaluated = any(str(c.get("decision") or "") in ("PASS", "REVIEW", "FAIL") for c in candidates)
        if retired and evaluated:
            return "DELIVERED_FALLBACK (safe default: no delivering receipt)"
        if not retired and version.get("status") == mv.STATUS_REVIEW:
            return "FAILED (MOTION_QA_REVIEW)"
        return "FAILED (MOTION_QA_FAILED)"


def _reason(candidate: Optional[dict[str, Any]]) -> str:
    if candidate is None:
        return "no selected candidate"
    receipt = business_qa.receipt(candidate.get("qa_result") or {})
    if receipt is None:
        return f"legacy {candidate.get('decision')}, no receipt"
    return (
        f"legacy {candidate.get('decision')}, receipt {receipt.get('delivery_action')}/"
        f"{receipt.get('retry_action')} integrity={receipt.get('integrity_status')}"
    )


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True, type=Path)
    args = ap.parse_args()
    state = json.loads(args.input.read_text())
    versions = {str(v["id"]): v for v in state["versions"]}
    by_version: dict[str, list[dict[str, Any]]] = collections.defaultdict(list)
    for c in state["candidates"]:
        by_version[str(c["motion_version_id"])].append(c)

    runs = [r for r in state["runs"] if r.get("motion_id") == "BREATHING"]
    print(f"BREATHING runs: {len(runs)}  (authority stamp present on: "
          f"{sum(1 for r in runs if business_qa.QA_AUTHORITY_STAMP_KEY in ((r.get('provider_state') or {}).get('_business_qa_cutover') or {}))})")
    changed, same, no_motion = [], 0, collections.Counter()
    for r in runs:
        vid = str(r.get("motion_version_id") or "")
        if not vid or vid not in versions:
            no_motion[(r["status"], ((r.get("last_error") or {}).get("code")))] += 1
            continue
        cands = sorted(by_version.get(vid, []), key=lambda c: c.get("attempt") or 0)
        enrolled = bool(((r.get("provider_state") or {}).get("_business_qa_cutover") or {}).get("enrolled"))
        legacy = _run_outcome(LEGACY, versions[vid], cands, enrolled)
        business = _run_outcome(BUSINESS, versions[vid], cands, enrolled)
        selected = next((c for c in cands if c.get("selected")), None) or (cands[-1] if cands else None)
        stored = f"{r['status']}" + (f" ({(r.get('last_error') or {}).get('code')})" if r.get("last_error") else "")
        if legacy == business:
            same += 1
        else:
            changed.append((r["id"][:8], r["created_at"][:10], stored, legacy, business, _reason(selected)))
    print(f"runs that never reached the motion stage (unaffected): {sum(no_motion.values())} {dict(no_motion)}")
    print(f"runs with a motion version: same outcome in both modes = {same}, different = {len(changed)}")
    for row in changed:
        print("  RUN %s %s | stored: %s | legacy: %s | business: %s | %s" % row)

    print("\ncandidate deliverability (BREATHING, evaluated candidates only):")
    table = collections.Counter()
    diffs = []
    for c in state["candidates"]:
        if c.get("motion_id") != "BREATHING" or c.get("decision") not in ("PASS", "REVIEW", "FAIL"):
            continue
        with business_qa.qa_authority_scope(LEGACY):
            legacy_ok = mv.candidate_is_publishable(c)
        with business_qa.qa_authority_scope(BUSINESS):
            business_ok = mv.candidate_is_publishable(c)
        direct_ok = business_ok or publication._is_grandfathered_legacy_pass(c)
        table[(legacy_ok, business_ok, direct_ok)] += 1
        if legacy_ok != business_ok:
            diffs.append((c["id"][:8], c["created_at"][:10], legacy_ok, business_ok, direct_ok, _reason(c)))
    for (legacy_ok, business_ok, direct_ok), n in sorted(table.items()):
        print(f"  legacy={legacy_ok!s:5} business(run)={business_ok!s:5} business(direct publish)={direct_ok!s:5}  x{n}")
    for row in diffs:
        print("  CAND %s %s | legacy=%s business(run)=%s direct=%s | %s" % row)


if __name__ == "__main__":
    main()
