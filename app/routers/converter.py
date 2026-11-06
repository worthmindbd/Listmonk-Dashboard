from fastapi import APIRouter, UploadFile, File, Form, HTTPException
from fastapi.responses import StreamingResponse
from typing import Optional
from app.services.csv_converter import detect_columns, convert_csv
from app.services.listmonk_client import listmonk
import json
import io

router = APIRouter()

MAX_UPLOAD_BYTES = 50 * 1024 * 1024  # 50 MB

# ListMonk's importer accepts exactly these modes; anything else either
# produces a cryptic upstream 4xx or silently different behaviour. (The list
# previously held "subscribe-existing"/"unsubscribed", which ListMonk does not
# accept, while rejecting the UI's "blocklist" option outright.)
VALID_IMPORT_MODES = {"subscribe", "blocklist"}


def _parse_attribute_columns(attribute_columns: Optional[str]) -> list[str]:
    """Parse and validate the attribute_columns form field."""
    if not attribute_columns:
        return []
    try:
        attrs = json.loads(attribute_columns)
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid JSON in attribute_columns")
    if not isinstance(attrs, list) or not all(isinstance(a, str) for a in attrs):
        raise HTTPException(
            status_code=400,
            detail="attribute_columns must be a JSON array of column-name strings",
        )
    return attrs


def _parse_list_ids(list_ids: str) -> list[int]:
    """Parse and validate the list_ids form field."""
    try:
        parsed = json.loads(list_ids)
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid JSON in list_ids")
    if (
        not isinstance(parsed, list)
        or not parsed
        or not all(isinstance(i, int) and not isinstance(i, bool) for i in parsed)
    ):
        raise HTTPException(
            status_code=400,
            detail="list_ids must be a JSON array of integer list IDs (e.g. [1,2])",
        )
    return parsed


def _parse_mode(mode: str) -> str:
    if mode not in VALID_IMPORT_MODES:
        raise HTTPException(
            status_code=400,
            detail=f"mode must be one of: {', '.join(sorted(VALID_IMPORT_MODES))}",
        )
    return mode


async def _read_upload(file: UploadFile) -> bytes:
    """Read an uploaded file, rejecting oversized payloads before buffering."""
    if file.size is not None and file.size > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"File too large (max {MAX_UPLOAD_BYTES // (1024 * 1024)} MB)",
        )
    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="Empty file uploaded")
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"File too large (max {MAX_UPLOAD_BYTES // (1024 * 1024)} MB)",
        )
    return content


@router.post("/detect-columns")
async def detect_csv_columns(file: UploadFile = File(...)):
    """Upload a CSV and detect its columns + preview rows."""
    content = await _read_upload(file)
    result = detect_columns(content)
    return result


@router.post("/convert")
async def convert_csv_file(
    file: UploadFile = File(...),
    email_column: str = Form(...),
    name_column: Optional[str] = Form(None),
    attribute_columns: Optional[str] = Form(None),
):
    """Convert a CSV to ListMonk format and return as download."""
    content = await _read_upload(file)
    attrs = _parse_attribute_columns(attribute_columns)

    result = convert_csv(content, email_column, name_column, attrs)

    if "error" in result.get("stats", {}):
        raise HTTPException(status_code=400, detail=result["stats"]["error"])

    return StreamingResponse(
        io.StringIO(result["csv_content"]),
        media_type="text/csv",
        headers={
            "Content-Disposition": "attachment; filename=listmonk_import.csv",
            "X-Conversion-Stats": json.dumps(result["stats"]),
        },
    )


@router.post("/convert-and-import")
async def convert_and_import(
    file: UploadFile = File(...),
    email_column: str = Form(...),
    name_column: Optional[str] = Form(None),
    attribute_columns: Optional[str] = Form(None),
    list_ids: str = Form(...),
    mode: str = Form("subscribe"),
    overwrite: bool = Form(False),
):
    """Convert CSV to ListMonk format and immediately import to ListMonk.

    ``overwrite`` is opt-in: when true ListMonk REPLACES the name and the whole
    attribute set of subscribers that already exist, so a CSV carrying fewer
    columns silently wipes their other attributes. It used to be hard-coded on.
    """
    content = await _read_upload(file)
    attrs = _parse_attribute_columns(attribute_columns)
    parsed_list_ids = _parse_list_ids(list_ids)
    import_mode = _parse_mode(mode)

    result = convert_csv(content, email_column, name_column, attrs)

    if "error" in result.get("stats", {}):
        raise HTTPException(status_code=400, detail=result["stats"]["error"])

    import_params = {
        "mode": import_mode,
        "delim": ",",
        "lists": parsed_list_ids,
        "overwrite": overwrite,
    }
    import_result = await listmonk.import_subscribers(
        result["csv_content"].encode("utf-8"),
        "listmonk_import.csv",
        import_params,
    )
    return {
        "conversion_stats": result["stats"],
        "import_result": import_result,
    }
