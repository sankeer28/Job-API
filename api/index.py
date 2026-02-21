"""
JobSpy API — Vercel Serverless

Wraps python-jobspy's scrape_jobs() as a JSON REST endpoint.

Local dev:  python api/index.py       → http://localhost:8000
Vercel:     vercel.json routes everything here automatically.

Endpoints
---------
GET  /              → API reference (parameter docs)
GET  /api/jobs      → scrape jobs, all params as query-string
POST /api/jobs      → scrape jobs, all params as JSON body
                      (query-string and JSON body are merged; body wins on conflict)
"""

import os
import sys
import math

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from flask import Flask, request, jsonify
from flask_cors import CORS
from flasgger import Swagger
import pandas as pd
from jobspy import scrape_jobs

app = Flask(__name__)
CORS(app)  # allow all origins — restrict to your domain in production if needed

# ── Swagger / OpenAPI config ───────────────────────────────────────────────────
SWAGGER_TEMPLATE = {
    "swagger": "2.0",
    "info": {
        "title": "JobSpy API",
        "description": (
            "Aggregate job postings from LinkedIn, Indeed, Glassdoor, "
            "ZipRecruiter, Google Jobs, Bayt, Naukri, and more via python-jobspy.\n\n"
            "**Quick start:** fill in `search_term` + `location`, leave everything "
            "else at its default, and hit **Execute**."
        ),
        "version": "1.0.0",
    },
    "basePath": "/",
    "schemes": ["http", "https"],
    "consumes": ["application/json"],
    "produces": ["application/json"],
    "tags": [
        {"name": "Jobs",      "description": "Job-scraping endpoints"},
        {"name": "Reference", "description": "API documentation"},
    ],
    "definitions": {
        "Salary": {
            "type": "object",
            "properties": {
                "interval":      {"type": "string", "example": "yearly"},
                "min_amount":    {"type": "number", "example": 80000},
                "max_amount":    {"type": "number", "example": 120000},
                "currency":      {"type": "string", "example": "USD"},
                "salary_source": {"type": "string", "example": "direct_data"},
            },
        },
        "Location": {
            "type": "object",
            "properties": {
                "country": {"type": "string"},
                "city":    {"type": "string"},
                "state":   {"type": "string"},
            },
        },
        "JobPost": {
            "type": "object",
            "properties": {
                "site":             {"type": "string", "example": "indeed"},
                "title":            {"type": "string", "example": "Software Engineer"},
                "company":          {"type": "string", "example": "Acme Corp"},
                "company_url":      {"type": "string"},
                "job_url":          {"type": "string"},
                "location":         {"$ref": "#/definitions/Location"},
                "is_remote":        {"type": "boolean"},
                "description":      {"type": "string"},
                "job_type":         {"type": "string", "example": "fulltime"},
                "salary":           {"$ref": "#/definitions/Salary"},
                "date_posted":      {"type": "string", "format": "date", "example": "2025-01-15"},
                "emails":           {"type": "array", "items": {"type": "string"}},
                "job_level":        {"type": "string"},
                "company_industry": {"type": "string"},
            },
        },
        "JobsResponse": {
            "type": "object",
            "properties": {
                "jobs":  {"type": "array", "items": {"$ref": "#/definitions/JobPost"}},
                "count": {"type": "integer", "example": 15},
                "sites": {"type": "array", "items": {"type": "string"}, "example": ["indeed"]},
                "query": {"type": "object"},
            },
        },
        "ErrorResponse": {
            "type": "object",
            "properties": {
                "error":      {"type": "string"},
                "error_type": {"type": "string"},
                "parameters": {"type": "object"},
            },
        },
        "JobsBody": {
            "type": "object",
            "properties": {
                "site_name":                  {"type": "array", "items": {"type": "string"}, "example": ["indeed"],
                                              "description": "Job boards to scrape. Options: linkedin, indeed, zip_recruiter, glassdoor, google, bayt, bdjobs, naukri"},
                "search_term":                {"type": "string",  "example": "software engineer"},
                "google_search_term":         {"type": "string",  "example": "software engineer jobs near New York since yesterday",
                                              "description": "Used only for Google Jobs — copy directly from a google.com/search jobs query"},
                "location":                   {"type": "string",  "example": "New York, NY"},
                "distance":                   {"type": "integer", "example": 50, "default": 50},
                "job_type":                   {"type": "string",  "example": "fulltime",
                                              "description": "fulltime | parttime | internship | contract"},
                "is_remote":                  {"type": "boolean", "example": False},
                "results_wanted":             {"type": "integer", "example": 15, "default": 15,
                                              "description": "Number of results per site (max ~1000)"},
                "hours_old":                  {"type": "integer", "example": 72,
                                              "description": "Only return jobs posted within the last N hours"},
                "easy_apply":                 {"type": "boolean", "example": False},
                "description_format":         {"type": "string",  "example": "markdown",
                                              "description": "markdown | html"},
                "offset":                     {"type": "integer", "example": 0, "default": 0},
                "linkedin_fetch_description": {"type": "boolean", "example": False,
                                              "description": "Fetch full LinkedIn description (much slower)"},
                "linkedin_company_ids":       {"type": "array", "items": {"type": "integer"},
                                              "example": [1441]},
                "country_indeed":             {"type": "string",  "example": "USA",
                                              "description": "Country filter for Indeed / Glassdoor (e.g. USA, UK, Canada, Germany)"},
                "enforce_annual_salary":      {"type": "boolean", "example": False},
                "proxies":                    {"type": "array", "items": {"type": "string"},
                                              "example": ["user:pass@1.2.3.4:8080"]},
                "user_agent":                 {"type": "string"},
                "ca_cert":                    {"type": "string"},
            },
        },
    },
}

