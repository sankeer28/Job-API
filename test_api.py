"""
JobSpy API — Test Suite
========================
Requires the server to be running first:
    python api/index.py

Then run this script in a separate terminal:
    python test_api.py [base_url]

Default base_url = http://localhost:8000
"""

import sys
import json
import time
import urllib.request
import urllib.parse
import urllib.error

BASE_URL = sys.argv[1].rstrip("/") if len(sys.argv) > 1 else "http://localhost:8000"

# ── Colours (works on Windows 10+ and all Unix terminals) ────────────────────
GREEN  = "\033[92m"
RED    = "\033[91m"
YELLOW = "\033[93m"
CYAN   = "\033[96m"
RESET  = "\033[0m"
BOLD   = "\033[1m"

passed = 0
failed = 0
skipped = 0


# ── Helpers ───────────────────────────────────────────────────────────────────

def _request(method: str, path: str, params: dict | None = None, body: dict | None = None):
    url = BASE_URL + path
    if params:
        url += "?" + urllib.parse.urlencode(params, doseq=True)

    data = None
    headers = {}
    if body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"

    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            raw = resp.read().decode()
            return resp.status, json.loads(raw)
    except urllib.error.HTTPError as e:
        raw = e.read().decode()
        try:
            return e.code, json.loads(raw)
        except Exception:
            return e.code, {"raw": raw}
    except urllib.error.URLError as e:
        raise ConnectionError(
            f"\n  Could not reach {BASE_URL}\n"
            f"  Make sure the server is running:  python api/index.py\n"
            f"  Error: {e.reason}"
        )


def run_test(name: str, method: str, path: str,
             params: dict | None = None, body: dict | None = None,
             expected_status: int = 200,
             checks: list | None = None,
             slow: bool = False):
    """Execute one test case and print the result."""
    global passed, failed, skipped
    tag = f"[{method} {path}]"
    label = f"{BOLD}{name}{RESET}"

    if slow:
        print(f"  {YELLOW}SKIP (slow){RESET}  {tag}  {label}")
        skipped += 1
        return

    print(f"  {CYAN}RUN{RESET}  {tag}  {label}", end="", flush=True)
    t0 = time.monotonic()
    try:
        status, data = _request(method, path, params=params, body=body)
    except ConnectionError as e:
        print(f"\n{RED}FATAL:{RESET}{e}")
        sys.exit(1)
    elapsed = time.monotonic() - t0

    errors = []

    if status != expected_status:
        errors.append(f"expected HTTP {expected_status}, got {status}")

    for check_fn, description in (checks or []):
        try:
            if not check_fn(data):
                errors.append(f"check failed: {description}")
        except Exception as exc:
            errors.append(f"check raised {type(exc).__name__}: {exc}")

    if errors:
        failed += 1
        print(f"\r  {RED}FAIL{RESET}  {tag}  {label}  ({elapsed:.1f}s)")
        for e in errors:
            print(f"       {RED}✗{RESET} {e}")
        # Print truncated response for debugging
        preview = json.dumps(data)[:400]
        print(f"       response: {preview}")
    else:
        passed += 1
        print(f"\r  {GREEN}PASS{RESET}  {tag}  {label}  ({elapsed:.1f}s)")


# ── Test Cases ────────────────────────────────────────────────────────────────

def test_reference_page():
    run_test(
        name="Reference page returns API docs",
        method="GET", path="/",
        checks=[
            (lambda d: "name" in d,        "has 'name' key"),
            (lambda d: "endpoints" in d,   "has 'endpoints' key"),
            (lambda d: "parameters" in d,  "has 'parameters' key"),
            (lambda d: "response_schema" in d, "has 'response_schema' key"),
            (lambda d: d.get("name") == "JobSpy API", "name == 'JobSpy API'"),
        ],
    )


def test_get_jobs_indeed_only():
    run_test(
        name="GET /api/jobs — Indeed only, 3 results",
        method="GET", path="/api/jobs",
        params={"site_name": "indeed", "search_term": "python developer",
                "location": "New York", "results_wanted": 3, "country_indeed": "USA"},
        checks=[
            (lambda d: "jobs" in d,          "has 'jobs' key"),
            (lambda d: "count" in d,         "has 'count' key"),
            (lambda d: "sites" in d,         "has 'sites' key"),
            (lambda d: "query" in d,         "has 'query' key"),
            (lambda d: isinstance(d["jobs"], list), "jobs is a list"),
            (lambda d: d["count"] == len(d["jobs"]), "count matches len(jobs)"),
            (lambda d: d["sites"] == ["indeed"], "sites == ['indeed']"),
        ],
    )


