import csv
import io
import json
from typing import Iterator


def dict_list_to_csv(data: list[dict], columns: list[str]) -> Iterator[str]:
    """Convert a list of dicts to CSV string chunks for streaming."""
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=columns, extrasaction="ignore")
    writer.writeheader()
    yield output.getvalue()
    output.seek(0)
    output.truncate(0)

    for row in data:
        # Flatten nested dicts/lists to JSON strings
        flat_row = {}
        for col in columns:
            val = row.get(col, "")
            if isinstance(val, (dict, list)):
                flat_row[col] = json.dumps(val, ensure_ascii=False)
            else:
                flat_row[col] = val
        writer.writerow(flat_row)
        yield output.getvalue()
        output.seek(0)
        output.truncate(0)