swagger = Swagger(app, template=SWAGGER_TEMPLATE, config={
    "headers": [],
    "specs": [{"endpoint": "apispec", "route": "/apispec.json"}],
    "static_url_path": "/flasgger_static",
    "swagger_ui": True,
    "specs_route": "/docs",
})

# ── Helpers ───────────────────────────────────────────────────────────────────

TRUTHY = {"1", "true", "yes"}


def _bool(val) -> bool | None:
    """Parse a bool from a string or native bool/int. Returns None if val is None."""
    if val is None:
        return None
    if isinstance(val, bool):
        return val
    if isinstance(val, int):
        return bool(val)
    return str(val).lower() in TRUTHY


def _int(val, default=None) -> int | None:
    if val is None or val == "":
        return default
    try:
        return int(val)
    except (ValueError, TypeError):
        return default


def _csv_list(val, cast=str) -> list | None:
    """Split a comma-separated string (or pass-through a list) and cast each element."""
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


def df_to_safe_records(df: pd.DataFrame) -> list[dict]:
    """
    Convert a pandas DataFrame to a list of plain dicts that are safe to
    JSON-serialise:
      - float NaN / inf  → None
      - NaT / pd.NA      → None
      - date / datetime  → ISO-8601 string
      - everything else  → kept as-is
    """
    records = []
    for _, row in df.iterrows():
        clean: dict = {}
        for key, val in row.items():
            try:
                if val is None:
                    clean[key] = None
                elif isinstance(val, float) and (math.isnan(val) or math.isinf(val)):
                    clean[key] = None
                elif hasattr(val, "isoformat"):          # date / datetime
                    clean[key] = val.isoformat()
                elif isinstance(val, (list, dict, bool)):
                    clean[key] = val
                else:
                    # Check pandas-specific NA types (NAType, NaT, etc.)
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


# ── Routes ────────────────────────────────────────────────────────────────────

