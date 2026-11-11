# 导出与映射格式

## 开始核对

```sh
python scripts/reconcile.py --inspect export.csv
python scripts/reconcile.py --inspect --sheet "Listings" --header-row 3 export.xlsx
python scripts/reconcile.py --inspect --records-path data.products export.json
python scripts/reconcile.py --exports mapping.json
```

命令从技能目录运行。相对文件路径以配置文件所在目录为准。CSV 使用 UTF-8 和逗号分隔，首行为唯一列名。XLSX 使用 `sheet` 和可选的 `header_row`（默认 1）；多工作表时要明确选择。映射使用的公式单元格应先转成值，脚本会提示公式所在行。含前导零的编号在 Excel 或 JSON 中保留为文本。嵌套 JSON 的数组路径通过 `records_path` 指定。

## 最小配置示意

```json
{
  "scope": {"location": "示例店", "channel": "网店", "window": "日常销售", "currency": "CNY"},
  "minor_digits": 2,
  "layers": {
    "catalog": {
      "file": "admin.csv",
      "observed_at": "2026-01-15T12:00:00+08:00",
      "coverage": "complete",
      "identity": "sku",
      "fields": {"id": ["货号", "颜色", "尺码"], "name": "品名", "state": "上架"},
      "states": {"是": "enabled", "否": "disabled"}
    },
    "storefront": {
      "file": "store.xlsx",
      "sheet": "在售规格",
      "observed_at": "2026-01-15T12:03:00+08:00",
      "coverage": "partial",
      "identity": "sku",
      "fields": {"id": ["product", "colour", "size"], "name": "title"}
    }
  }
}
```

- 层可选 `source`（供货方）、`catalog`（后台）和 `storefront`（前台）。至少提供两层。已尝试采集但失败的层只需填时间和 `"coverage": "failed"`。
- `fields.id` 可为单列或同序的规格列数组，`name` 为显示名。单列编号不同时使用 `"identity": "crosswalk"`，根对象加 `"crosswalk": "sku-map.csv"`。对照表列名是 `layer,id,sku`，`id` 为原始编号，`sku` 为已确认的公共编号。
- `state` 可省略，默认 `unknown`。配置后，`states` 将原值映射到后台的 `enabled`、`disabled`、`unknown`，或其他层的 `available`、`unavailable`、`unknown`。布尔值使用 `"true"` 或 `"false"` 作为映射键，空值使用空字符串。缺少上架或供货可售证据时，脚本无法判定前台应出现该规格。
- 可在后台和前台的 `fields` 映射 `sale_price_minor`、`list_price_minor`。默认读取非负整数最小货币单位。普通金额如 `18.50` 可用 `"price_unit": "major"` 和全局的 `minor_digits`，例如人民币填 2。使用点作小数分隔符，去掉货币符号和千分符。两个层都提供相同语义的字段时才比较；其中前台字段空白时可报告缺价。供货报价单独解释。

## 读懂报告

`findings` 给出差异的层、代码和证据。导出模式的 `ref` 指向 CSV 物理行、Excel 工作表与行，或 JSON 路径与数组位置。完整层的缺项记为 `absent`，部分或失败层的缺项记为 `unverified`。这些发现描述导出之间的差异；判断实际漏上架原因需要检查项目规则。重复 SKU 单独报告，该规格的跨层差异暂停判断。未映射编号保留在 `exports.<层>.unmapped` 并降低有效覆盖度。`same_name` 只供人工复核。

已有标准化数据时，可运行 `python scripts/reconcile.py snapshot.json`。输入使用同样的 `scope`，将 `layers` 换为 `snapshots`，每层含 `observed_at`、`coverage` 和 `items`。记录至少含 `sku`、`name`、`state`，可带 `ref`、`sale_price_minor` 和 `list_price_minor`。失败层使用空数组。
