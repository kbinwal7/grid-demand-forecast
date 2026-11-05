import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.skipif(
    shutil.which("node") is None,
    reason="Install Node.js to run the dashboard threshold unit tests.",
)
def test_dashboard_threshold_rules():
    with tempfile.TemporaryDirectory() as temp_dir:
        temp_root = Path(temp_dir)
        test_dir = temp_root / "tests"
        module_dir = temp_root / "src" / "gridcast" / "static" / "js"
        test_dir.mkdir(parents=True)
        module_dir.mkdir(parents=True)
        shutil.copy2(
            PROJECT_ROOT / "tests" / "thresholds.test.js",
            test_dir / "thresholds.test.js",
        )
        shutil.copy2(
            PROJECT_ROOT / "src" / "gridcast" / "static" / "js" / "thresholds.js",
            module_dir / "thresholds.js",
        )
        (temp_root / "package.json").write_text('{"type":"module"}', encoding="utf-8")
        subprocess.run(
            ["node", "--test", str(test_dir / "thresholds.test.js")],
            cwd=temp_root,
            check=True,
            capture_output=True,
            text=True,
        )