def test_post_jobs_indeed_only():
    run_test(
        name="POST /api/jobs — Indeed only, 3 results (JSON body)",
        method="POST", path="/api/jobs",
        body={"site_name": ["indeed"], "search_term": "data engineer",
              "location": "Remote", "results_wanted": 3, "country_indeed": "USA"},
        checks=[
            (lambda d: "jobs" in d,    "has 'jobs' key"),
            (lambda d: "count" in d,   "has 'count' key"),
            (lambda d: isinstance(d["jobs"], list), "jobs is a list"),
        ],
    )


def test_post_body_overrides_query():
    """Body param (results_wanted=2) must override query-string (results_wanted=10)."""
    run_test(
        name="POST — body wins over query-string on conflict",
        method="POST", path="/api/jobs",
        params={"site_name": "indeed", "results_wanted": "10",
                "search_term": "qa engineer", "country_indeed": "USA"},
        body={"results_wanted": 2, "location": "Chicago"},
        checks=[
            (lambda d: d.get("query", {}).get("results_wanted") == 2,
             "query echo shows results_wanted=2 (body value)"),
        ],
    )


def test_fulltime_filter():
    run_test(
        name="GET /api/jobs — job_type=fulltime",
        method="GET", path="/api/jobs",
        params={"site_name": "indeed", "search_term": "developer",
                "location": "Austin, TX", "results_wanted": 3,
                "job_type": "fulltime", "country_indeed": "USA"},
        checks=[
            (lambda d: "jobs" in d, "has 'jobs' key"),
        ],
    )


def test_remote_filter():
    run_test(
        name="GET /api/jobs — is_remote=true",
        method="GET", path="/api/jobs",
        params={"site_name": "indeed", "search_term": "backend engineer",
                "results_wanted": 3, "is_remote": "true", "country_indeed": "USA"},
        checks=[
            (lambda d: "jobs" in d, "has 'jobs' key"),
        ],
    )


def test_hours_old_filter():
    run_test(
        name="GET /api/jobs — hours_old=72",
        method="GET", path="/api/jobs",
        params={"site_name": "indeed", "search_term": "software engineer",
                "location": "Boston, MA", "results_wanted": 3,
                "hours_old": 72, "country_indeed": "USA"},
        checks=[
            (lambda d: "jobs" in d, "has 'jobs' key"),
        ],
    )


def test_offset_pagination():
    run_test(
        name="GET /api/jobs — offset=5 (pagination)",
        method="GET", path="/api/jobs",
        params={"site_name": "indeed", "search_term": "analyst",
                "location": "Seattle, WA", "results_wanted": 3,
                "offset": 5, "country_indeed": "USA"},
        checks=[
            (lambda d: d.get("query", {}).get("offset") == 5,
             "query echo shows offset=5"),
        ],
    )


def test_description_format_html():
    run_test(
        name="GET /api/jobs — description_format=html",
        method="GET", path="/api/jobs",
        params={"site_name": "indeed", "search_term": "devops",
                "location": "Austin, TX", "results_wanted": 2,
                "description_format": "html", "country_indeed": "USA"},
        checks=[
            (lambda d: "jobs" in d, "has 'jobs' key"),
            (lambda d: d.get("query", {}).get("description_format") == "html",
             "query echo shows description_format=html"),
        ],
    )


def test_enforce_annual_salary():
    run_test(
        name="GET /api/jobs — enforce_annual_salary=true",
        method="GET", path="/api/jobs",
        params={"site_name": "indeed", "search_term": "nurse",
                "location": "Dallas, TX", "results_wanted": 2,
                "enforce_annual_salary": "true", "country_indeed": "USA"},
        checks=[
            (lambda d: d.get("query", {}).get("enforce_annual_salary") is True,
             "query echo shows enforce_annual_salary=True"),
        ],
    )


