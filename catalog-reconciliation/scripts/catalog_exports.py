"""Read user exports using explicit field, state, and identity mappings."""
import csv
import json
import re
from itertools import islice
from pathlib import Path
from zipfile import BadZipFile

LAYERS = ("source", "catalog", "storefront")
PRICES = ("sale_price_minor", "list_price_minor")
FORMULA = object()


def field(row, path, label):
    if not isinstance(path, str) or not path:
        raise ValueError(f"{label}: field paths must be non-empty strings")
    if isinstance(row, dict) and path in row:
        value = row[path]
    else:
        value = row
        for part in path.split("."):
            if not isinstance(value, dict) or part not in value:
                raise ValueError(f"{label}: missing field {path}")
            value = value[part]
    if value is FORMULA:
        raise ValueError(f"{label}: {path} contains a formula; export it as a value")
    return value


def validate_headers(headers, label):
    if not headers or any(not isinstance(name, str) or not name.strip() for name in headers):
        raise ValueError(f"{label}: expected non-empty column headers")
    if len(headers) != len(set(headers)):
        raise ValueError(f"{label}: column headers are repeated")


def csv_rows(path):
    with path.open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.reader(stream, strict=True)
        headers = next(reader, [])
        validate_headers(headers, path.name)
        while True:
            line = reader.line_num + 1
            row = next(reader, None)
            if row is None:
                break
            if not row:
                continue
            label = f"{path.name}:line {line}"
            if len(row) != len(headers):
                raise ValueError(f"{label}: column count differs from header")
            yield label, dict(zip(headers, row))


def xlsx_rows(path, sheet_name, header_row):
    try:
        from openpyxl import load_workbook
        from openpyxl.utils.exceptions import InvalidFileException
    except ImportError as exc:
        raise ValueError("Excel support requires openpyxl; see requirements-excel.txt") from exc

    try:
        workbook = load_workbook(path, read_only=True, data_only=False, keep_links=False)
    except (BadZipFile, InvalidFileException, KeyError) as exc:
        raise ValueError(f"{path.name}: could not open the XLSX workbook") from exc
    try:
        sheets = [sheet.title for sheet in workbook.worksheets]
        if sheet_name is None and len(sheets) == 1:
            sheet_name = sheets[0]
        if sheet_name not in sheets:
            raise ValueError(f"{path.name}: select a worksheet from {', '.join(sheets)}")
        sheet = workbook[sheet_name]
        sheet.reset_dimensions()
        rows = sheet.iter_rows(min_row=header_row)
        headers = [cell.value for cell in next(rows, ())]
        while headers and headers[-1] is None:
            headers.pop()
        label = f"{path.name}!{sheet_name}"
        validate_headers(headers, label)
        for number, cells in enumerate(rows, header_row + 1):
            values = [FORMULA if cell.data_type == "f" else cell.value for cell in cells]
            if all(value is None for value in values):
                continue
            row_label = f"{label}:row {number}"
            if any(value is not None for value in values[len(headers):]):
                raise ValueError(f"{row_label}: data found under a blank header")
            values.extend([None] * (len(headers) - len(values)))
            yield row_label, dict(zip(headers, values))
    finally:
        workbook.close()


def export_rows(path, *, records_path=None, sheet=None, header_row=None):
    suffix = path.suffix.lower()
    if records_path is not None and suffix != ".json":
        raise ValueError(f"{path.name}: records_path is for JSON only")
    if (sheet is not None or header_row is not None) and suffix != ".xlsx":
        raise ValueError(f"{path.name}: sheet and header_row are for XLSX only")
    if suffix == ".csv":
        yield from csv_rows(path)
    elif suffix == ".xlsx":
        if header_row is not None and (type(header_row) is not int or not 1 <= header_row <= 1048576):
            raise ValueError(f"{path.name}: header_row must be between 1 and 1048576")
        yield from xlsx_rows(path, sheet, header_row or 1)
    elif suffix == ".json":
        with path.open(encoding="utf-8-sig") as stream:
            records = json.load(stream, parse_float=str)
        if records_path is not None:
            records = field(records, records_path, path.name)
        if not isinstance(records, list):
            raise ValueError(f"{path.name}: expected an array of objects")
        for ordinal, row in enumerate(records, 1):
            label = f"{path.name}:{records_path or '$'} record {ordinal}"
            if not isinstance(row, dict):
                raise ValueError(f"{label}: expected an object")
            yield label, row
    else:
        raise ValueError(f"{path.name}: use CSV, XLSX, or JSON")


def inspect_export(path, **options):
    records = export_rows(path, **options)
    try:
        return {"file": path.name, "sample": [
            {"ref": label, "fields": {
                name: "<formula>" if value is FORMULA else value
                for name, value in row.items()
            }}
            for label, row in islice(records, 3)
        ]}
    finally:
        records.close()


def key(value, label):
    if type(value) is int:
        return str(value)
    if not isinstance(value, str) or not value or value.strip() != value:
        raise ValueError(f"{label}: expected a stable ID without surrounding whitespace")
    return value


def row_id(row, fields, label):
    if isinstance(fields, list):
        if len(fields) < 2 or any(not isinstance(path, str) for path in fields):
            raise ValueError(f"{label}: composite IDs require two or more field paths")
        parts = [key(field(row, path, label), label) for path in fields]
        return json.dumps(parts, ensure_ascii=False, separators=(",", ":"))
    return key(field(row, fields, label), label)


