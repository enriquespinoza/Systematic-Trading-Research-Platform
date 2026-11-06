"""CLI for immutable local audit evidence and changes-only reports."""
from __future__ import annotations
import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from .registry import (
    EvidenceError, acknowledge, build_report, ingest, load_config,
    open_registry, render_markdown, utc_time,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--registry-dir", type=Path, required=True)
    parser.add_argument("--engine-config", type=Path, default=Path("config/audit-engines.json"))
    sub = parser.add_subparsers(dest="command", required=True)
    imp = sub.add_parser("ingest", help="Verify and append GitHub Actions evidence ZIPs")
    imp.add_argument("artifacts", type=Path, nargs="+")
    col = sub.add_parser("collect", help="Fetch authenticated GitHub Actions artifacts via gh CLI")
    col.add_argument("--per-engine", type=int, default=20)
    col.add_argument("--gh-bin", default="gh")
    rpt = sub.add_parser("report", help="Produce changes-only reports with durable cursor")
    rpt.add_argument("--markdown", type=Path, required=True)
    rpt.add_argument("--json", type=Path, required=True)
    rpt.add_argument("--as-of", type=str, default=None)
    rpt.add_argument("--report-name", default="default")
    rpt.add_argument("--acknowledge", action="store_true")
    args = parser.parse_args()
    try:
        config = load_config(args.engine_config)
        if args.command == "ingest":
            for path in args.artifacts:
                print(json.dumps(ingest(args.registry_dir, path, config), sort_keys=True))
            return 0
        if args.command == "collect":
            from .collect import collect
            result = collect(args.registry_dir, config, per_engine=args.per_engine, gh_bin=args.gh_bin)
            print(json.dumps({"fetched":len(result),"imported":sum(r["result"]=="imported" for r in result)}, sort_keys=True))
            return 0
        if not args.report_name or len(args.report_name)>64:
            raise EvidenceError("Invalid report name")
        now = utc_time(args.as_of) if args.as_of else datetime.now(timezone.utc)
        if args.markdown.resolve() == args.json.resolve():
            raise EvidenceError("Output filenames must differ")
        conn = open_registry(args.registry_dir)
        try:
            report = build_report(conn, config, now, args.report_name)
            for destination in (args.markdown, args.json):
                destination.parent.mkdir(parents=True,exist_ok=True)
            args.json.write_text(json.dumps(report,sort_keys=True,indent=2)+"\n",encoding="utf-8")
            args.markdown.write_text(render_markdown(report),encoding="utf-8")
            if args.acknowledge:
                acknowledge(conn,args.report_name,report)
            print(f"New run evidence: {report['events_seen']}; changed findings: {len(report['changed_findings'])}; acknowledged={args.acknowledge}")
        finally:
            conn.close()
        return 0
    except (EvidenceError,OSError,ValueError) as error:
        print(f"AUDIT_REGISTRY_ERROR: {error}",file=sys.stderr)
        return 1


if __name__=="__main__":
    raise SystemExit(main())
