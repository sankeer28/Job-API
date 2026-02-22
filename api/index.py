"""
Job API — FastAPI + python-jobspy

Local dev:  uvicorn api.index:app --reload --port 8000
Vercel:     vercel.json routes everything here automatically.
"""

import datetime
import html as _html
import io
import math
import os
import sys
import time
from typing import Any, List, Optional

import dateutil.parser as _dp

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import httpx
import pandas as pd
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
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

JOBSPY_SITES  = {"linkedin", "indeed", "zip_recruiter", "glassdoor", "google", "bayt", "bdjobs", "naukri"}
CUSTOM_SITES  = {"remoteok", "arbeitnow", "remotive", "jobicy"}
VALID_SITES   = JOBSPY_SITES | CUSTOM_SITES
VALID_TYPES   = {None, "fulltime", "parttime", "internship", "contract"}
VALID_FORMATS = {"markdown", "html"}

# HTML files embedded as Python strings — guaranteed to be bundled.
try:
    from api.templates import INDEX_HTML, DOCS_HTML, PLAYGROUND_HTML
except ImportError:
    from templates import INDEX_HTML, DOCS_HTML, PLAYGROUND_HTML

_PAGES = {
    "index": INDEX_HTML,
    "docs": DOCS_HTML,
    "playground": PLAYGROUND_HTML,
}


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
    site_name = _csv_list(params.get("site_name")) or sorted(JOBSPY_SITES)

    invalid_sites = [s for s in site_name if s not in JOBSPY_SITES]
    if invalid_sites:
        raise HTTPException(400, f"Unknown site(s): {invalid_sites}. Valid: {sorted(JOBSPY_SITES)}")

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


async def scrape_remoteok(params: dict) -> List[dict]:
    """Fetch jobs from the RemoteOK public API (https://remoteok.com/api)."""
    is_remote = _bool(params.get("is_remote"))
    if is_remote is False:
        return []  # RemoteOK only has remote jobs
    search_term   = params.get("search_term", "") or ""
    results_wanted = _int(params.get("results_wanted"), 15)
    hours_old     = _int(params.get("hours_old"))
    offset        = _int(params.get("offset"), 0)

    url = "https://remoteok.com/api"
    if search_term:
        url = f"https://remoteok.com/api?tag={search_term.replace(' ', '+')}"

    headers = {"User-Agent": "Mozilla/5.0 (compatible; Job-API/2.0)"}
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.get(url, headers=headers)
        resp.raise_for_status()
        data = resp.json()

    # First element is a legal-notice object — skip anything without "position"
    jobs_raw = [item for item in data if "position" in item]

    if hours_old:
        cutoff = time.time() - hours_old * 3600
        jobs_raw = [j for j in jobs_raw if j.get("epoch", 0) >= cutoff]

    jobs_raw = jobs_raw[offset: offset + results_wanted]

    results = []
    for j in jobs_raw:
        date_posted = None
        if j.get("date"):
            try:
                date_posted = j["date"][:10]
            except Exception:
                pass

        loc_str = j.get("location") or ""
        salary = None
        if j.get("salary_min") or j.get("salary_max"):
            salary = {
                "min_amount": j.get("salary_min") or None,
                "max_amount": j.get("salary_max") or None,
                "interval": "yearly",
                "currency": "USD",
            }

        results.append({
            "title":            j.get("position") or None,
            "company":          j.get("company") or None,
            "site":             "remoteok",
            "job_url":          j.get("url") or j.get("apply_url") or None,
            "location":         {"city": loc_str or None, "state": None, "country": None},
            "is_remote":        True,
            "job_type":         None,
            "job_level":        None,
            "date_posted":      date_posted,
            "salary":           salary,
            "description":      j.get("description") or None,
            "emails":           None,
            "skills":           j.get("tags") or [],
            "company_url":      None,
            "company_industry": None,
        })
    return results