def price_minor(value, precision, label):
    if value is None or value == "":
        return None
    if type(value) is float and precision is None and value.is_integer():
        value = int(value)
    match = re.fullmatch(r"([0-9]+)(?:\.([0-9]+))?", str(value))
    if not match or (precision is None and match[2]):
        raise ValueError(f"{label}: expected a non-negative price in the configured unit")
    if precision is None:
        return int(match[1])
    fraction = (match[2] or "").rstrip("0")
    if len(fraction) > precision:
        raise ValueError(f"{label}: price has more than {precision} fractional digits")
    return int(match[1]) * 10 ** precision + int(fraction.ljust(precision, "0") or "0")


def read_crosswalk(base, filename):
    if filename is None:
        return {}
    if not isinstance(filename, str) or Path(filename).suffix.lower() != ".csv":
        raise ValueError("crosswalk must be a CSV filename")
    index = {}
    for label, row in csv_rows(base / filename):
        layer = field(row, "layer", label)
        if layer not in LAYERS:
            raise ValueError(f"{label}: invalid layer")
        pair = (layer, key(field(row, "id", label), label))
        sku = key(field(row, "sku", label), label)
        if pair in index and index[pair] != sku:
            raise ValueError(f"{label}: local ID maps to more than one SKU")
        index[pair] = sku
    return index


def prepare_layer(base, layer, config, crosswalk, minor_digits):
    if not isinstance(config, dict):
        raise ValueError(f"layers.{layer} must be an object")
    coverage = config.get("coverage")
    if coverage not in ("complete", "partial", "failed"):
        raise ValueError(f"{layer}: coverage must be complete, partial, or failed")
    result = {"observed_at": config.get("observed_at"), "coverage": coverage, "items": []}
    unmapped = []
    if coverage == "failed":
        return result, unmapped
    filename = config.get("file")
    fields, states = config.get("fields"), config.get("states", {})
    identity = config.get("identity")
    if not isinstance(filename, str) or not filename:
        raise ValueError(f"{layer}: specify the export file")
    if not isinstance(fields, dict) or not {"id", "name"} <= fields.keys():
        raise ValueError(f"{layer}: provide id and name fields")
    if not isinstance(states, dict) or ("state" in fields and not states):
        raise ValueError(f"{layer}: provide a state map for the mapped state field")
    if identity not in ("sku", "crosswalk"):
        raise ValueError(f"{layer}: identity must be sku or crosswalk")
    if identity == "crosswalk" and isinstance(fields["id"], list):
        raise ValueError(f"{layer}: crosswalk mode requires a single local ID field")
    unit = config.get("price_unit", "minor")
    if unit not in ("minor", "major") or (unit == "major" and minor_digits is None):
        raise ValueError(f"{layer}: major prices require minor_digits; otherwise use minor")

    path = base / filename
    options = {name: config.get(name) for name in ("records_path", "sheet", "header_row")}
    for label, row in export_rows(path, **options):
        local_id = row_id(row, fields["id"], label)
        sku = local_id if identity == "sku" else crosswalk.get((layer, local_id))
        if sku is None:
            unmapped.append({"ref": label, "id": local_id})
            continue
        state = "unknown"
        if "state" in fields:
            raw_state = field(row, fields["state"], label)
            value = "" if raw_state is None else str(raw_state)
            if isinstance(raw_state, bool):
                value = value.lower()
            if value not in states:
                raise ValueError(f"{label}: state has no mapping")
            state = states[value]
        item = {"sku": sku, "name": field(row, fields["name"], label), "state": state,
                "ref": f"{label} ({local_id})"}
        for price in PRICES:
            if price in fields:
                item[price] = price_minor(field(row, fields[price], label),
                                          minor_digits if unit == "major" else None, f"{label}.{price}")
        result["items"].append(item)
    if unmapped:
        result["coverage"] = "partial"
    return result, unmapped


def prepare(mapping, base: Path):
    if not isinstance(mapping, dict) or not isinstance(mapping.get("layers"), dict):
        raise ValueError("the export mapping must contain a layers object")
    layers = mapping["layers"]
    if len(layers) < 2 or set(layers) - set(LAYERS):
        raise ValueError("use two or three layers from source, catalog, storefront")
    digits = mapping.get("minor_digits")
    if digits is not None and (type(digits) is not int or not 0 <= digits <= 6):
        raise ValueError("minor_digits must be an integer between 0 and 6")
    if not mapping.get("crosswalk") and any(
        isinstance(config, dict) and config.get("identity") == "crosswalk"
        and config.get("coverage") != "failed" for config in layers.values()
    ):
        raise ValueError("crosswalk identity requires a crosswalk CSV")
    crosswalk = read_crosswalk(base, mapping.get("crosswalk"))
    snapshots, evidence = {}, {}
    for layer in LAYERS:
        if layer not in layers:
            continue
        config = layers[layer]
        snapshot, unmapped = prepare_layer(base, layer, config, crosswalk, digits)
        snapshots[layer] = snapshot
        evidence[layer] = {"file": Path(config["file"]).name if config["coverage"] != "failed" else None,
                           "declared_coverage": config["coverage"], "unmapped": unmapped}
    return {"scope": mapping.get("scope"), "snapshots": snapshots}, evidence
