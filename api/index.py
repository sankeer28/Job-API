"""
Job API — FastAPI + python-jobspy

Local dev:  uvicorn api.index:app --reload --port 8000
Vercel:     vercel.json routes everything here automatically.
"""

import io
import math
import os
import sys
from typing import Any, List, Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from jobspy import scrape_jobs
from pydantic import BaseModel, Field

app = FastAPI(
    title="Job API",
    description="Aggregate job postings from LinkedIn, Indeed, Glassdoor, ZipRecruiter, Google Jobs, Bayt, Naukri and more via python-jobspy.",
    version="2.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Constants ─────────────────────────────────────────────────────────────────

VALID_SITES   = {"linkedin", "indeed", "zip_recruiter", "glassdoor", "google", "bayt", "bdjobs", "naukri"}
VALID_TYPES   = {None, "fulltime", "parttime", "internship", "contract"}
VALID_FORMATS = {"markdown", "html"}

# HTML files live in api/public/ so they're always bundled with the function.
_here      = os.path.dirname(os.path.abspath(__file__))
PUBLIC_DIR = os.path.join(_here, "public")


# ── Pydantic request model (POST body) ────────────────────────────────────────

class JobSearchRequest(BaseModel):
    site_name:                  Optional[List[str]] = Field(None, example=["indeed", "linkedin"])
    search_term:                Optional[str]       = Field(None, example="software engineer")
    google_search_term:         Optional[str]       = None
    location:                   Optional[str]       = Field(None, example="New York, NY")
    distance:                   Optional[int]       = Field(50,   example=50)
    job_type:                   Optional[str]       = Field(None, example="fulltime")
    is_remote:                  Optional[bool]      = None
    results_wanted:             Optional[int]       = Field(15,   example=15)
    hours_old:                  Optional[int]       = None
    easy_apply:                 Optional[bool]      = None
    description_format:         Optional[str]       = Field("markdown", example="markdown")
    offset:                     Optional[int]       = Field(0,    example=0)
    linkedin_fetch_description: Optional[bool]      = False
    linkedin_company_ids:       Optional[List[int]] = None
    country_indeed:             Optional[str]       = Field(None, example="USA")
    enforce_annual_salary:      Optional[bool]      = False
    proxies:                    Optional[List[str]] = None
    user_agent:                 Optional[str]       = None
    output_format:              Optional[str]       = Field("json", example="json")


# ── Helpers ───────────────────────────────────────────────────────────────────

TRUTHY = {"1", "true", "yes"}


def _bool(val) -> Optional[bool]:
    if val is None:
        return None
    if isinstance(val, bool):
        return val
    if isinstance(val, int):
        return bool(val)
    return str(val).lower() in TRUTHY


def _int(val, default=None) -> Optional[int]:
    if val is None or val == "":
        return default
    try:
        return int(val)
    except (ValueError, TypeError):
        return default


def _csv_list(val, cast=str) -> Optional[list]:
    if val is None or val == "":
        return None
    if isinstance(val, list):
        result = []
        for v in val:
            try:
                result.append(cast(v))
            except (ValueError, TypeError):
                pass
        return result or None
    parts = [v.strip() for v in str(val).split(",") if v.strip()]
    if not parts:
        return None
    result = []
    for p in parts:
        try:
            result.append(cast(p))
        except (ValueError, TypeError):
            pass
    return result or None


def df_to_safe_records(df: pd.DataFrame) -> List[dict]:
    records = []
    for _, row in df.iterrows():
        clean: dict = {}
        for key, val in row.items():
            try:
                if val is None:
                    clean[key] = None
                elif isinstance(val, float) and (math.isnan(val) or math.isinf(val)):
                    clean[key] = None
                elif hasattr(val, "isoformat"):
                    clean[key] = val.isoformat()
                elif isinstance(val, (list, dict, bool)):
                    clean[key] = val
                else:
                    try:
                        if pd.isna(val):
                            clean[key] = None
                            continue
                    except (TypeError, ValueError):
                        pass
                    clean[key] = val
            except Exception:
                clean[key] = str(val) if val is not None else None
        records.append(clean)
    return records


def _build_kwargs(params: dict) -> dict:
    """Turn a flat params dict into scrape_jobs() kwargs."""
    site_name = _csv_list(params.get("site_name")) or \
                ["linkedin", "indeed", "zip_recruiter", "glassdoor", "google", "bayt", "bdjobs", "naukri"]

    invalid_sites = [s for s in site_name if s not in VALID_SITES]
    if invalid_sites:
        raise HTTPException(400, f"Unknown site(s): {invalid_sites}. Valid: {sorted(VALID_SITES)}")

    job_type          = params.get("job_type") or None
    description_format = params.get("description_format") or "markdown"

    if job_type and job_type not in VALID_TYPES:
        raise HTTPException(400, f"Invalid job_type '{job_type}'. Valid: fulltime, parttime, internship, contract")
    if description_format not in VALID_FORMATS:
        raise HTTPException(400, f"Invalid description_format '{description_format}'. Valid: markdown, html")

    is_remote  = params.get("is_remote")
    easy_apply = params.get("easy_apply")

    kwargs: dict = {
        "site_name":                  site_name,
        "results_wanted":             _int(params.get("results_wanted"), 15),
        "distance":                   _int(params.get("distance"), 50),
        "description_format":         description_format,
        "offset":                     _int(params.get("offset"), 0),
        "linkedin_fetch_description": _bool(params.get("linkedin_fetch_description")) or False,
        "enforce_annual_salary":      _bool(params.get("enforce_annual_salary")) or False,
        "verbose":                    0,
    }

    if params.get("search_term"):          kwargs["search_term"]          = params["search_term"]
    if params.get("google_search_term"):   kwargs["google_search_term"]   = params["google_search_term"]
    if params.get("location"):             kwargs["location"]             = params["location"]
    if job_type:                           kwargs["job_type"]             = job_type
    if is_remote is not None:              kwargs["is_remote"]            = _bool(is_remote)
    if params.get("hours_old"):            kwargs["hours_old"]            = _int(params["hours_old"])
    if easy_apply is not None:             kwargs["easy_apply"]           = _bool(easy_apply)
    if params.get("country_indeed"):       kwargs["country_indeed"]       = params["country_indeed"]
    if params.get("proxies"):              kwargs["proxies"]              = _csv_list(params["proxies"])
    if params.get("user_agent"):           kwargs["user_agent"]           = params["user_agent"]

    lci = params.get("linkedin_company_ids")
    if lci:
        kwargs["linkedin_company_ids"] = _csv_list(lci, cast=int) if isinstance(lci, str) else lci

    return kwargs, site_name


def _csv_response(df: pd.DataFrame) -> StreamingResponse:
    buf = io.StringIO()
    df.to_csv(buf, index=False)
    buf.seek(0)
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=jobs.csv"},
    )


