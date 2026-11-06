import csv
import io
import json
from typing import Any, AsyncIterator, Iterator


# Leading characters that make Excel/LibreOffice/Sheets evaluate a cell as a
# formula. Subscriber names and attributes come from public signup forms, so
# an exported "=HYPERLINK(...)" name would execute when the CSV is opened.
_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def _neutralise_formula(val: Any) -> Any:
    """Prefix a text cell with ``'`` when a spreadsheet would run it.

    Only strings are touched — real numbers (including negatives) are written
    as numbers. This is the OWASP-recommended CSV injection mitigation.
    """
    if isinstance(val, str) and val.startswith(_FORMULA_PREFIXES):
        return "'" + val
    return val


def _flatten_row(row: dict, columns: list[str]) -> dict:
    """Flatten nested dicts/lists to JSON strings so CSV can hold them, and
    neutralise spreadsheet formulas in text cells."""
    flat_row = {}
    for col in columns:
        val = row.get(col, "")
        if isinstance(val, (dict, list)):
            flat_row[col] = json.dumps(val, ensure_ascii=False)
        else:
            flat_row[col] = _neutralise_formula(val)
    return flat_row


def dict_list_to_csv(data: list[dict], columns: list[str]) -> Iterator[str]:
    """Convert an in-memory list of dicts to CSV string chunks.

    Note this still requires the whole dataset in memory; prefer
    ``aiter_dicts_to_csv`` when the rows can be streamed from the API.
    """
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=columns, extrasaction="ignore")
    writer.writeheader()
    yield output.getvalue()
    output.seek(0)
    output.truncate(0)

    for row in data:
        writer.writerow(_flatten_row(row, columns))
        yield output.getvalue()
        output.seek(0)
        output.truncate(0)


async def aiter_dicts_to_csv(rows: AsyncIterator[dict], columns: list[str]) -> AsyncIterator[str]:
    """Stream CSV chunks from an async row iterator.

    Rows are consumed lazily, so peak memory stays proportional to a single
    page rather than the whole export. Each row is flushed as its own chunk,
    which keeps the response streaming instead of buffering the full CSV.
    """
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=columns, extrasaction="ignore")
    writer.writeheader()
    yield output.getvalue()
    output.seek(0)
    output.truncate(0)

    async for row in rows:
        writer.writerow(_flatten_row(row, columns))
        yield output.getvalue()
        output.seek(0)
        output.truncate(0)
