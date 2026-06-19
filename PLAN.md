# Central IPTV EPG API

## Goal

Build one free central schedule service for many independent single-page IPTV
sites. The service downloads RapidNAS once, converts XMLTV data to stable
`iptv-org` channel IDs, and publishes static JSON through Cloudflare Pages.

## Architecture

```text
RapidNAS XMLTV
      |
      v
GitHub Actions (check every 6 hours, refresh after 18 hours)
      |
      v
Python EPG builder
      |
      v
public/v1/channels/<iptv-org-id>.json
      |
      v
Cloudflare Pages CDN
      |
      v
Independent channel sites
```

The API has no database, server process, paid function, or per-visitor upstream
request. Channel sites only download small static JSON files from the CDN.

## Data contract

- `GET /v1/status.json`: build time, source statistics, and API version.
- `GET /v1/index.json`: published channel list and JSON URLs.
- `GET /v1/channels/<channel-id>.json`: schedule grouped by local date.

Only mappings with status `verified` are published. Fuzzy and ambiguous matches
must be reviewed outside this service.

## Reliability

1. Download to a temporary file.
2. Validate gzip/XML and minimum source statistics.
3. Parse the source once for all mapped channels.
4. Build the complete API in a temporary directory.
5. Replace `public/` only after every validation succeeds.
6. Keep the previous Cloudflare deployment when a workflow fails.
7. Do not publish invented schedules or silently change channel identities.

## Free deployment

- Source repository: one public GitHub repository.
- Scheduler: GitHub Actions.
- Hosting/CDN: one Cloudflare Pages project connected through its GitHub App.
- Vercel is intentionally not used for the central API.

## Delivery stages

1. Build and test locally.
2. Publish the repository to GitHub.
3. Create the Cloudflare Pages project and deploy `public/`.
4. Add GitHub repository secrets for unattended deployments.
5. Connect the `Domkino.ru` site to the public API.
6. Add the IPTV single-page category to Site Factory later.
