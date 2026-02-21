# JobSpy API

A Vercel serverless API that wraps [python-jobspy](https://github.com/speedyapply/JobSpy) and returns job postings as JSON.

Scrapes **LinkedIn, Indeed, Glassdoor, ZipRecruiter, Google Jobs, Bayt, and BDJobs** in a single request.

---

## Deploy to Vercel

```bash
npm i -g vercel
cd jobspy-api
vercel
```

> **Timeout note:** The Hobby plan allows up to **60 seconds** per serverless function (configured in `vercel.json`). For large scrapes, upgrade to Pro or self-host.

---

## Local development

```bash
pip install -r requirements.txt
python api/index.py          # → http://localhost:8000
```

---

## Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/` | Parameter reference & response schema |
| `GET` | `/api/jobs` | Scrape jobs via query-string |
| `POST` | `/api/jobs` | Scrape jobs via JSON body (merges with query-string; body wins) |

---

## Parameters

| Parameter | Type | Default | Description |
|---|---|---|---|
| `site_name` | CSV string / array | all sites | `linkedin`, `indeed`, `zip_recruiter`, `glassdoor`, `google`, `bayt`, `bdjobs` |
| `search_term` | string | — | Job search query. Supports `-exclude` and `"exact phrases"`. Indeed searches title **and** description. |
| `google_search_term` | string | — | Search string for Google Jobs **only** — copy directly from a `google.com` Jobs search box. |
| `location` | string | — | City, state, or country. LinkedIn searches globally; ZipRecruiter is US/Canada only. |
| `distance` | int | `50` | Search radius in **miles**. |
| `job_type` | string | — | `fulltime` · `parttime` · `internship` · `contract` |
| `is_remote` | bool | — | `true` / `false` — remote-only filter. |
| `results_wanted` | int | `15` | Results **per site**. All boards cap at ~1 000 per search. |
| `hours_old` | int | — | Only jobs posted within the last N hours. ZipRecruiter/Glassdoor round up to the nearest day. |
| `easy_apply` | bool | — | Jobs that host their application on the board itself. LinkedIn Easy Apply filter is unreliable. |
| `description_format` | string | `markdown` | `markdown` or `html` |
| `offset` | int | `0` | Skip the first N results (pagination). |
| `linkedin_fetch_description` | bool | `false` | Fetch full LinkedIn description + direct URL. **Much slower** — O(n) extra requests. |
| `linkedin_company_ids` | int CSV / array | — | Filter LinkedIn to specific company IDs. |
| `country_indeed` | string | — | Country for Indeed/Glassdoor (must match exactly, e.g. `USA`, `UK`, `Canada`, `Germany`). |
| `enforce_annual_salary` | bool | `false` | Normalize all salary figures to annual. |
| `proxies` | CSV string / array | — | `user:pass@host:port` or `host:port`. Round-robins across all scrapers. |
| `user_agent` | string | — | Override the default User-Agent header. |
| `ca_cert` | string | — | Path to CA certificate bundle for HTTPS proxies. |

### Indeed limitations (only one of these at a time)
- `hours_old`
- `job_type` / `is_remote`
- `easy_apply`

### LinkedIn limitations (only one of these at a time)
- `hours_old`
- `easy_apply`

---

## Example requests

### GET
```
/api/jobs?search_term=software+engineer&location=San+Francisco,+CA&site_name=indeed,linkedin&results_wanted=20&hours_old=72&country_indeed=USA
```

### POST (JSON body)
```json
{
  "search_term": "data engineer",
  "location": "New York, NY",
  "site_name": ["indeed", "linkedin", "glassdoor"],
  "results_wanted": 30,
  "job_type": "fulltime",
  "is_remote": true,
  "country_indeed": "USA",
  "enforce_annual_salary": true,
  "description_format": "markdown"
}
```

---

## Response schema

```json
{
  "jobs": [
    {
      "site":             "indeed",
      "title":            "Software Engineer",
      "company":          "Acme Corp",
      "company_url":      "https://...",
      "job_url":          "https://...",
      "location": {
        "country": "USA",
        "city":    "San Francisco",
        "state":   "CA"
      },
      "is_remote":        false,
      "description":      "...",
      "job_type":         "fulltime",
      "interval":         "yearly",
      "min_amount":       120000,
      "max_amount":       160000,
      "currency":         "USD",
      "salary_source":    "direct_data",
      "date_posted":      "2026-02-20",
      "emails":           [],
      "job_level":        null,
      "company_industry": "Software"
    }
  ],
  "count": 1,
  "sites": ["indeed", "linkedin"],
  "query": {
    "search_term": "software engineer",
    "location": "San Francisco, CA",
    "results_wanted": 20
  }
}
```

---

## Supported countries (Indeed / Glassdoor)

Argentina, Australia, Austria, Bahrain, Belgium, Brazil, Canada, Chile, China, Colombia, Costa Rica, Czech Republic, Denmark, Ecuador, Egypt, Finland, France, Germany, Greece, Hong Kong, Hungary, India, Indonesia, Ireland, Israel, Italy, Japan, Kuwait, Luxembourg, Malaysia, Mexico, Morocco, Netherlands, New Zealand, Nigeria, Norway, Oman, Pakistan, Panama, Peru, Philippines, Poland, Portugal, Qatar, Romania, Saudi Arabia, Singapore, South Africa, South Korea, Spain, Sweden, Switzerland, Taiwan, Thailand, Turkey, Ukraine, United Arab Emirates, UK, USA, Uruguay, Venezuela, Vietnam

*Countries marked with \* also support Glassdoor.*

---

## Notes

- **Indeed** is currently the most reliable scraper — no rate-limiting.
- **LinkedIn** rate-limits around the 10th page per IP. Use `proxies` for large scrapes.
- All boards cap at approximately **1 000 results** per search.
- Response code `429` means the job board has blocked the IP. Wait, or rotate proxies.
