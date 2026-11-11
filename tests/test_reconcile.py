import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "catalog-reconciliation/scripts/reconcile.py"
EXAMPLES = ROOT / "examples"


def run_cli(*args):
    return subprocess.run([sys.executable, "-X", "utf8", str(SCRIPT), *map(str, args)],
                          capture_output=True, encoding="utf-8", check=False)


def issues(report):
    return {finding["sku"]: {(issue["layer"], issue["code"]) for issue in finding["issues"]}
            for finding in report["findings"]}


def composite(*parts):
    return json.dumps(parts, ensure_ascii=False, separators=(",", ":"))


class ReconcileTests(unittest.TestCase):
    def parse(self, *args):
        process = run_cli(*args)
        self.assertEqual(process.returncode, 0, process.stderr)
        return json.loads(process.stdout)

    def with_mapping(self, example, mutate):
        path = EXAMPLES / example / "mapping.json"
        mapping = json.loads(path.read_text(encoding="utf-8"))
        if "crosswalk" in mapping:
            mapping["crosswalk"] = str(path.parent / mapping["crosswalk"])
        for layer in mapping["layers"].values():
            if "file" in layer:
                layer["file"] = str(path.parent / layer["file"])
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / "mapping.json"
            mutate(mapping, Path(temp))
            output.write_text(json.dumps(mapping, ensure_ascii=False), encoding="utf-8")
            return self.parse("--exports", output)

    def test_restaurant_snapshot(self):
        report = self.parse(EXAMPLES / "menu.json")
        self.assertEqual(report["same_name"]["source"][0]["skus"], ["tea-l", "tea-s"])
        self.assertEqual(issues(report), {
            "tea-l": {("storefront", "absent")},
            "tea-s": {("storefront", "sale_price_minor_mismatch"), ("storefront", "list_price_minor_missing")},
            "bun": {("storefront", "disabled_sku_visible"), ("storefront", "availability_mismatch")},
        })

    def test_retail_crosswalk(self):
        report = self.parse("--exports", EXAMPLES / "retail/mapping.json")
        self.assertEqual(issues(report)["BOTTLE-750"], {("storefront", "absent")})
        self.assertEqual(issues(report)["BOTTLE-500"], {
            ("storefront", "sale_price_minor_mismatch"), ("storefront", "list_price_minor_missing"),
        })
        bottle = next(row for row in report["findings"] if row["sku"] == "BOTTLE-500")
        self.assertTrue(bottle["evidence"]["catalog"][0]["ref"].startswith("inventory.json:data.variants record "))

    def test_apparel_two_exports_with_excel(self):
        report = self.parse("--exports", EXAMPLES / "apparel/mapping.json")
        self.assertEqual(issues(report), {
            composite("001", "黑", "M"): {("storefront", "absent")},
            composite("001", "黑", "L"): {
                ("storefront", "sale_price_minor_mismatch"), ("storefront", "list_price_minor_missing"),
            },
            composite("001", "白", "M"): {("storefront", "disabled_sku_visible")},
        })
        variant = next(row for row in report["findings"] if row["sku"] == composite("001", "黑", "L"))
        self.assertEqual(variant["evidence"]["catalog"][0]["sale_price_minor"], 6990)
        self.assertEqual(variant["evidence"]["storefront"][0]["sale_price_minor"], 6490)
        self.assertIn("store.xlsx!在售规格:row 6", variant["evidence"]["storefront"][0]["ref"])
        self.assertEqual(report["snapshots"]["storefront"]["distinct_skus"], 3)

    def test_partial_storefront_is_uncertain(self):
        def mutate(mapping, _):
            mapping["layers"]["storefront"]["coverage"] = "partial"

        report = self.with_mapping("apparel", mutate)
        self.assertEqual(issues(report)[composite("001", "黑", "M")], {("storefront", "unverified")})

    def test_missing_crosswalk_reduces_coverage(self):
        def mutate(mapping, temp):
            path = temp / "crosswalk.csv"
            original = Path(mapping["crosswalk"]).read_text(encoding="utf-8")
            lines = [line for line in original.splitlines()
                     if not (line.startswith("storefront,") and line.endswith(",BOTTLE-500"))]
            path.write_text("\n".join(lines), encoding="utf-8")
            mapping["crosswalk"] = str(path)

        report = self.with_mapping("retail", mutate)
        self.assertEqual(report["snapshots"]["storefront"]["coverage"], "partial")
        self.assertEqual(issues(report)["BOTTLE-500"], {("storefront", "unverified")})
        self.assertIn("shop.csv:line ", report["exports"]["storefront"]["unmapped"][0]["ref"])

    def test_duplicate_sku_pauses_conclusions(self):
        source = EXAMPLES / "menu.json"
        document = json.loads(source.read_text(encoding="utf-8"))
        records = document["snapshots"]["catalog"]["items"]
        records.append(records[0].copy())
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "duplicate.json"
            path.write_text(json.dumps(document), encoding="utf-8")
            self.assertEqual(issues(self.parse(path))["tea-s"], {("catalog", "duplicate_sku")})

    def test_inspect_preserves_text_identifiers_and_physical_rows(self):
        xlsx = EXAMPLES / "apparel/store.xlsx"
        missing_sheet = run_cli("--inspect", xlsx)
        self.assertEqual(missing_sheet.returncode, 2)
        self.assertIn("在售规格", missing_sheet.stderr)
        preview = self.parse("--inspect", "--sheet", "在售规格", "--header-row", 3, xlsx)
        self.assertEqual([row["fields"]["product"] for row in preview["sample"]], ["002", "001", "001"])
        self.assertIn(":row 6", preview["sample"][1]["ref"])
        self.assertEqual(preview["sample"][0]["fields"]["sale_price"], 0)

    def test_formula_price_requires_a_value(self):
        from openpyxl import Workbook

        path = EXAMPLES / "apparel/mapping.json"
        mapping = json.loads(path.read_text(encoding="utf-8"))
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            workbook = Workbook()
            sheet = workbook.active
            sheet.title = "在售规格"
            sheet.append(["Example"])
            sheet.append([None])
            sheet.append(["product", "colour", "size", "title", "availability", "sale_price", "compare_at"])
            sheet.append(["001", "黑", "L", "Shirt", "in_stock", "=4+5", None])
            workbook.save(base / "store.xlsx")
            workbook.close()
            mapping["layers"]["catalog"]["file"] = str(path.parent / "catalog.csv")
            config = base / "mapping.json"
            config.write_text(json.dumps(mapping), encoding="utf-8")
            process = run_cli("--exports", config)
            self.assertEqual(process.returncode, 2)
            self.assertIn("row 4: sale_price contains a formula", process.stderr)

    def test_unmapped_price_column_does_not_imply_missing_price(self):
        def mutate(mapping, _):
            del mapping["layers"]["storefront"]["fields"]["list_price_minor"]

        report = self.with_mapping("apparel", mutate)
        self.assertEqual(issues(report)[composite("001", "黑", "L")],
                         {("storefront", "sale_price_minor_mismatch")})


if __name__ == "__main__":
    unittest.main()