@app.route("/", methods=["GET"])
def index():
    """
    API reference
    ---
    tags:
      - Reference
    summary: Full parameter reference and response schema
    responses:
      200:
        description: API documentation object
    """
    return jsonify({
        "name": "JobSpy API",
        "version": "1.0.0",
        "description": (
            "Aggregate job postings from LinkedIn, Indeed, Glassdoor, "
                "ZipRecruiter, Google Jobs, Bayt, Naukri, and more via python-jobspy."
        ),
        "endpoints": {
            "GET /":         "This reference page.",
            "GET /api/jobs": "Scrape jobs — pass all parameters as query-string.",
            "POST /api/jobs": (
                "Scrape jobs — pass parameters as a JSON body. "
                "Query-string and JSON body are merged; body takes precedence."
            ),
        },
        "parameters": {
            "site_name": {
                "type": "string (CSV) | array",
                "description": "Which job boards to scrape.",
                "options": ["linkedin", "indeed", "zip_recruiter", "glassdoor", "google", "bayt", "bdjobs", "naukri"],
                "default": "all of the above",
                "example": "site_name=indeed,linkedin",
            },
            "search_term": {
                "type": "string",
                "description": (
                    "Job search query. Supports negative keywords (-word) "
                    "and exact phrases (\"phrase\"). "
                    "Note: Indeed searches title AND description."
                ),
                "example": "search_term=software engineer",
            },
            "google_search_term": {
                "type": "string",
                "description": (
                    "Search term used specifically for Google Jobs. "
                    "This is the ONLY filter applied to Google results — "
                    "copy the string directly from a google.com/search jobs query."
                ),
                "example": "google_search_term=software engineer jobs near San Francisco, CA since yesterday",
            },
            "location": {
                "type": "string",
                "description": (
                    "City, state, or country. LinkedIn searches globally. "
                    "ZipRecruiter is US/Canada only. "
                    "Use country_indeed for Indeed/Glassdoor country selection."
                ),
                "example": "location=San Francisco, CA",
            },
            "distance": {
                "type": "integer",
                "description": "Search radius in miles.",
                "default": 50,
                "example": "distance=25",
            },
            "job_type": {
                "type": "string",
                "description": "Filter by employment type.",
                "options": ["fulltime", "parttime", "internship", "contract"],
                "note": "Cannot be combined with hours_old or easy_apply on Indeed.",
                "example": "job_type=fulltime",
            },
            "is_remote": {
                "type": "boolean",
                "description": "Filter for remote listings only.",
                "note": "Cannot be combined with hours_old or easy_apply on Indeed.",
                "example": "is_remote=true",
            },
            "results_wanted": {
                "type": "integer",
                "description": (
                    "Number of results to return PER site. "
                    "All boards are capped at ~1 000 results per search."
                ),
                "default": 15,
                "example": "results_wanted=50",
            },
            "hours_old": {
                "type": "integer",
                "description": (
                    "Only return jobs posted within the last N hours. "
                    "ZipRecruiter and Glassdoor round up to the nearest day. "
                    "Note: cannot combine with job_type, is_remote, or easy_apply on Indeed."
                ),
                "example": "hours_old=72",
            },
            "easy_apply": {
                "type": "boolean",
                "description": (
                    "Only return jobs that host their application on the board itself. "
                    "LinkedIn Easy Apply filter no longer reliably works."
                ),
                "note": "Cannot be combined with hours_old or job_type/is_remote on Indeed.",
                "example": "easy_apply=true",
            },
            "description_format": {
                "type": "string",
                "description": "Output format for the job description field.",
                "options": ["markdown", "html"],
                "default": "markdown",
                "example": "description_format=html",
            },
            "offset": {
                "type": "integer",
                "description": "Skip the first N results (useful for pagination).",
                "default": 0,
                "example": "offset=25",
            },
            "linkedin_fetch_description": {
                "type": "boolean",
                "description": (
                    "Fetch the full LinkedIn job description and the direct job URL. "
                    "Increases the number of requests by O(n) — much slower."
                ),
                "default": False,
                "example": "linkedin_fetch_description=true",
            },
            "linkedin_company_ids": {
                "type": "integer CSV | array of integers",
                "description": "Filter LinkedIn results to specific company IDs.",
                "example": "linkedin_company_ids=1441,2382",
            },
            "country_indeed": {
                "type": "string",
                "description": (
                    "Country filter for Indeed and Glassdoor. "
                    "Must match the exact country name (e.g. 'USA', 'UK', 'Canada', 'Germany'). "
                    "See README for the full list of supported countries."
                ),
                "example": "country_indeed=USA",
            },
            "enforce_annual_salary": {
                "type": "boolean",
                "description": "Convert all salary figures to an annual amount.",
                "default": False,
                "example": "enforce_annual_salary=true",
            },
            "proxies": {
                "type": "string CSV | array",
                "description": (
                    "Proxy list. Each proxy cycles across all scrapers (round-robin). "
                    "Format: 'user:pass@host:port' or 'host:port'. "
                    "Use 'localhost' to route through a local proxy."
                ),
                "example": "proxies=user:pass@1.2.3.4:8080,5.6.7.8:3128",
            },
            "user_agent": {
                "type": "string",
                "description": "Override the default User-Agent header sent to job boards.",
                "example": "user_agent=Mozilla/5.0 ...",
            },
            "ca_cert": {
                "type": "string",
                "description": "Filesystem path to a CA certificate bundle for HTTPS proxies.",
                "example": "ca_cert=/etc/ssl/certs/ca-certificates.crt",
            },
        },
        "response_schema": {
            "jobs":  "Array of job objects (see JobPost schema below).",
            "count": "Number of jobs returned.",
            "sites": "List of sites that were queried.",
            "query": "Echo of the parameters that were forwarded to scrape_jobs().",
            "jobpost_schema": {
                "site":             "Source job board",
                "title":            "Job title",
                "company":          "Company name",
                "company_url":      "Company profile URL",
                "job_url":          "Direct link to the job posting",
                "location": {
                    "country": "Country",
                    "city":    "City",
                    "state":   "State / province",
                },
                "is_remote":        "Boolean — remote position",
                "description":      "Full job description (format controlled by description_format)",
                "job_type":         "fulltime | parttime | internship | contract",
                "salary": {
                    "interval":      "yearly | monthly | weekly | daily | hourly",
                    "min_amount":    "Minimum salary",
                    "max_amount":    "Maximum salary",
                    "currency":      "ISO currency code",
                    "salary_source": "direct_data | description",
                },
                "date_posted":      "ISO-8601 date string",
                "emails":           "List of contact emails extracted from the description",
                "job_level":        "(LinkedIn only) seniority level",
                "company_industry": "(LinkedIn & Indeed) industry classification",
                "indeed_extras": {
                    "company_country":         "Country of the hiring company",
                    "company_addresses":       "Office addresses",
                    "company_employees_label": "Employee count bucket",
                    "company_revenue_label":   "Revenue bucket",
                    "company_description":     "About the company",
                    "company_logo":            "Logo image URL",
                },
                "naukri_extras": {
                    "skills":                "Required skills list",
                    "experience_range":      "e.g. '2-5 years'",
                    "company_rating":        "Naukri employer rating",
                    "company_reviews_count": "Number of reviews",
                    "vacancy_count":         "Number of open positions",
                    "work_from_home_type":   "WFH classification",
                },
            },
        },
        "notes": [
            "Indeed is currently the most reliable scraper (no rate limiting).",
            "LinkedIn rate-limits aggressively — use proxies for large result sets.",
            "All boards cap results at ~1 000 per search.",
            "ZipRecruiter and Glassdoor round hours_old up to the nearest day.",
            (
                "Vercel Hobby plan limits function execution to 60 s. "
                "For large scrapes (results_wanted > 50 or many sites) consider "
                "the Pro plan or running the API outside of Vercel."
            ),
        ],
    })


