"""
모션 QA 규칙 리플레이 — 저장된 qa_result 를 v9 / v10 규칙으로 다시 판정한다.

    # 1) 읽기 전용 덤프 (SELECT 만; 쓰기·프로바이더 호출 없음)
    python -m backend.scripts.replay_motion_qa_rules --fetch --out rows.json

    # 2) 리플레이 (오프라인 — DB 불필요)
    python -m backend.scripts.replay_motion_qa_rules --input rows.json
    python -m backend.scripts.replay_motion_qa_rules --input rows.json --labels labels.csv
    MOTION_QA_V10_BORDERLINE_BAND=0.20 python -m backend.scripts.replay_motion_qa_rules --input rows.json --all

labels.csv: `candidate_id,label` 헤더 포함, label ∈ {good, bad} (접두 8자 id 도 허용).
출력에는 user_id / 이메일 / 서명 URL / 스토리지 경로를 절대 찍지 않는다.
"""

from __future__ import annotations

import argparse
import collections
import csv
import json
import os
import sys
from pathlib import Path
from typing import Any, Optional

# `python backend/scripts/...` 로 실행해도 backend 패키지를 찾도록.
_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from backend.services import motion_video_qa as qa  # noqa: E402

_SAFE_COLUMNS = (
    "id,motion_version_id,motion_id,provider,model,attempt,prompt_version,"
    "generation_metadata,qa_result,decision,selected,error,created_at"
)


def _load_env_file(path: Path) -> None:
    if not path.is_file():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        v = v.strip().strip('"').strip("'")
        if v and k not in os.environ:
            os.environ[k] = v


def fetch_rows(out: Path) -> list[dict[str, Any]]:
    """읽기 전용 SELECT. 후보 + 버전(motion_class 용)을 한 JSON 으로 저장한다."""
    _load_env_file(_ROOT / ".env.local")
    _load_env_file(_ROOT / "backend" / "env.local")
    from supabase import create_client

    url = os.environ.get("SUPABASE_URL") or os.environ.get("VITE_SUPABASE_URL") or ""
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY") or ""
    if not url or not key:
        raise SystemExit("SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY 가 필요합니다 (읽기 전용).")
    client = create_client(url, key)
    cand_table = os.getenv("PET_MOTION_CANDIDATES_TABLE", "pet_motion_candidates")
    ver_table = os.getenv("PET_MOTION_VERSIONS_TABLE", "pet_motion_versions")

    def _page(table: str, cols: str) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        start, page = 0, 1000
        while True:
            r = client.table(table).select(cols).order("created_at").range(start, start + page - 1).execute()
            data = getattr(r, "data", None) or []
            rows.extend(data)
            if len(data) < page:
                break
            start += page
        return rows

    candidates = _page(cand_table, _SAFE_COLUMNS)
    versions = _page(ver_table, "id,motion_id,motion_class,version,status")
    payload = {"candidates": candidates, "versions": versions}
    out.write_text(json.dumps(payload, ensure_ascii=False, default=str))
    print(f"saved {len(candidates)} candidates / {len(versions)} versions → {out}")
    return candidates


def load_rows(path: Path) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    data = json.loads(path.read_text())
    if isinstance(data, dict):
        candidates = list(data.get("candidates") or [])
        versions = {str(v.get("id")): v for v in (data.get("versions") or [])}
    else:
        candidates, versions = list(data), {}
    return candidates, versions


def load_labels(path: Optional[Path]) -> dict[str, str]:
    if not path:
        return {}
    labels: dict[str, str] = {}
    with path.open() as fh:
        for row in csv.DictReader(fh):
            cid = str(row.get("candidate_id") or "").strip()
            label = str(row.get("label") or "").strip().lower()
            if cid and label in ("good", "bad"):
                labels[cid] = label
    return labels


def _label_for(labels: dict[str, str], cid: str) -> Optional[str]:
    if cid in labels:
        return labels[cid]
    for k, v in labels.items():
        if cid.startswith(k) or k.startswith(cid):
            return v
    return None


