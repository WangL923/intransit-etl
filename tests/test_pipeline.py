import shutil
import subprocess
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]


def test_end_to_end(tmp_path):
    data, out = tmp_path / "orders", tmp_path / "out"
    shutil.copytree(ROOT / "data" / "orders", data)

    r = subprocess.run(
        [sys.executable, str(ROOT / "intransit_etl.py"),
         "--input", str(data), "--config", str(ROOT / "config"), "--out", str(out)],
        capture_output=True, text=True)
    assert r.returncode == 0, r.stderr

    d = pd.read_excel(out / "delivery.xlsx")
    # 日期明细宽转长：3+1 个非零明细 → 至少 4 行
    assert len(d) >= 4
    # 别名定位：第二个 sheet 用“品相/剩余未排”也能出数
    assert set(d["supplier"].unique()) == {"NovaSilicon", "HelioMaterials"}
    # 在途推算：到货日期 = 发货日期 + 运输周期
    assert d["arrival_date"].notna().all()
    assert (d["arrival_date"] > d["ship_date"]).all()
    # 供应商间运输周期差异（压力测试叙事的种子）
    gap = d[d["supplier"] == "HelioMaterials"]["transit_days"].max()
    assert gap >= 10
    # hidden sheet 未混入
    assert "Old" not in str(d.get("sheet", ""))
    # 损坏文件被隔离
    failed = pd.read_csv(out / "failed.csv")
    assert "corrupted.xlsx" in set(failed["file"])