def _excel_response(df: pd.DataFrame) -> StreamingResponse:
    buf = io.BytesIO()
    df.to_excel(buf, index=False)
    buf.seek(0)
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename=jobs.xlsx"},
    )


async def _run_scrape(params: dict):
    """Core scrape logic shared by GET and POST handlers."""
    output_format = (params.get("output_format") or "json").lower()
    kwargs, site_name = _build_kwargs(params)

    try:
        df = scrape_jobs(**kwargs)
    except Exception as exc:
        raise HTTPException(500, detail={
            "error":      str(exc),
            "error_type": type(exc).__name__,
            "parameters": {k: v for k, v in kwargs.items() if k != "verbose"},
        })

    if output_format == "csv":
        return _csv_response(df if df is not None else pd.DataFrame())
    if output_format == "excel":
        return _excel_response(df if df is not None else pd.DataFrame())

    if df is None or df.empty:
        return {"jobs": [], "count": 0, "sites": site_name,
                "query": {k: v for k, v in kwargs.items() if k != "verbose"}}

    records = df_to_safe_records(df)
    return {"jobs": records, "count": len(records), "sites": site_name,
            "query": {k: v for k, v in kwargs.items() if k != "verbose"}}


# ── Routes ────────────────────────────────────────────────────────────────────