def replay(
    candidates: list[dict[str, Any]],
    versions: dict[str, dict[str, Any]],
    *,
    labels: dict[str, str],
    show_all: bool,
    reclassify_temporal: bool,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for c in sorted(candidates, key=lambda c: str(c.get("created_at") or "")):
        q = c.get("qa_result") or {}
        if not q or not q.get("checks"):
            continue  # ERROR 후보 — QA 결과 없음
        motion_id = str(c.get("motion_id") or "")
        motion_class = (versions.get(str(c.get("motion_version_id"))) or {}).get("motion_class")
        old = qa.rescore_stored_qa_result(
            q, motion_id=motion_id, motion_class=motion_class,
            ruleset=qa.RULESET_V9, reclassify_temporal=reclassify_temporal,
        )
        new = qa.rescore_stored_qa_result(
            q, motion_id=motion_id, motion_class=motion_class,
            ruleset=qa.RULESET_V10, reclassify_temporal=reclassify_temporal,
        )
        rows.append(
            {
                "candidate_id": str(c.get("id")),
                "motion_id": motion_id,
                "provider": str(c.get("provider") or ""),
                "attempt": int(c.get("attempt") or 0),
                "stored_version": str(q.get("qa_version") or ""),
                "stored": str(c.get("decision") or ""),
                "v9": old["decision"],
                "v10": new["decision"],
                "v9_reasons": old["reasons"],
                "v10_reasons": new["reasons"],
                "downgrades": new["judgement"].get("downgrades") or [],
                "advisories": new["judgement"].get("advisories") or [],
                "hard_fails": new["judgement"].get("hard_fails") or [],
                "temporal_verdict": new.get("temporal_verdict"),
                "label": _label_for(labels, str(c.get("id"))),
            }
        )
    return rows


def print_report(rows: list[dict[str, Any]], *, show_all: bool) -> None:
    changed = [r for r in rows if r["v9"] != r["v10"]]
    print(f"\nrows with QA result: {len(rows)}   v9→v10 decision changes: {len(changed)}")
    fidelity = [r for r in rows if r["stored_version"] == qa.MOTION_VIDEO_QA_VERSION]
    mism = [r for r in fidelity if r["stored"] != r["v9"]]
    print(f"v9 fidelity (stored v9 rows re-scored under v9): {len(fidelity) - len(mism)}/{len(fidelity)} identical"
          + (f"  ⚠ mismatches: {[r['candidate_id'][:8] for r in mism]}" if mism else ""))

    trans = collections.Counter((r["v9"], r["v10"]) for r in rows)
    print("\ntransition matrix (v9 → v10):")
    for (a, b), n in sorted(trans.items()):
        mark = "" if a == b else "  ←"
        print(f"   {a:6} → {b:6} {n:>4}{mark}")

    hdr = f"{'candidate':9} {'motion':13} {'provider':15} {'att':>3} {'stored(ver)':22} {'v9':6} {'v10':6}  change"
    print("\n" + hdr)
    print("-" * len(hdr))
    for r in rows:
        if not show_all and r["v9"] == r["v10"]:
            continue
        why = "; ".join(
            f"{d.get('check')}:{d.get('from')}→{d.get('to')} ({d.get('reason')}, "
            + (f"worst {d.get('worst_ratio')}x" if d.get('worst_ratio') is not None else f"gates {d.get('gate_ratios')}") + ")"
            for d in r["downgrades"]
        )
        why_adv = "; ".join(f"{a.get('check')} advisory ({a.get('reason')})" for a in r["advisories"])
        note = " | ".join(x for x in (why, why_adv) if x)
        print(
            f"{r['candidate_id'][:8]:9} {r['motion_id']:13} {r['provider']:15} {r['attempt']:>3} "
            f"{(r['stored'] + '(' + r['stored_version'].replace('motion-video-qa-', '') + ')'):22} "
            f"{r['v9']:6} {r['v10']:6}  {note}"
        )

    labelled = [r for r in rows if r["label"]]
    if labelled:
        print(f"\nlabels supplied: {len(labelled)}")
        for rs in ("v9", "v10"):
            m = collections.Counter((r["label"], r[rs]) for r in labelled)
            good_pass = m[("good", "PASS")]
            good_block = m[("good", "REVIEW")] + m[("good", "FAIL")]
            bad_pass = m[("bad", "PASS")]
            bad_fail = m[("bad", "FAIL")]
            bad_review = m[("bad", "REVIEW")]
            print(
                f"   {rs:4} good→PASS {good_pass:3}  good→REVIEW {m[('good','REVIEW')]:3}  good→FAIL {m[('good','FAIL')]:3} "
                f"| bad→PASS {bad_pass:3} (fail-open)  bad→REVIEW {bad_review:3}  bad→FAIL {bad_fail:3}"
            )
        print("   (target: bad→PASS must stay 0; v10 should move good→FAIL into REVIEW/PASS)")


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--fetch", action="store_true", help="읽기 전용 SELECT 로 행을 덤프한다 (--out 필요)")
    ap.add_argument("--out", type=Path, default=Path("motion_qa_rows.json"))
    ap.add_argument("--input", type=Path, help="덤프 JSON (dict{candidates,versions} 또는 후보 list)")
    ap.add_argument("--labels", type=Path, help="candidate_id,label(good|bad) CSV")
    ap.add_argument("--all", action="store_true", help="바뀌지 않은 행도 표에 포함")
    ap.add_argument("--no-reclassify-temporal", action="store_true",
                    help="저장된 시간축 verdict 를 그대로 쓴다 (기본: 저장 지표로 현재 분류기 재실행)")
    ap.add_argument("--json", type=Path, help="행별 결과를 JSON 으로도 저장")
    args = ap.parse_args(argv)

    if args.fetch:
        fetch_rows(args.out)
        if not args.input:
            args.input = args.out
    if not args.input:
        ap.error("--input 또는 --fetch 가 필요합니다")

    candidates, versions = load_rows(args.input)
    rows = replay(
        candidates, versions,
        labels=load_labels(args.labels),
        show_all=args.all,
        reclassify_temporal=not args.no_reclassify_temporal,
    )
    print(f"ruleset knobs (env): band={os.getenv('MOTION_QA_V10_BORDERLINE_BAND', '0.15')} "
          f"hard_fail_ratio={os.getenv('MOTION_QA_V10_HARD_FAIL_RATIO', '1.5')} "
          f"snr_strong_mult={os.getenv('MOTION_QA_V10_TORSO_SNR_STRONG_MULT', '2.0')} "
          f"osc_strong_mult={os.getenv('MOTION_QA_V10_OSC_STRONG_MULT', '2.0')}")
    print_report(rows, show_all=args.all)
    if args.json:
        args.json.write_text(json.dumps(rows, ensure_ascii=False, indent=1, default=str))
        print(f"\nper-row results → {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
