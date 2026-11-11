# 多源商品目录对账 · Catalog Reconciliation

一个可复用的 Agent Skill。用两份或三份商品清单，找出漏展示、售价或原价差异，以及可售状态冲突。每项发现都能回查到文件、行和规格编号。

An Agent Skill for comparing two or three product catalogs from CSV, Excel, or JSON. Match variants by a stable SKU, a composite key, or a confirmed crosswalk, then report differences with row-level evidence.

## 用在你的项目

把 [`catalog-reconciliation/`](catalog-reconciliation/) 整个目录放到 Agent 的技能目录。对于 Codex，可放到项目的 `.agents/skills/catalog-reconciliation/`。随后提供你自己的导出文件并调用技能。

> 用 $catalog-reconciliation 核对这两份导出。第一份是后台商品库，第二份是店铺实际展示清单。先看看表头，再核对哪些规格漏展示或价格对不上，每项附上原始行号。

Skill 先查看少量样例行，确认 SKU 或「商品编号 + 颜色 + 尺码」这样的组合键。跨系统编号通过已知的 SKU 对照表匹配。Agent 会根据你的字段生成映射配置，再运行本地脚本。资料缺页、映射无法确认或两列价格口径不同的情况会留待核实。

## 本地试用

需要 Python 3.10 或更新版本。CSV 和 JSON 只需标准库。读取 Excel `.xlsx` 时安装可选依赖，建议使用 Python 虚拟环境。

```sh
python -m pip install -r catalog-reconciliation/requirements-excel.txt
python catalog-reconciliation/scripts/reconcile.py --inspect --sheet 在售规格 --header-row 3 examples/apparel/store.xlsx
python catalog-reconciliation/scripts/reconcile.py --exports examples/apparel/mapping.json
```

服装样例使用后台 CSV 和店铺 Excel，按货号、颜色和尺码匹配。报告会发现一个规格未展示、一个规格售价有差异且缺原价，以及一个已停用规格仍然可见。另一个规格的零售价能正常对齐。

```sh
python catalog-reconciliation/scripts/reconcile.py --exports examples/retail/mapping.json
python catalog-reconciliation/scripts/reconcile.py examples/menu.json
python -m unittest discover -s tests -v
```

自行车配件样例演示不同系统通过对照表关联编号。餐饮样例演示直接核对标准化快照。所有示例均为虚构数据。脚本读取本地文件，输出 UTF-8 JSON。

- [技能说明](catalog-reconciliation/SKILL.md)
- [导出与映射格式](catalog-reconciliation/references/exports.md)
- [MIT 许可证](LICENSE)
