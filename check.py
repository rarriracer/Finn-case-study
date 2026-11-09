"""Run Finn's regression tests and Excel stress cases."""

import io
import os
from pathlib import Path
import re
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest


ROOT = Path(__file__).resolve().parent


def run_regression_tests():
    report = io.StringIO()
    suite = unittest.defaultTestLoader.discover(str(ROOT / "tests"))
    result = unittest.TextTestRunner(stream=report, verbosity=2).run(suite)
    if result.testsRun == 0:
        report.write("Keine Regressionstests gefunden.\n")
    output = report.getvalue()
    print(output, end="", flush=True)
    return result.testsRun > 0 and result.wasSuccessful(), f"## Regressionstests\n\n```text\n{output}```\n"


def run_excel_tests():
    with TemporaryDirectory(prefix="finn-excel-") as output_dir:
        result = subprocess.run(
            [sys.executable, "tests/stress_import.py", "beispiele/Wirtschaftsplan_2027.xlsx", "--output", output_dir],
            cwd=ROOT,
            env={**os.environ, "PYTHONPATH": str(ROOT)},
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        print(result.stdout, end="", flush=True)
        if result.returncode:
            return False, f"## Excel-Stresstest fehlgeschlagen\n\n```text\n{result.stdout}\n```\n"
        report = (Path(output_dir) / "Ergebnisse.md").read_text(encoding="utf-8")
    return True, re.sub(r"\[([^\]]+)\]\([^)]*\)", r"`\1`", report)


def main():
    regression_ok, regression_report = run_regression_tests()
    excel_ok, excel_report = run_excel_tests()
    if summary_path := os.environ.get("GITHUB_STEP_SUMMARY"):
        with Path(summary_path).open("a", encoding="utf-8") as summary:
            summary.write(f"{regression_report}\n{excel_report}")
    return 0 if regression_ok and excel_ok else 1


if __name__ == "__main__":
    sys.exit(main())
