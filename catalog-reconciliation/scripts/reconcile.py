#!/usr/bin/env python3
"""Compare two or three catalog snapshots with a shared SKU and scope."""
import argparse
import json
import re
import sys
import unicodedata
from collections import defaultdict
from csv import Error as CSVError
from datetime import datetime
from pathlib import Path

STATES = {
    "source": {"available", "unavailable", "unknown"},
    "catalog": {"enabled", "disabled", "unknown"},
    "storefront": {"available", "unavailable", "unknown"},
}
COVERAGE = ("complete", "partial", "failed")
PRICES = ("sale_price_minor", "list_price_minor")


def object_field(value, label):
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be an object")
    return value


def text_field(value, label):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be non-empty text")
    return value.strip()


def snapshot_index(snapshot, layer):
    snapshot = object_field(snapshot, layer)
    coverage = snapshot.get("coverage")
    if coverage not in COVERAGE:
        raise ValueError(f"{layer}.coverage must be complete, partial, or failed")
    observed = text_field(snapshot.get("observed_at"), f"{layer}.observed_at")
    try:
        timestamp = datetime.fromisoformat(observed.replace("Z", "+00:00"))
        if timestamp.utcoffset() is None:
            raise ValueError
    except ValueError:
        raise ValueError(f"{layer}.observed_at must be ISO 8601 with a time zone") from None

    rows = snapshot.get("items")
    if not isinstance(rows, list) or (coverage == "failed" and rows):
        raise ValueError(f"{layer}.items must be an array and must be empty for failed coverage")
    index = defaultdict(list)
    for number, raw in enumerate(rows, 1):
        label = f"{layer}.items row {number}"
        row = object_field(raw, label)
        sku = text_field(row.get("sku"), f"{label}.sku")
        if sku != row["sku"]:
            raise ValueError(f"{label}.sku must not have surrounding whitespace")
        state = text_field(row.get("state"), f"{label}.state")
        if state not in STATES[layer]:
            raise ValueError(f"{label}.state is invalid")
        record = {"row": number, "name": text_field(row.get("name"), f"{label}.name"), "state": state}
        if row.get("ref") is not None:
            record["ref"] = text_field(row["ref"], f"{label}.ref")
        for price in PRICES:
            amount = row.get(price)
            if amount is not None and (type(amount) is not int or amount < 0):
                raise ValueError(f"{label}.{price} must be null or a non-negative integer")
            if price in row:
                record[price] = amount
        index[sku].append(record)
    return index, {"coverage": coverage, "observed_at": observed, "rows": len(rows), "distinct_skus": len(index)}


def name_collisions(index):
    names = defaultdict(set)
    for sku, records in index.items():
        for record in records:
            normalized = unicodedata.normalize("NFKC", record["name"]).casefold()
            names[" ".join(normalized.split())].add(sku)
    return [{"name": name, "skus": sorted(skus)} for name, skus in sorted(names.items()) if len(skus) > 1]


def sku_findings(records, summaries):
    duplicates = [(layer, "duplicate_sku") for layer, matches in records.items() if len(matches) > 1]
    if duplicates:
        return duplicates

    source, catalog, storefront = (next(iter(records.get(layer, [])), {}) for layer in STATES)
    differences = []

    def absent(layer):
        code = "absent" if summaries[layer]["coverage"] == "complete" else "unverified"
        differences.append((layer, code))

    if "source" in records and not source and (catalog.get("state") == "enabled" or storefront):
        absent("source")
    if "catalog" in records and not catalog and (source.get("state") == "available" or storefront):
        absent("catalog")
    source_ready = "source" not in records or source.get("state") == "available"
    catalog_ready = "catalog" not in records or catalog.get("state") == "enabled"
    if "storefront" in records and not storefront and source_ready and catalog_ready:
        absent("storefront")
    if storefront and catalog.get("state") == "disabled":
        differences.append(("storefront", "disabled_sku_visible"))
    if storefront.get("state") == "available" and source.get("state") == "unavailable":
        differences.append(("storefront", "availability_mismatch"))
    if catalog and storefront:
        for field in PRICES:
            expected = catalog.get(field)
            if expected is None or field not in storefront:
                continue
            observed = storefront[field]
            if observed is None:
                differences.append(("storefront", f"{field}_missing"))
            elif expected != observed:
                differences.append(("storefront", f"{field}_mismatch"))
    return differences


def reconcile(document):
    document = object_field(document, "input")
    raw_scope = object_field(document.get("scope"), "scope")
    scope = {field: text_field(raw_scope.get(field), f"scope.{field}")
             for field in ("location", "channel", "window", "currency")}
    if not re.fullmatch(r"[A-Z]{3}", scope["currency"]):
        raise ValueError("scope.currency must be a three-letter uppercase code")
    snapshots = object_field(document.get("snapshots"), "snapshots")
    if len(snapshots) < 2 or set(snapshots) - set(STATES):
        raise ValueError("use two or three snapshots from source, catalog, storefront")
    indexes, summaries = {}, {}
    for layer in STATES:
        if layer in snapshots:
            indexes[layer], summaries[layer] = snapshot_index(snapshots[layer], layer)
    findings = []
    for sku in sorted(set().union(*indexes.values())):
        records = {layer: index.get(sku, []) for layer, index in indexes.items()}
        differences = sku_findings(records, summaries)
        if differences:
            findings.append({"sku": sku, "issues": [{"layer": layer, "code": code} for layer, code in differences],
                             "evidence": records})
    return {"scope": scope, "snapshots": summaries,
            "same_name": {layer: name_collisions(index) for layer, index in indexes.items()},
            "findings": findings}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--exports", action="store_true", help="load an export mapping JSON")
    mode.add_argument("--inspect", action="store_true", help="preview up to three rows of an export")
    parser.add_argument("--sheet", help="worksheet for --inspect")
    parser.add_argument("--header-row", type=int, help="XLSX header row for --inspect")
    parser.add_argument("--records-path", help="JSON array path for --inspect")
    parser.add_argument("input", type=Path, help="snapshot JSON, export mapping, or export to inspect")
    args = parser.parse_args()
    if not args.inspect and any(value is not None for value in (args.sheet, args.header_row, args.records_path)):
        parser.error("--sheet, --header-row, and --records-path require --inspect")
    try:
        if args.inspect:
            from catalog_exports import inspect_export
            report = inspect_export(args.input, sheet=args.sheet, header_row=args.header_row,
                                    records_path=args.records_path)
        else:
            with args.input.open(encoding="utf-8-sig") as stream:
                document = json.load(stream)
            if args.exports:
                from catalog_exports import prepare
                document, exports = prepare(document, args.input.parent)
            report = reconcile(document)
            if args.exports:
                report["exports"] = exports
    except (OSError, ValueError, CSVError) as exc:
        parser.exit(2, f"reconcile: {exc}\n")
    sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
