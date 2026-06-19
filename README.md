# IPTV EPG API

Static JSON schedule API generated from RapidNAS XMLTV and addressed by
canonical `iptv-org` channel IDs.

## Local commands

```powershell
python scripts/build_api.py --force
python -m unittest discover -s tests -p "test_*.py"
python -m http.server 8090 --bind 127.0.0.1 --directory public
```

Open:

- `http://127.0.0.1:8090/v1/status.json`
- `http://127.0.0.1:8090/v1/index.json`
- `http://127.0.0.1:8090/v1/channels/Domkino.ru.json`

Normal scheduled execution:

```powershell
python scripts/build_api.py --min-age-hours 18
```

The process exits successfully without rebuilding when the current publication
is younger than the configured minimum age.

## Mapping

`config/channel-mappings.csv` is the release allowlist. Only rows with
`status=verified` are accepted. Update mappings in the parent IPTV tooling,
review them, and then copy the verified result into this project.

## Deployment

GitHub Actions checks every six hours. A build is performed only after the last
successful publication becomes at least 18 hours old. The generated `public/`
directory is deployed to the dedicated Cloudflare Pages project.

See `PLAN.md` for architecture and reliability decisions.