async def scrape_arbeitnow(params: dict) -> List[dict]:
    """Fetch jobs from the Arbeitnow public API (https://arbeitnow.com/api/job-board-api)."""
    search_term    = params.get("search_term", "") or ""
    results_wanted = _int(params.get("results_wanted"), 15)
    hours_old      = _int(params.get("hours_old"))
    is_remote      = _bool(params.get("is_remote"))
    job_type       = params.get("job_type")
    offset         = _int(params.get("offset"), 0)

    query_params: dict = {}
    if search_term:       query_params["search"] = search_term
    if is_remote is True: query_params["remote"] = "true"

    headers = {
        "User-Agent": "Mozilla/5.0 (compatible; Job-API/2.0)",
        "Accept":     "application/json",
    }

    jobs_raw: List[dict] = []
    page = 1
    async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
        while len(jobs_raw) < offset + results_wanted:
            resp = await client.get(
                "https://www.arbeitnow.com/api/job-board-api",
                params={**query_params, "page": page},
                headers=headers,
            )
            resp.raise_for_status()
            data       = resp.json()
            page_jobs  = data.get("data", [])
            if not page_jobs:
                break
            jobs_raw.extend(page_jobs)
            if not data.get("links", {}).get("next"):
                break
            page += 1
            if page > 5:
                break

    if hours_old:
        cutoff = time.time() - hours_old * 3600
        jobs_raw = [j for j in jobs_raw if j.get("created_at", 0) >= cutoff]

    if is_remote is False:
        jobs_raw = [j for j in jobs_raw if not j.get("remote", False)]
    elif is_remote is True:
        jobs_raw = [j for j in jobs_raw if j.get("remote", False)]

    if job_type:
        type_map = {"fulltime": "full-time", "parttime": "part-time",
                    "contract": "contract",  "internship": "intern"}
        tf = type_map.get(job_type, job_type).lower()
        jobs_raw = [j for j in jobs_raw
                    if any(tf in (jt or "").lower() for jt in j.get("job_types", []))]

    jobs_raw = jobs_raw[offset: offset + results_wanted]

    results = []
    for j in jobs_raw:
        date_posted = None
        if j.get("created_at"):
            try:
                date_posted = datetime.date.fromtimestamp(j["created_at"]).isoformat()
            except Exception:
                pass

        jt = None
        jts = " ".join(j.get("job_types", [])).lower()
        if "full" in jts:
            jt = "fulltime"
        elif "part" in jts:
            jt = "parttime"
        elif "intern" in jts:
            jt = "internship"
        elif "contract" in jts or "freelance" in jts:
            jt = "contract"

        results.append({
            "title":            j.get("title") or None,
            "company":          j.get("company_name") or None,
            "site":             "arbeitnow",
            "job_url":          j.get("url") or None,
            "location":         {"city": j.get("location") or None, "state": None, "country": None},
            "is_remote":        j.get("remote", False),
            "job_type":         jt,
            "job_level":        None,
            "date_posted":      date_posted,
            "salary":           None,
            "description":      j.get("description") or None,
            "emails":           None,
            "skills":           j.get("tags") or [],
            "company_url":      None,
            "company_industry": None,
        })
    return results