def test_country_indeed_canada():
    run_test(
        name="GET /api/jobs — country_indeed=Canada",
        method="GET", path="/api/jobs",
        params={"site_name": "indeed", "search_term": "accountant",
                "location": "Toronto", "results_wanted": 3,
                "country_indeed": "Canada"},
        checks=[
            (lambda d: "jobs" in d, "has 'jobs' key"),
            (lambda d: d.get("query", {}).get("country_indeed") == "Canada",
             "query echo shows country_indeed=Canada"),
        ],
    )


def test_csv_site_list():
    """Comma-separated site_name via GET query-string."""
    run_test(
        name="GET /api/jobs — site_name CSV (indeed,glassdoor)",
        method="GET", path="/api/jobs",
        params={"site_name": "indeed,glassdoor", "search_term": "product manager",
                "location": "Chicago, IL", "results_wanted": 2,
                "country_indeed": "USA"},
        checks=[
            (lambda d: set(d.get("sites", [])) == {"indeed", "glassdoor"},
             "sites contains exactly ['indeed','glassdoor']"),
        ],
    )


def test_multi_site_post():
    """Array of sites via POST JSON body."""
    run_test(
        name="POST /api/jobs — site_name array (indeed + zip_recruiter)",
        method="POST", path="/api/jobs",
        body={"site_name": ["indeed", "zip_recruiter"],
              "search_term": "marketing manager",
              "location": "Miami, FL",
              "results_wanted": 2,
              "country_indeed": "USA"},
        checks=[
            (lambda d: set(d.get("sites", [])) == {"indeed", "zip_recruiter"},
             "sites == ['indeed','zip_recruiter']"),
        ],
    )


def test_no_search_term_returns_jobs():
    """API should work without a search_term (scrapes generic listings)."""
    run_test(
        name="GET /api/jobs — no search_term",
        method="GET", path="/api/jobs",
        params={"site_name": "indeed", "location": "New York",
                "results_wanted": 2, "country_indeed": "USA"},
        checks=[
            (lambda d: "jobs" in d, "has 'jobs' key"),
        ],
    )


# ── Error / Validation tests (fast, no scraping) ─────────────────────────────

def test_invalid_site_name():
    run_test(
        name="GET /api/jobs — invalid site_name returns 400",
        method="GET", path="/api/jobs",
        params={"site_name": "fakeboard", "search_term": "test"},
        expected_status=400,
        checks=[
            (lambda d: "error" in d, "has 'error' key"),
            (lambda d: "fakeboard" in d.get("error", ""), "error mentions 'fakeboard'"),
        ],
    )


def test_invalid_job_type():
    run_test(
        name="GET /api/jobs — invalid job_type returns 400",
        method="GET", path="/api/jobs",
        params={"site_name": "indeed", "search_term": "test",
                "job_type": "gig"},
        expected_status=400,
        checks=[
            (lambda d: "error" in d, "has 'error' key"),
            (lambda d: "gig" in d.get("error", ""), "error mentions 'gig'"),
        ],
    )


def test_invalid_description_format():
    run_test(
        name="GET /api/jobs — invalid description_format returns 400",
        method="GET", path="/api/jobs",
        params={"site_name": "indeed", "search_term": "test",
                "description_format": "pdf"},
        expected_status=400,
        checks=[
            (lambda d: "error" in d, "has 'error' key"),
            (lambda d: "pdf" in d.get("error", ""), "error mentions 'pdf'"),
        ],
    )


# ── Slow / optional tests (only run with --slow flag) ─────────────────────────

SLOW_MODE = "--slow" in sys.argv


def test_linkedin_slow():
    run_test(
        name="GET /api/jobs — LinkedIn (slow; may be rate-limited)",
        method="GET", path="/api/jobs",
        params={"site_name": "linkedin", "search_term": "software engineer",
                "location": "San Francisco, CA", "results_wanted": 3},
        slow=not SLOW_MODE,
        checks=[
            (lambda d: "jobs" in d, "has 'jobs' key"),
        ],
    )


def test_glassdoor_slow():
    run_test(
        name="GET /api/jobs — Glassdoor only",
        method="GET", path="/api/jobs",
        params={"site_name": "glassdoor", "search_term": "data scientist",
                "location": "New York", "results_wanted": 3,
                "country_indeed": "USA"},
        slow=not SLOW_MODE,
        checks=[
            (lambda d: "jobs" in d, "has 'jobs' key"),
        ],
    )


