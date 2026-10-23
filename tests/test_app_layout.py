"""The repository deploys as a Databricks App as it is (Apps > Deploy from Git reads its root)."""

import ast
from pathlib import Path

import yaml

ROOT = Path(__file__).parents[1]


def test_app_yaml_at_the_root_starts_a_file_that_exists():
    spec = yaml.safe_load((ROOT / "app.yaml").read_text(encoding="utf-8"))
    cmd = spec["command"]
    assert cmd[0] == "python" and (ROOT / cmd[1]).is_file()
    env = {e["name"]: e for e in spec["env"]}
    assert env["WISHBRIDGE_PROJECTS_VOLUME"]["value"].startswith("/Volumes/")  # a working default for Git deploys
    assert env["WISHBRIDGE_WAREHOUSE_ID"]["valueFrom"] == "sql-warehouse"
    assert not any("__" in str(e.get("value", "")) for e in spec["env"])  # no unfilled placeholders


def test_the_start_file_finds_wishbridge_in_src():
    text = (ROOT / "databricks_app" / "app_main.py").read_text(encoding="utf-8")
    ast.parse(text)
    assert 'os.path.join(os.path.dirname(here), "src")' in text
    assert (ROOT / "databricks_app" / ".." / "src" / "wishbridge" / "ui" / "app.py").resolve().is_file()


def test_the_app_installs_its_packages_from_the_root():
    req = (ROOT / "requirements.txt").read_text(encoding="utf-8")
    for pkg in ("streamlit", "databricks-sdk", "pandas", "pyyaml"):
        assert pkg in req
