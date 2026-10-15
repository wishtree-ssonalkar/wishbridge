"""Fix code shows the client's original even when the converter renamed the file."""

from wishbridge.warehouses.ssis.config import load_config
from wishbridge.warehouses.ssis.convert import original_file


def _project(tmp_path):
    (tmp_path / "input" / "SalesDW_ETL").mkdir(parents=True)
    (tmp_path / "input" / "SalesDW_ETL" / "LoadFactSales.dtsx").write_text("<DTS:Executable/>", encoding="utf-8")
    (tmp_path / "input" / "SalesDW_ETL" / "Master.dtsx").write_text("<DTS:Executable/>", encoding="utf-8")
    (tmp_path / "database_code" / "dw" / "Views").mkdir(parents=True)
    (tmp_path / "database_code" / "dw" / "Views" / "dw.vSales.sql").write_text("CREATE VIEW dw.vSales AS SELECT 1",
                                                                               encoding="utf-8")
    (tmp_path / "project.yml").write_text("source: ssis\n", encoding="utf-8")
    return load_config(tmp_path / "project.yml")


def test_ssis_notebook_finds_its_package(tmp_path):
    cfg = _project(tmp_path)
    # with or without the project folder the converter may have dropped
    assert original_file(cfg, "SalesDW_ETL/LoadFactSales.py").name == "LoadFactSales.dtsx"
    assert original_file(cfg, "Master.py").name == "Master.dtsx"


def test_database_code_and_schema(tmp_path):
    cfg = _project(tmp_path)
    assert original_file(cfg, "_database_code/dw/Views/dw.vSales.sql").read_text(encoding="utf-8").startswith("CREATE VIEW")
    assert original_file(cfg, "_database_schema/dw.sql") is None  # made from the table definitions: no original
    assert original_file(cfg, "Unknown.py") is None