def test_zip_recruiter_slow():
    run_test(
        name="GET /api/jobs — ZipRecruiter (US only)",
        method="GET", path="/api/jobs",
        params={"site_name": "zip_recruiter", "search_term": "nurse",
                "location": "Houston, TX", "results_wanted": 3},
        slow=not SLOW_MODE,
        checks=[
            (lambda d: "jobs" in d, "has 'jobs' key"),
        ],
    )


def test_google_slow():
    run_test(
        name="GET /api/jobs — Google Jobs",
        method="GET", path="/api/jobs",
        params={"site_name": "google",
                "google_search_term": "software engineer jobs in New York since yesterday",
                "results_wanted": 3},
        slow=not SLOW_MODE,
        checks=[
            (lambda d: "jobs" in d, "has 'jobs' key"),
        ],
    )


def test_easy_apply_slow():
    run_test(
        name="GET /api/jobs — easy_apply=true (Indeed)",
        method="GET", path="/api/jobs",
        params={"site_name": "indeed", "search_term": "software engineer",
                "location": "Remote", "results_wanted": 3,
                "easy_apply": "true", "country_indeed": "USA"},
        slow=not SLOW_MODE,
        checks=[
            (lambda d: d.get("query", {}).get("easy_apply") is True,
             "query echo shows easy_apply=True"),
        ],
    )


def test_linkedin_company_filter_slow():
    run_test(
        name="GET /api/jobs — linkedin_company_ids filter",
        method="GET", path="/api/jobs",
        params={"site_name": "linkedin", "search_term": "engineer",
                "linkedin_company_ids": "1441",   # LinkedIn company id for Apple
                "results_wanted": 3},
        slow=not SLOW_MODE,
        checks=[
            (lambda d: "jobs" in d, "has 'jobs' key"),
        ],
    )


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    print(f"\n{BOLD}JobSpy API Test Suite{RESET}")
    print(f"Target: {CYAN}{BASE_URL}{RESET}")
    if SLOW_MODE:
        print(f"{YELLOW}Slow tests ENABLED (--slow){RESET}")
    else:
        print(f"{YELLOW}Slow tests SKIPPED  (pass --slow to enable){RESET}")
    print("-" * 60)

    # ── Fast tests (reference page + validation) ──────────────────────
    print(f"\n{BOLD}[Reference & Validation]{RESET}")
    test_reference_page()
    test_invalid_site_name()
    test_invalid_job_type()
    test_invalid_description_format()

    # ── Scraping tests (Indeed is fastest & most reliable) ────────────
    print(f"\n{BOLD}[Indeed scraping]{RESET}")
    test_get_jobs_indeed_only()
    test_post_jobs_indeed_only()
    test_post_body_overrides_query()
    test_fulltime_filter()
    test_remote_filter()
    test_hours_old_filter()
    test_offset_pagination()
    test_description_format_html()
    test_enforce_annual_salary()
    test_country_indeed_canada()
    test_no_search_term_returns_jobs()

    # ── Multi-site tests ──────────────────────────────────────────────
    print(f"\n{BOLD}[Multi-site]{RESET}")
    test_csv_site_list()
    test_multi_site_post()

    # ── Slow / optional tests ─────────────────────────────────────────
    print(f"\n{BOLD}[Slow / optional — other boards]{RESET}")
    test_linkedin_slow()
    test_glassdoor_slow()
    test_zip_recruiter_slow()
    test_google_slow()
    test_easy_apply_slow()
    test_linkedin_company_filter_slow()

    # ── Summary ───────────────────────────────────────────────────────
    total = passed + failed + skipped
    print("\n" + "=" * 60)
    status_line = (
        f"{GREEN}{passed} passed{RESET}  "
        f"{RED}{failed} failed{RESET}  "
        f"{YELLOW}{skipped} skipped{RESET}  "
        f"({total} total)"
    )
    print(f"  {status_line}")
    print("=" * 60 + "\n")

    sys.exit(0 if failed == 0 else 1)


if __name__ == "__main__":
    main()