_PAGES = {"index.html", "docs.html", "playground.html"}

@app.get("/", include_in_schema=False)
@app.get("/{page}.html", include_in_schema=False)
async def serve_html(page: str = "index"):
    filename = f"{page}.html"
    if filename not in _PAGES:
        raise HTTPException(404, "Not found")
    path = os.path.join(PUBLIC_DIR, filename)
    if os.path.exists(path):
        return FileResponse(path, media_type="text/html")
    raise HTTPException(500, f"File not found on server: {path}")


@app.get("/api/health", tags=["Meta"])
async def health():
    """API health check."""
    return {"status": "ok", "service": "Job API", "version": "2.0.0"}


@app.get("/api/debug-files", include_in_schema=False)
async def debug_files():
    """Temporary: show what exists on the server."""
    cwd = os.getcwd()
    here = os.path.dirname(os.path.abspath(__file__))
    result = {"cwd": cwd, "here": here, "PUBLIC_DIR": PUBLIC_DIR, "public_exists": os.path.isdir(PUBLIC_DIR)}
    # List top-level items
    try:
        result["cwd_contents"] = os.listdir(cwd)
    except Exception as e:
        result["cwd_contents"] = str(e)
    # List public/ if it exists
    if os.path.isdir(PUBLIC_DIR):
        result["public_contents"] = os.listdir(PUBLIC_DIR)
    # Also check /var/task
    for d in ["/var/task", "/var/task/public", "/var/task/api"]:
        try:
            result[d] = os.listdir(d)
        except Exception as e:
            result[d] = str(e)
    return result


@app.get("/api/sites", tags=["Meta"])
async def sites():
    """List all supported job boards."""
    return {"sites": sorted(VALID_SITES)}


@app.get("/api/jobs", tags=["Jobs"], summary="Scrape jobs (GET — query params)")
async def jobs_get(
    site_name:                  Optional[str]  = Query(None,        description="Comma-separated boards: indeed,linkedin,glassdoor,zip_recruiter,google,bayt,naukri,bdjobs"),
    search_term:                Optional[str]  = Query(None,        example="software engineer"),
    google_search_term:         Optional[str]  = Query(None),
    location:                   Optional[str]  = Query(None,        example="New York, NY"),
    distance:                   Optional[int]  = Query(50),
    job_type:                   Optional[str]  = Query(None,        description="fulltime|parttime|internship|contract"),
    is_remote:                  Optional[str]  = Query(None),
    results_wanted:             Optional[int]  = Query(15),
    hours_old:                  Optional[int]  = Query(None),
    easy_apply:                 Optional[str]  = Query(None),
    description_format:         Optional[str]  = Query("markdown",  description="markdown|html"),
    offset:                     Optional[int]  = Query(0),
    linkedin_fetch_description: Optional[str]  = Query(None),
    linkedin_company_ids:       Optional[str]  = Query(None,        description="Comma-separated IDs, e.g. 1441,2382"),
    country_indeed:             Optional[str]  = Query(None,        example="USA"),
    enforce_annual_salary:      Optional[str]  = Query(None),
    proxies:                    Optional[str]  = Query(None),
    user_agent:                 Optional[str]  = Query(None),
    output_format:              Optional[str]  = Query("json",      description="json|csv|excel"),
):
    params = {k: v for k, v in locals().items() if v is not None}
    return await _run_scrape(params)


@app.post("/api/jobs", tags=["Jobs"], summary="Scrape jobs (POST — JSON body)")
async def jobs_post(body: JobSearchRequest):
    params = {k: v for k, v in body.model_dump(exclude_none=True).items()}
    return await _run_scrape(params)


# ── Dev server ────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("api.index:app", host="0.0.0.0", port=8000, reload=True)