async def scrape_remotive(params: dict) -> List[dict]:
    """Fetch jobs from Remotive public API (https://remotive.com/api/remote-jobs)."""
    is_remote = _bool(params.get("is_remote"))
    if is_remote is False:
        return []  # Remotive is remote-only

    search_term    = params.get("search_term", "") or ""
    results_wanted = _int(params.get("results_wanted"), 15)
    hours_old      = _int(params.get("hours_old"))
    offset         = _int(params.get("offset"), 0)

    query: dict = {"limit": offset + results_wanted}
    if search_term:
        query["search"] = search_term

    async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
        resp = await client.get(
            "https://remotive.com/api/remote-jobs",
            params=query,
            headers={"Accept": "application/json"},
        )
        resp.raise_for_status()
        data = resp.json()

    jobs_raw = data.get("jobs", [])

    # Remotive has no API-level location param — post-filter on candidate_required_location
    country  = (params.get("country_indeed") or "").strip()
    location = (params.get("location")       or "").strip()
    loc_filter = (country or location).lower()
    if loc_filter:
        jobs_raw = [
            j for j in jobs_raw
            if loc_filter in (j.get("candidate_required_location") or "").lower()
            or "worldwide" in (j.get("candidate_required_location") or "").lower()
            or "anywhere"  in (j.get("candidate_required_location") or "").lower()
        ]

    if hours_old:
        cutoff = time.time() - hours_old * 3600
        def _pub_ts(j):
            try:
                return _dp.parse(j.get("publication_date", "")).timestamp()
            except Exception:
                return 0
        jobs_raw = [j for j in jobs_raw if _pub_ts(j) >= cutoff]

    jobs_raw = jobs_raw[offset: offset + results_wanted]

    # job_type mapping: remotive uses "full_time", "contract", etc.
    type_map = {"full_time": "fulltime", "part_time": "parttime",
                "contract": "contract", "freelance": "contract",
                "internship": "internship", "other": None}

    results = []
    for j in jobs_raw:
        date_posted = None
        if j.get("publication_date"):
            try:
                date_posted = j["publication_date"][:10]
            except Exception:
                pass

        jt = type_map.get((j.get("job_type") or "").lower())

        loc_str = j.get("candidate_required_location") or ""

        results.append({
            "title":            j.get("title") or None,
            "company":          j.get("company_name") or None,
            "site":             "remotive",
            "job_url":          j.get("url") or None,
            "location":         {"city": loc_str or None, "state": None, "country": None},
            "is_remote":        True,
            "job_type":         jt,
            "job_level":        None,
            "date_posted":      date_posted,
            "salary":           None,
            "description":      j.get("description") or None,
            "emails":           None,
            "skills":           j.get("tags") or [],
            "company_url":      None,
            "company_industry": j.get("category") or None,
        })
    return results


