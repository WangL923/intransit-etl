#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
intransit-etl — 供应商发货 / 在执行订单横表自动转结构化在途数据（作品集演示版）

对应真实业务场景：采购每周下发多供应商“发货跟踪表”，同一张表里混着
三种业务语义——剩余未排、项目分配、按发货日期的明细（横表）。本工具自动
定位表头（多关键字别名）、过滤隐藏 sheet、拆分三类数据、结合运输周期表
推算到货日期，输出统一结构化数据集。

核心设计：
1. 表头多别名定位：不同供应商 sheet 列名写法不一，别名列表兜住差异
2. 三类数据分离抽取：surplus（剩余未排）/ project（项目分配）/ delivery（按日明细宽转长）
3. 在途推算：供应商+基地 merge 运输周期表 → 到货日期 = 发货日期 + 周期
4. 规格口径映射外置（config/spec_map.csv），与代码分离
5. 单文件异常隔离 + failed.csv（与姊妹项目同一套架构）

用法：
    python make_sample_data.py
    python intransit_etl.py --input data/orders --config config --out data/output
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

import numpy as np
import pandas as pd

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("intransit-etl")

# ---------------------------------------------------------------------------
# 1. 表头别名配置：不同供应商 sheet 的列名写法差异，全部兜在这里
# ---------------------------------------------------------------------------

ALIASES: dict[str, list[str]] = {
    "supplier":  ["供应商"],
    "spec":      ["品相", "规格型号"],
    "surplus":   ["剩余未排/kg", "剩余未排"],
    "factory":   ["发货基地", "基地", "收货基地"],
    "order":     ["采购合同号", "晶澳合同号", "采购合同号-新", "我方合同号"],
    "sum":       ["合计"],
    "total":     ["合同金额/元", "不含税电汇金额/元", "内部别称", "不含税承兑金额/元", "增值税金额"],
    "project":   ["分配数量/kg", "分配数量"],
    "price":     ["承兑单价", "单价", "合同单价", "电汇单价"],
    # 列区间标志：日期明细列位于 total 列与 surplus 列之间（真实模板布局）
}

LONG_COLS = ["supplier", "spec", "quantity", "factory", "order_no", "ship_date", "price"]


def locate_headers(df: pd.DataFrame) -> dict[str, tuple[int, int]]:
    """在整张表内定位每个字段的表头坐标 (row, col)。返回字段 -> 坐标。

    用向量化扫描替代逐单元格循环；找不到的字段返回 None，由调用方决定容错。
    """
    text = df.astype(str).apply(lambda s: s.str.strip())
    located: dict[str, tuple[int, int]] = {}
    for field, aliases in ALIASES.items():
        hit = None
        for alias in aliases:
            matches = np.argwhere(text.values == alias)
            if len(matches):
                hit = (int(matches[0][0]), int(matches[0][1]))
                break
        located[field] = hit
    return located


# ---------------------------------------------------------------------------
# 2. 三类数据抽取
# ---------------------------------------------------------------------------

def _row_range(loc: dict) -> range:
    return range(loc["supplier"][0] + 1, loc["sum"][0])


def extract_surplus(df: pd.DataFrame, loc: dict) -> pd.DataFrame:
    """剩余未排：每行一条（供应商/规格/数量/基地/合同号/单价）。"""
    rows = []
    for r in _row_range(loc):
        qty = df.iloc[r, loc["surplus"][1]]
        if pd.isna(qty) or qty == 0:
            continue
        rows.append([df.iloc[r, loc["supplier"][1]], df.iloc[r, loc["spec"][1]],
                     qty, df.iloc[r, loc["factory"][1]], df.iloc[r, loc["order"][1]],
                     None, df.iloc[r, loc["price"][1]]])
    return pd.DataFrame(rows, columns=LONG_COLS)


def extract_project(df: pd.DataFrame, loc: dict) -> pd.DataFrame:
    """项目分配：每行一条，quantity 取分配数量列。"""
    if loc.get("project") is None:
        return pd.DataFrame(columns=LONG_COLS)
    rows = []
    for r in _row_range(loc):
        qty = df.iloc[r, loc["project"][1]]
        if pd.isna(qty) or qty == 0:
            continue
        rows.append([df.iloc[r, loc["supplier"][1]], df.iloc[r, loc["spec"][1]],
                     qty, df.iloc[r, loc["factory"][1]], df.iloc[r, loc["order"][1]],
                     None, df.iloc[r, loc["price"][1]]])
    return pd.DataFrame(rows, columns=LONG_COLS)


def extract_delivery_detail(df: pd.DataFrame, loc: dict) -> pd.DataFrame:
    """按发货日期的执行明细：total 列与 surplus 列之间的日期列宽转长。"""
    rows = []
    for r in _row_range(loc):
        for c in range(loc["total"][1] + 1, loc["surplus"][1]):
            qty = df.iloc[r, c]
            if pd.isna(qty) or qty == 0:
                continue
            rows.append([df.iloc[r, loc["supplier"][1]], df.iloc[r, loc["spec"][1]],
                         qty, df.iloc[r, loc["factory"][1]], df.iloc[r, loc["order"][1]],
                         df.iloc[0, c], df.iloc[r, loc["price"][1]]])  # 第 0 行为日期行
    return pd.DataFrame(rows, columns=LONG_COLS)


