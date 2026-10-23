"""
Interfaccia a riga di comando per il validatore formale.
Gestisce encoding UTF-8, banner, rendering del report ed exit code.
"""
import sys
from pathlib import Path

_exp_base = Path(__file__).resolve().parent.parent
if str(_exp_base) not in sys.path:
    sys.path.insert(0, str(_exp_base))

from .context import create_default_context
from .report import ValidationReport
from .runner import run_validation


def main(argv: list[str] | None = None) -> int:
    # Forza output UTF-8 per console Windows PowerShell
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")

    print("=" * 75)
    print("STATIC INFRASTRUCTURE CHECKS: POL-01..POL-05 + LEGACY COMPATIBILITY")
    print("=" * 75)

    base_dir = Path(__file__).resolve().parent.parent
    ctx = create_default_context(base_dir)
    report = ValidationReport(verbose=True)

    run_validation(ctx, report)
    report.render_summary()

    return 0 if report.is_successful() else 1


if __name__ == "__main__":
    sys.exit(main())
