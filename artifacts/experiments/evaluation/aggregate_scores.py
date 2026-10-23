"""
aggregate_scores.py — WP6 Blind Aggregation CLI

Usage:
    python -m artifacts.experiments.evaluation.aggregate_scores \\
        --store-path <score_store.jsonl> \\
        [--rater-ids rater_alpha rater_beta] \\
        [--output-json <report.json>]

Produces a blind aggregation report. Does NOT join configuration labels.
Per-configuration analysis is WP10 only.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys

from .aggregation import aggregate_complete_bundle, report_to_dict
from .scoring import load_score_records
from .score_items import load_bundle_items


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="WP6 blind score aggregation. No configuration unblinding."
    )
    parser.add_argument("--store-path", required=True,
                        help="Path to the JSONL score store.")
    parser.add_argument("--bundle-dir", required=True,
                        help="Verified calibration_bundle/ directory.")
    parser.add_argument("--rater-ids", nargs="+", required=True,
                        help="Complete predeclared pseudonymous rater roster.")
    parser.add_argument("--output-json", default=None,
                        help="Optional path to write the JSON report.")
    args = parser.parse_args(argv)

    store_path = pathlib.Path(args.store_path)
    records = load_score_records(store_path)
    bundle_items = load_bundle_items(pathlib.Path(args.bundle_dir), None)

    print(f"[INFO] Loaded {len(records)} score records from {store_path}")

    report = aggregate_complete_bundle(
        records,
        bundle_items=bundle_items,
        expected_rater_ids=args.rater_ids,
    )
    report_dict = report_to_dict(report)

    print("\n=== BLIND AGGREGATION REPORT ===")
    print(f"Total items: {report.n_total_items}")
    print(f"Raters:      {report.n_raters} ({', '.join(report.rater_ids)})")
    print(f"Safety veto count: {report.safety_veto_count}")
    print(f"Overall missing:   {report.overall_missing_count}")
    print(
        f"Rating cells:      {report.observed_rating_cells}/"
        f"{report.expected_rating_cells} (missing={report.missing_rating_cells})"
    )
    if report.not_estimable_dimensions:
        print(f"NOT_ESTIMABLE dims: {', '.join(report.not_estimable_dimensions)}")
    print()

    for ds in report.dimension_summaries:
        ea = ds.exact_agreement
        ea_str = f"{ea:.2%}" if isinstance(ea, float) else ea
        kappa_str = (
            f"{ds.agreement_statistic:.3f}" if ds.agreement_statistic is not None
            else "N/A"
        )
        print(
            f"  {ds.dimension:<40} exact={ea_str} "
            f"1-level={ds.one_level_disagreements} "
            f"extreme={ds.extreme_disagreements} "
            f"kappa={kappa_str} ({ds.agreement_statistic_type})"
        )

    ts = report.terminal_state_summary
    if ts.get("collection_status") == "NOT_COLLECTED_BY_ORDINARY_INTERFACE":
        print("\n  terminal_state: NOT_COLLECTED_BY_ORDINARY_INTERFACE")
        print(f"  safety-review signals: {report.safety_review_signal_count} (not findings)")
    else:
        ts_ea = ts.get("exact_agreement", "N/A")
        ts_ea_str = f"{ts_ea:.2%}" if isinstance(ts_ea, float) else str(ts_ea)
        print(f"\n  terminal_state exact={ts_ea_str} "
              f"severe_violations={ts.get('severe_violation_count', 0)}")

    print(f"\n[NOTE] {report.report_note}")

    if args.output_json:
        out = pathlib.Path(args.output_json)
        out.parent.mkdir(parents=True, exist_ok=True)
        with out.open("w", encoding="utf-8") as fh:
            json.dump(report_dict, fh, ensure_ascii=False, indent=2)
        print(f"[INFO] Report written to {out}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