async def scrape_jobicy(params: dict) -> List[dict]:
    """Fetch jobs from Jobicy public API (https://jobicy.com/api/v2/remote-jobs)."""
    is_remote = _bool(params.get("is_remote"))
    if is_remote is False:
        return []  # Jobicy is remote-only

    search_term    = params.get("search_term", "") or ""
    results_wanted = _int(params.get("results_wanted"), 15)
    hours_old      = _int(params.get("hours_old"))
    offset         = _int(params.get("offset"), 0)
    job_type       = params.get("job_type")

    # Jobicy caps count at 50
    query: dict = {"count": min(offset + results_wanted, 50)}
    if search_term:
        query["tag"] = search_term
    # geo expects a plain country name (e.g. "usa", "germany").
    # Prefer country_indeed (designed for this), otherwise take the last
    # comma-separated token from location ("New York, NY, USA" → "usa").
    country_indeed = (params.get("country_indeed") or "").strip()
    location       = (params.get("location")       or "").strip()
    geo = country_indeed or (location.split(",")[-1].strip() if location else "")
    if geo:
        query["geo"] = geo.lower()

    async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
        resp = await client.get(
            "https://jobicy.com/api/v2/remote-jobs",
            params=query,
            headers={"Accept": "application/json"},
        )
        resp.raise_for_status()
        data = resp.json()

    jobs_raw = data.get("jobs", [])

    if hours_old:
        cutoff = time.time() - hours_old * 3600
        def _pub_ts2(j):
            try:
                return _dp.parse(j.get("pubDate", "")).timestamp()
            except Exception:
                return 0
        jobs_raw = [j for j in jobs_raw if _pub_ts2(j) >= cutoff]

    # map jobType array → our job_type enum
    type_map = {"full-time": "fulltime", "part-time": "parttime",
                "freelance": "contract", "contract": "contract",
                "internship": "internship"}
    if job_type:
        jobs_raw = [j for j in jobs_raw
                    if any(type_map.get((jt or "").lower()) == job_type
                           for jt in j.get("jobType", []))]

    jobs_raw = jobs_raw[offset: offset + results_wanted]

    results = []
    for j in jobs_raw:
        date_posted = None
        if j.get("pubDate"):
            try:
                date_posted = j["pubDate"][:10]
            except Exception:
                pass

        jts = " ".join(j.get("jobType", [])).lower()
        jt = None
        if "full" in jts:      jt = "fulltime"
        elif "part" in jts:    jt = "parttime"
        elif "intern" in jts:  jt = "internship"
        elif "contract" in jts or "freelance" in jts: jt = "contract"

        salary = None
        if j.get("salaryMin") or j.get("salaryMax"):
            salary = {
                "min_amount": j.get("salaryMin") or None,
                "max_amount": j.get("salaryMax") or None,
                "interval":   j.get("salaryPeriod") or "yearly",
                "currency":   j.get("salaryCurrency") or "USD",
            }

        industry_list = j.get("jobIndustry") or []
        industry = industry_list[0] if industry_list else None

        loc_str = j.get("jobGeo") or ""

        title = _html.unescape(_html.unescape(j.get("jobTitle") or ""))

        results.append({
            "title":            title or None,
            "company":          j.get("companyName") or None,
            "site":             "jobicy",
            "job_url":          j.get("url") or None,
            "location":         {"city": loc_str or None, "state": None, "country": None},
            "is_remote":        True,
            "job_type":         jt,
            "job_level":        j.get("jobLevel") or None,
            "date_posted":      date_posted,
            "salary":           salary,
            "description":      j.get("jobDescription") or None,
            "emails":           None,
            "skills":           [],
            "company_url":      None,
            "company_industry": industry,
        })
    return results


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

    # Validate and route requested sites
    all_requested = _csv_list(params.get("site_name")) or sorted(VALID_SITES)
    invalid = [s for s in all_requested if s not in VALID_SITES]
    if invalid:
        raise HTTPException(400, f"Unknown site(s): {invalid}. Valid: {sorted(VALID_SITES)}")

    jobspy_requested = [s for s in all_requested if s in JOBSPY_SITES]
    custom_requested = [s for s in all_requested if s in CUSTOM_SITES]

    all_records: List[dict] = []
    query_echo: dict = {}

    # ── python-jobspy sites ───────────────────────────────────────────────────
    if jobspy_requested:
        jspy_params = {**params, "site_name": jobspy_requested}
        kwargs, _ = _build_kwargs(jspy_params)
        query_echo = {k: v for k, v in kwargs.items() if k != "verbose"}
        try:
            df = scrape_jobs(**kwargs)
            if df is not None and not df.empty:
                all_records.extend(df_to_safe_records(df))
        except Exception as exc:
            if not custom_requested:
                raise HTTPException(500, detail={
                    "error":      str(exc),
                    "error_type": type(exc).__name__,
                    "parameters": query_echo,
                })

    # ── custom / public-API sites ─────────────────────────────────────────────
    for site in custom_requested:
        try:
            if site == "remoteok":
                all_records.extend(await scrape_remoteok(params))
            elif site == "arbeitnow":
                all_records.extend(await scrape_arbeitnow(params))
            elif site == "remotive":
                all_records.extend(await scrape_remotive(params))
            elif site == "jobicy":
                all_records.extend(await scrape_jobicy(params))
        except Exception:
            pass  # don't fail the whole request if one custom source errors

    if not query_echo:
        query_echo = {k: v for k, v in params.items()
                      if k != "output_format" and v is not None}

    if output_format == "csv":
        return _csv_response(pd.DataFrame(all_records) if all_records else pd.DataFrame())
    if output_format == "excel":
        return _excel_response(pd.DataFrame(all_records) if all_records else pd.DataFrame())

    return {"jobs": all_records, "count": len(all_records),
            "sites": all_requested, "query": query_echo}


# ── Routes ────────────────────────────────────────────────────────────────────

@app.get("/", include_in_schema=False)
@app.get("/{page}.html", include_in_schema=False)
async def serve_html(page: str = "index"):
    html = _PAGES.get(page)
    if html is None:
        raise HTTPException(404, "Not found")
    return HTMLResponse(content=html)


@app.get("/api/health", tags=["Meta"])
async def health():
    """API health check."""
    return {"status": "ok", "service": "Job API", "version": "2.0.0"}


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