@app.route("/api/jobs", methods=["GET"])
def jobs_get():
    """
    Scrape jobs (GET)
    ---
    tags:
      - Jobs
    summary: Scrape job postings — pass all parameters as query-string
    parameters:
      - name: site_name
        in: query
        type: string
        description: "Comma-separated list of job boards. Options: linkedin, indeed, zip_recruiter, glassdoor, google, bayt, bdjobs, naukri"
        example: indeed
      - name: search_term
        in: query
        type: string
        description: Job search query (supports -negative and \"exact phrase\")
        example: software engineer
      - name: google_search_term
        in: query
        type: string
        description: "Used only for Google Jobs — copy directly from a google.com/search jobs query"
        example: "software engineer jobs near New York since yesterday"
      - name: location
        in: query
        type: string
        description: City, state, or country
        example: "New York, NY"
      - name: distance
        in: query
        type: integer
        description: Search radius in miles
        default: 50
      - name: job_type
        in: query
        type: string
        description: "fulltime | parttime | internship | contract"
        example: fulltime
      - name: is_remote
        in: query
        type: boolean
        description: Filter for remote listings only
      - name: results_wanted
        in: query
        type: integer
        description: Number of results per site (max ~1000)
        default: 15
      - name: hours_old
        in: query
        type: integer
        description: Only return jobs posted within the last N hours
        example: 72
      - name: easy_apply
        in: query
        type: boolean
        description: Only return jobs with in-board application
      - name: description_format
        in: query
        type: string
        description: "markdown | html"
        default: markdown
      - name: offset
        in: query
        type: integer
        description: Skip first N results (pagination)
        default: 0
      - name: linkedin_fetch_description
        in: query
        type: boolean
        description: Fetch full LinkedIn description — much slower
        default: false
      - name: linkedin_company_ids
        in: query
        type: string
        description: Comma-separated LinkedIn company IDs
        example: "1441,2382"
      - name: country_indeed
        in: query
        type: string
        description: Country filter for Indeed / Glassdoor (e.g. USA, UK, Canada, Germany)
        example: USA
      - name: enforce_annual_salary
        in: query
        type: boolean
        description: Convert all salary figures to annual amount
        default: false
      - name: proxies
        in: query
        type: string
        description: Comma-separated proxy list (user:pass@host:port)
      - name: user_agent
        in: query
        type: string
        description: Override the default User-Agent header
      - name: ca_cert
        in: query
        type: string
        description: Path to CA certificate bundle for HTTPS proxies
    responses:
      200:
        description: List of job postings
        schema:
          $ref: '#/definitions/JobsResponse'
      400:
        description: Invalid parameter value
        schema:
          $ref: '#/definitions/ErrorResponse'
      500:
        description: Scraping error
        schema:
          $ref: '#/definitions/ErrorResponse'
    """
    return _jobs_handler()


