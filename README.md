# Job API

A Vercel serverless API that aggregates job postings from multiple boards and returns them as JSON. Wraps [jobspy](https://github.com/speedyapply/JobSpy) for Indeed, LinkedIn, and Naukri, and calls the public REST APIs of RemoteOK, Arbeitnow, Remotive, and Jobicy directly.


## Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/` | Parameter reference & response schema |
| `GET` | `/api/jobs` | Scrape jobs via query-string |
| `POST` | `/api/jobs` | Scrape jobs via JSON body (merges with query-string; body wins) |
| `GET` | `/api/sites` | List all supported job boards |
| `GET` | `/api/health` | Health check |

---

## Supported job boards

| Board | Value | Source | Notes |
|---|---|---|---|
| Indeed | `indeed` | jobspy | Most reliable |
| LinkedIn | `linkedin` | jobspy | Rate-limits ~page 10 per IP |
| Naukri | `naukri` | jobspy | India-focused |
| RemoteOK | `remoteok` | Public API | Remote-only |
| Arbeitnow | `arbeitnow` | Public API | Europe/Germany-focused |
| Remotive | `remotive` | Public API | Remote-only |
| Jobicy | `jobicy` | Public API | Remote-only |

---

## Parameters

| Parameter | Type | Default | Description |
|---|---|---|---|
| `site_name` | CSV string / array | all sites | `indeed`, `linkedin`, `naukri`, `remoteok`, `arbeitnow`, `remotive`, `jobicy` |
| `search_term` | string | — | Job search query. Supports `-exclude` and `"exact phrases"`. |
| `location` | string | — | City, state, or country. Used for Indeed/LinkedIn/Naukri. For Jobicy/Remotive the last token (e.g. `"USA"` from `"New York, NY, USA"`) is used as a country filter. |
| `distance` | int | `50` | Search radius in **miles** (jobspy boards only). |
| `job_type` | string | — | `fulltime` · `parttime` · `internship` · `contract` |
| `is_remote` | bool | — | `true` / `false` — remote-only filter. RemoteOK/Remotive/Jobicy always return remote jobs and are excluded when `is_remote=false`. |
| `results_wanted` | int | `15` | Results per site. |
| `hours_old` | int | — | Only jobs posted within the last N hours. |
| `easy_apply` | bool | — | Jobs that host their application on the board itself (jobspy boards only). |
| `description_format` | string | `markdown` | `markdown` or `html` (jobspy boards only). |
| `offset` | int | `0` | Skip the first N results (pagination). |
| `linkedin_fetch_description` | bool | `false` | Fetch full LinkedIn description + direct URL. **Much slower** — O(n) extra requests. |
| `linkedin_company_ids` | int CSV / array | — | Filter LinkedIn to specific company IDs. |
| `country_indeed` | string | — | Country for Indeed (e.g. `USA`, `UK`, `Canada`, `Germany`). Also used as the `geo` filter for Jobicy and as a location filter for Remotive. |
| `enforce_annual_salary` | bool | `false` | Normalize all salary figures to annual (jobspy boards only). |
| `proxies` | CSV string / array | — | `user:pass@host:port` or `host:port`. Round-robins across scrapers. |
| `user_agent` | string | — | Override the default User-Agent header. |

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
https://j0b-api.vercel.app/api/jobs?site_name=indeed,remoteok,remotive&search_term=software+engineer&results_wanted=10&is_remote=true
```

### POST (JSON body)
```json
{
  "search_term": "data engineer",
  "location": "New York, NY, USA",
  "site_name": ["indeed", "linkedin", "remoteok", "jobicy"],
  "results_wanted": 20,
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
      "salary": {
        "min_amount":  120000,
        "max_amount":  160000,
        "interval":    "yearly",
        "currency":    "USD"
      },
      "date_posted":      "2026-02-20",
      "emails":           [],
      "skills":           [],
      "job_level":        null,
      "company_industry": "Software"
    }
  ],
  "count": 1,
  "sites": ["indeed", "remoteok"],
  "query": {
    "search_term": "software engineer",
    "results_wanted": 20
  }
}
```

---

## Notes

- **Indeed** and **LinkedIn** are scraped via jobspy. LinkedIn rate-limits around the 10th page per IP — use `proxies` for large scrapes.
- **RemoteOK**, **Remotive**, and **Jobicy** are remote-only boards and are automatically skipped when `is_remote=false`.
- **Arbeitnow** is focused on European/German jobs.
- Public API boards (**remoteok**, **arbeitnow**, **remotive**, **jobicy**) require no authentication and don't need proxies.
- Response code `429` means the job board has blocked the IP. Wait, or rotate proxies.


