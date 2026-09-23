# intransit-etl

供应商发货 / 在执行订单横表自动转结构化在途数据（作品集演示版）

采购每周下发多供应商“发货跟踪表”：同一张表里混着剩余未排、项目分配、
按发货日期的执行明细三种业务语义，且各供应商 sheet 列名写法不一。
本工具自动定位表头、过滤隐藏 sheet、拆分三类数据、结合运输周期表推算到货日期，
输出统一结构化数据集，作为库存缺口分析与到货预测的输入。

**Lead: @WangL923**（到货数据模块主导） · Collaborator: @GenGC0128
（工程化重构与测试）

数据为程序合成，不含任何真实业务信息。

## 特性

- **表头多别名定位**：列名写法差异（“品相/规格型号”“剩余未排/kg/剩余未排”…）由别名列表兜住
- **三类数据分离抽取**：surplus / project / 按日明细（宽转长：合同金额列与剩余未排列之间的日期列展开）
- **在途推算**：供应商+基地 merge 运输周期表 → 到货日期 = 发货日期 + 周期
- **规格口径映射外置**（config/spec_map.csv）
- **失败隔离** + 隐藏 sheet 过滤

## 快速开始

```bash
pip install -r requirements.txt
python make_sample_data.py
python intransit_etl.py --input data/orders --config config --out data/output
pytest tests/ -v
```

输出：`delivery.xlsx`（含 ship_date / transit_days / arrival_date / amount）、
`surplus.xlsx`、`project.xlsx`、`failed.csv`

## 设计决策备忘

| 问题 | 方案 |
|---|---|
| 各供应商 sheet 列名不统一 | 字段级别名列表，首个命中即定位 |
| 一张表混三种业务语义 | 拆三个抽取函数，统一 LONG_COLS schema |
| 逐单元格全表扫描定位表头（旧版 1000×1000） | 向量化 `np.argwhere` 一次扫描 |
| 运输周期字典双层 for 匹配（旧版 O(n×m)） | `merge` 连接，O(n log n) |
| 隐藏 sheet 混入脏数据 | `sheet_state != 'hidden'` 过滤 |

## 姊妹项目

- [hetero-pdf-etl] — 异构 PDF 批量解析
- [wide-table-etl] — 多来源 Excel 横表转标准长表
- （规划中）supply-gap-analysis — 库存 + 在途 + 使用计划 → 缺口分析与压力测试

## License

MIT