@app.route("/api/jobs", methods=["POST"])
def jobs_post():
    """
    Scrape jobs (POST)
    ---
    tags:
      - Jobs
    summary: Scrape job postings — pass parameters as a JSON body
    description: Query-string and JSON body are merged; body takes precedence on conflict.
    parameters:
      - name: body
        in: body
        required: false
        schema:
          $ref: '#/definitions/JobsBody'
    responses:
      200:
        description: List of job postings
        schema:
          $ref: '#/definitions/JobsResponse'
      400:
        description: Invalid parameter value
        schema:
          $ref: '#/definitions/ErrorResponse'
      500:
        description: Scraping error
        schema:
          $ref: '#/definitions/ErrorResponse'
    """
    return _jobs_handler()


def _jobs_handler():
    # ── Merge query-string + JSON body ────────────────────────────────────────
    params: dict = {}
    params.update(request.args.to_dict(flat=True))
    if request.is_json:
        body = request.get_json(silent=True)
        if body and isinstance(body, dict):
            params.update(body)

    # ── Parse every supported parameter ──────────────────────────────────────

    # site_name — default to all supported sites
    site_name = _csv_list(params.get("site_name"))
    if not site_name:
        site_name = ["linkedin", "indeed", "zip_recruiter", "glassdoor", "google", "bayt", "bdjobs", "naukri"]

    search_term             = params.get("search_term") or None
    google_search_term      = params.get("google_search_term") or None
    location                = params.get("location") or None
    distance                = _int(params.get("distance"), default=50)
    job_type                = params.get("job_type") or None
    is_remote               = _bool(params.get("is_remote")) if params.get("is_remote") is not None else None
    results_wanted          = _int(params.get("results_wanted"), default=15)
    hours_old               = _int(params.get("hours_old"))
    easy_apply              = _bool(params.get("easy_apply")) if params.get("easy_apply") is not None else None
    description_format      = params.get("description_format") or "markdown"
    offset                  = _int(params.get("offset"), default=0)

    linkedin_fetch_description = _bool(params.get("linkedin_fetch_description")) or False
    linkedin_company_ids       = _csv_list(params.get("linkedin_company_ids"), cast=int)

    country_indeed          = params.get("country_indeed") or None
    enforce_annual_salary   = _bool(params.get("enforce_annual_salary")) or False

    proxies                 = _csv_list(params.get("proxies"))
    user_agent              = params.get("user_agent") or None
    ca_cert                 = params.get("ca_cert") or None

    # ── Validate ──────────────────────────────────────────────────────────────
    valid_sites = {"linkedin", "indeed", "zip_recruiter", "glassdoor", "google", "bayt", "bdjobs", "naukri"}
    invalid = [s for s in site_name if s not in valid_sites]
    if invalid:
        return jsonify({
            "error": f"Unknown site(s): {invalid}. Valid options: {sorted(valid_sites)}"
        }), 400

    valid_job_types = {None, "fulltime", "parttime", "internship", "contract"}
    if job_type not in valid_job_types:
        return jsonify({
            "error": f"Invalid job_type '{job_type}'. Valid options: fulltime, parttime, internship, contract"
        }), 400

    valid_formats = {None, "markdown", "html"}
    if description_format not in valid_formats:
        return jsonify({
            "error": f"Invalid description_format '{description_format}'. Valid options: markdown, html"
        }), 400

    # ── Build kwargs — only forward non-None optional args ────────────────────
    kwargs: dict = {
        "site_name":                   site_name,
        "results_wanted":              results_wanted,
        "distance":                    distance,
        "description_format":          description_format,
        "offset":                      offset,
        "linkedin_fetch_description":  linkedin_fetch_description,
        "enforce_annual_salary":       enforce_annual_salary,
        "verbose":                     0,   # suppress console output in API mode
    }

    if search_term:             kwargs["search_term"]           = search_term
    if google_search_term:      kwargs["google_search_term"]    = google_search_term
    if location:                kwargs["location"]              = location
    if job_type:                kwargs["job_type"]              = job_type
    if is_remote is not None:   kwargs["is_remote"]             = is_remote
    if hours_old is not None:   kwargs["hours_old"]             = hours_old
    if easy_apply is not None:  kwargs["easy_apply"]            = easy_apply
    if linkedin_company_ids:    kwargs["linkedin_company_ids"]  = linkedin_company_ids
    if country_indeed:          kwargs["country_indeed"]        = country_indeed
    if proxies:                 kwargs["proxies"]               = proxies
    if user_agent:              kwargs["user_agent"]            = user_agent
    if ca_cert:                 kwargs["ca_cert"]               = ca_cert

    # ── Scrape ────────────────────────────────────────────────────────────────
    try:
        df = scrape_jobs(**kwargs)
    except Exception as exc:
        return jsonify({
            "error":      str(exc),
            "error_type": type(exc).__name__,
            "parameters": {k: v for k, v in kwargs.items() if k != "verbose"},
        }), 500

    # ── Serialise ─────────────────────────────────────────────────────────────
    if df is None or df.empty:
        return jsonify({
            "jobs":  [],
            "count": 0,
            "sites": site_name,
            "query": {k: v for k, v in kwargs.items() if k != "verbose"},
        })

    records = df_to_safe_records(df)

    return jsonify({
        "jobs":  records,
        "count": len(records),
        "sites": site_name,
        "query": {k: v for k, v in kwargs.items() if k != "verbose"},
    })


# ── Dev server ────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    app.run(debug=True, port=8000)