# ---------------------------------------------------------------------------
# 3. 在途推算：merge 运输周期表 → 到货日期；merge 规格映射 → 统一口径
# ---------------------------------------------------------------------------

def enrich_delivery(df: pd.DataFrame, config_dir: Path) -> pd.DataFrame:
    out = df.copy()

    transit = pd.read_csv(config_dir / "transit_time.csv")
    fac = out["factory"].astype(str)
    out["factory_std"] = "base_a"
    out.loc[fac.str.contains("曲|qj", case=False), "factory_std"] = "base_b"
    out = out.merge(
        transit.rename(columns={"supplier": "_sup", "factory": "_fac", "days": "transit_days"}),
        how="left", left_on=["supplier", "factory_std"], right_on=["_sup", "_fac"],
    ).drop(columns=["_sup", "_fac"])

    out["ship_date"] = pd.to_datetime(out["ship_date"], errors="coerce")
    out["arrival_date"] = out["ship_date"] + pd.to_timedelta(out["transit_days"], unit="D")

    spec_map = pd.read_csv(config_dir / "spec_map.csv")
    out = out.merge(spec_map, how="left", left_on="spec", right_on="spec_alias")

    out["amount"] = pd.to_numeric(out["price"], errors="coerce") * pd.to_numeric(out["quantity"], errors="coerce")
    return out.dropna(subset=["supplier"])


# ---------------------------------------------------------------------------
# 4. 调度：遍历工作簿，过滤隐藏 sheet，逐文件失败隔离
# ---------------------------------------------------------------------------

def parse_workbook(path: Path, config_dir: Path) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    import openpyxl
    wb = openpyxl.load_workbook(path, data_only=True, read_only=True)
    surplus_frames, project_frames, delivery_frames = [], [], []
    for sheet_name in wb.sheetnames:
        if wb[sheet_name].sheet_state != "hidden" and "发货" in sheet_name:
            df = pd.read_excel(path, sheet_name=sheet_name, header=None)
            loc = locate_headers(df)
            if any(v is None for v in (loc["supplier"], loc["spec"], loc["surplus"],
                                       loc["factory"], loc["order"], loc["sum"], loc["total"])):
                log.warning("[%s] 表头定位失败，跳过", sheet_name)
                continue
            surplus_frames.append(extract_surplus(df, loc))
            project_frames.append(extract_project(df, loc))
            delivery_frames.append(extract_delivery_detail(df, loc))
    wb.close()
    if not delivery_frames:
        return (pd.DataFrame(columns=LONG_COLS),) * 3
    return (pd.concat(surplus_frames, ignore_index=True),
            pd.concat(project_frames, ignore_index=True),
            enrich_delivery(pd.concat(delivery_frames, ignore_index=True), config_dir))


def run_pipeline(input_dir: Path, config_dir: Path, out_dir: Path) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    failures, surplus_all, project_all, delivery_all = [], [], [], []

    for xlsx in sorted(input_dir.rglob("*.xlsx")):
        try:
            s, p, d = parse_workbook(xlsx, config_dir)
            surplus_all.append(s)
            project_all.append(p)
            delivery_all.append(d)
            log.info("[%s] surplus=%d project=%d delivery=%d",
                     xlsx.name, len(s), len(p), len(d))
        except Exception as exc:
            failures.append({"file": xlsx.name, "reason": f"{type(exc).__name__}: {exc}"})

    summary = {
        "surplus_rows": 0, "project_rows": 0, "delivery_rows": 0, "failed_files": len(failures),
    }
    if surplus_all:
        pd.concat(surplus_all, ignore_index=True).to_excel(out_dir / "surplus.xlsx", index=False)
        summary["surplus_rows"] = sum(len(f) for f in surplus_all)
    if project_all:
        pd.concat(project_all, ignore_index=True).to_excel(out_dir / "project.xlsx", index=False)
        summary["project_rows"] = sum(len(f) for f in project_all)
    if delivery_all:
        pd.concat(delivery_all, ignore_index=True).to_excel(out_dir / "delivery.xlsx", index=False)
        summary["delivery_rows"] = sum(len(f) for f in delivery_all)
    if failures:
        pd.DataFrame(failures).to_csv(out_dir / "failed.csv", index=False)
    log.info("done: %s", summary)
    return summary


def main() -> None:
    ap = argparse.ArgumentParser(description="intransit-etl demo")
    ap.add_argument("--input", required=True, type=Path)
    ap.add_argument("--config", required=True, type=Path)
    ap.add_argument("--out", required=True, type=Path)
    args = ap.parse_args()
    run_pipeline(args.input, args.config, args.out)


if __name__ == "__main__":
    main()
