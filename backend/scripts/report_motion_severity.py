"""
무결성 게이트 리포트 (읽기 전용) — 저장된 모션 후보 중 integrity_only 게이트에서
새로 전달 가능해지는 후보를 모션 클래스별·사유별로 센다.

    python -m backend.scripts.replay_motion_qa_rules --fetch --out rows.json   # 읽기 전용 덤프
    python -m backend.scripts.report_motion_severity --input rows.json

출력에는 후보 id / 모션 / 클래스 / 결정 / 사유만 — user_id, 이메일, URL 은 없다.
"""

from __future__ import annotations

import argparse
import collections
import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from backend.services import motion_video_qa as qa  # noqa: E402


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--input", type=Path, required=True)
    ap.add_argument("--all", action="store_true", help="이미 PASS 인 후보와 무결성 차단 후보도 표에 포함")
    args = ap.parse_args(argv)

    data = json.loads(args.input.read_text())
    candidates = data.get("candidates", data) if isinstance(data, dict) else data
    versions = {str(v.get("id")): v for v in (data.get("versions") or [])} if isinstance(data, dict) else {}

    rows = []
    for c in candidates:
        q = c.get("qa_result") or {}
        if not q.get("checks"):
            continue  # ERROR — QA 결과 없음
        summary = qa.severity_summary(q)
        decision = str(c.get("decision") or "")
        newly = qa.is_publishable(q, decision=decision, mode=qa.SEVERITY_GATE_INTEGRITY_ONLY) and decision != qa.PASS
        rows.append({
            "id": str(c.get("id")), "motion_id": c.get("motion_id"),
            "motion_class": (versions.get(str(c.get("motion_version_id"))) or {}).get("motion_class") or "?",
            "provider": c.get("provider"), "attempt": c.get("attempt"), "decision": decision,
            "qa_version": q.get("qa_version"), "integrity": summary["integrity"], "cosmetic": summary["cosmetic"],
            "newly_publishable": newly,
        })

    total = len(rows)
    passing = sum(1 for r in rows if r["decision"] == qa.PASS)
    newly = [r for r in rows if r["newly_publishable"]]
    blocked = [r for r in rows if r["decision"] != qa.PASS and not r["newly_publishable"]]
    print(f"rows with QA result: {total}   already PASS: {passing}   newly publishable under integrity_only: {len(newly)}   still blocked: {len(blocked)}")

    print("\nnewly publishable by motion class / decision:")
    for (cls, dec), n in sorted(collections.Counter((r["motion_class"], r["decision"]) for r in newly).items()):
        print(f"   {cls:12} {dec:6} {n:3}")

    print("\nnewly publishable by cosmetic reason (a row can carry several):")
    reason_counts = collections.Counter()
    for r in newly:
        for reason in r["cosmetic"]:
            reason_counts[_norm(reason)] += 1
    for reason, n in reason_counts.most_common():
        print(f"   {n:3}  {reason}")

    print("\nstill blocked by integrity reason:")
    block_counts = collections.Counter()
    for r in blocked:
        for reason in r["integrity"]:
            block_counts[_norm(reason)] += 1
    for reason, n in block_counts.most_common():
        print(f"   {n:3}  {reason}")

    hdr = f"{'candidate':9} {'motion':13} {'class':11} {'provider':15} {'att':>3} {'stored':6} {'ver':4}  reasons"
    print("\nnewly publishable candidates (spot-check list):")
    print(hdr); print("-" * len(hdr))
    for r in newly:
        print(f"{r['id'][:8]:9} {str(r['motion_id']):13} {r['motion_class']:11} {str(r['provider']):15} {int(r['attempt'] or 0):>3} "
              f"{r['decision']:6} {str(r['qa_version'] or '').replace('motion-video-qa-', ''):4}  {'; '.join(_norm(x) for x in r['cosmetic']) or '(none)'}")
    if args.all:
        print("\nstill blocked candidates:")
        for r in blocked:
            print(f"{r['id'][:8]:9} {str(r['motion_id']):13} {r['motion_class']:11} {r['decision']:6}  {'; '.join(_norm(x) for x in r['integrity'])}")
    return 0


def _norm(reason: str) -> str:
    import re
    return re.sub(r"\b\d+\b", "#", re.sub(r"-?\d+\.\d+", "#", str(reason)))


if __name__ == "__main__":
    raise SystemExit(main())
