"""Shared report formatting; serialization conventions remain version-specific."""
import csv
import json
from pathlib import Path


def cell(value):
    if value is None:
        return "null"
    if isinstance(value, (dict, list, bool)):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def write_text(path, text):
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(text, encoding="utf-8")
    temporary.replace(path)


def markdown(rows, fields):
    def line(values):
        return "| " + " | ".join(cell(v).replace("|", "\\|").replace("\n", "<br>")
                                  for v in values) + " |"
    return "\n".join([line(fields), line(["---"] * len(fields)),
                       *(line([row.get(key) for key in fields]) for row in rows)])


def csv_file(path, rows, fields=None, *, legacy=False):
    """Preserve legacy null/bool text and atomic writes, or study CSV conventions."""
    fields = fields or list(dict.fromkeys(k for row in rows for k in row)) or ['status']
    path = Path(path)
    target = path.with_suffix(path.suffix + '.tmp') if legacy else path
    formatter = cell if legacy else structured_cell
    with target.open('w', encoding='utf-8', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            values = {key: row.get(key) for key in fields} if legacy else row
            writer.writerow({key: formatter(value) for key, value in values.items()})
    if legacy:
        target.replace(path)


def legacy_csv(path, rows, fields):
    csv_file(path, rows, fields, legacy=True)


def structured_cell(value):
    return json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list)) else value


def append_csv(path, rows):
    """Stream task-sized batches; preserve schema extensions as structured fields."""
    if not rows:
        return
    path = Path(path)
    exists = path.exists()
    if exists:
        with path.open(newline='') as stream:
            fields = next(csv.reader(stream))
    else:
        fields = list(dict.fromkeys(k for row in rows for k in row)) + ['extra_fields']
    with path.open('a', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        if not exists:
            writer.writeheader()
        for row in rows:
            value = {k: row.get(k) for k in fields}
            value['extra_fields'] = {k: v for k, v in row.items() if k not in fields}
            writer.writerow({k: structured_cell(v) for k, v in value.items()})
