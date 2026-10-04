"""
BREATHING authority dry-run — re-evaluate stored qa_result rows offline.

    python -m backend.scripts.replay_motion_qa_rules --fetch --out rows.json   # read-only SELECT
    python -m backend.scripts.dryrun_breathing_authority --input rows.json
    BREATHING_AUTHORITY_PROFILE=breathing-v1 python -m backend.scripts.dryrun_breathing_authority --input rows.json

No provider/VLM calls and no DB writes: only stored measurements are rescored.
Two modes per candidate: "vlm_off" drops every stored VLM answer (unknown),
"vlm_stored" keeps the VLM evidence that was persisted with the candidate.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from backend.services import business_qa, motion_video_qa as qa  # noqa: E402


def _receipt(stored: dict[str, Any], *, attempt: int, vlm: bool) -> dict[str, Any]:
    source = dict(stored)
    if not vlm:
        source["vlm"] = None
        source["checks"] = {
            k: v for k, v in dict(stored.get("checks") or {}).items() if not k.startswith("vlm_")
        }
    rescored = qa.rescore_stored_qa_result(
        source, motion_id="BREATHING", motion_class="MICRO",
        ruleset=str(stored.get("ruleset") or qa.RULESET_V9),
    )
    merged = {
        **source,
        "decision": rescored["decision"],
        "checks": rescored["checks"],
        "reasons": rescored["reasons"],
        "motion_business_contract": rescored["motion_business_contract"],
        "business_signals": rescored["business_signals"],
    }
    # The stored escalation receipt belongs to the old authority decision.
    merged.pop("vlm_escalation", None)
    merged.pop("business_qa", None)
    return business_qa.build_business_result(
        merged, attempt_number=attempt, request_kind="MICRO", fallback_available=True
    )


def run(path: Path) -> list[dict[str, Any]]:
    data = json.loads(path.read_text())
    rows = []
    for c in sorted(data["candidates"], key=lambda c: str(c.get("created_at") or "")):
        q = c.get("qa_result") or {}
        temporal = q.get("temporal")
        if c.get("motion_id") != "BREATHING" or not isinstance(temporal, dict):
            continue
        metrics = temporal.get("metrics")
        if not isinstance(metrics, dict):
            continue
        attempt = int(c.get("attempt") or 1)
        row: dict[str, Any] = {
            "id": str(c["id"])[:8],
            "attempt": attempt,
            "scale_range": metrics.get("scale_range"),
            "scale_oscillation": metrics.get("scale_oscillation"),
            "scale_trend": metrics.get("scale_trend"),
            "drift": metrics.get("translation_drift_frac_of_pet"),
            "stored_receipt": (q.get("business_qa") or None),
        }
        for mode, vlm in (("vlm_off", False), ("vlm_stored", True)):
            r = _receipt(q, attempt=attempt, vlm=vlm)
            row[mode] = {
                "profile": r["authority_profile"],
                "integrity": r["integrity_status"],
                "quality": r["quality_status"],
                "delivery": r["delivery_action"],
                "retry": r["retry_action"],
                "terminal": r["terminal_state"],
                "non_pass": r["reasons"],
            }
        rows.append(row)
    return rows


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True, type=Path)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    rows = run(args.input)
    if args.json:
        print(json.dumps(rows, ensure_ascii=False))
        return
    print(f"profile={business_qa.BREATHING_AUTHORITY_VERSION}  candidates={len(rows)}")
    for row in rows:
        for mode in ("vlm_off", "vlm_stored"):
            m = row[mode]
            print(
                f"{row['id']} a{row['attempt']} range={row['scale_range']} drift={row['drift']} "
                f"{mode:10s} integ={m['integrity']:6s} qual={m['quality']:6s} "
                f"{m['delivery']:21s} {m['retry']:10s} {m['terminal']}"
            )


if __name__ == "__main__":
    main()
