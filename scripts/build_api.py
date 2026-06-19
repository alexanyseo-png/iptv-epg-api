from __future__ import annotations

import argparse
import csv
import gzip
import json
import os
import shutil
import tempfile
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from urllib.request import Request, urlopen
from xml.etree.ElementTree import ParseError, iterparse
from zoneinfo import ZoneInfo


RAPIDNAS_URL = "http://epg.rapidnas.org/xmltv/epg_lite.xml.gz"
ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MAPPING = ROOT / "config" / "channel-mappings.csv"
DEFAULT_CACHE = ROOT / "cache" / "rapidnas-epg-lite.xml.gz"
DEFAULT_PUBLIC = ROOT / "public"
MOSCOW = ZoneInfo("Europe/Moscow")
API_VERSION = "1"
DEFAULT_DAYS = 3
MIN_SOURCE_BYTES = 1_000_000
MIN_XML_CHANNELS = 1_000
MIN_XML_PROGRAMMES = 100_000


@dataclass(frozen=True)
class Mapping:
    rapidnas_id: str
    rapidnas_name: str
    channel_id: str
    channel_name: str
    programme_count: int


def utc_now() -> datetime:
    return datetime.now().astimezone()


def parse_xmltv_datetime(value: str) -> datetime:
    value = value.strip()
    if len(value) < 20:
        raise ValueError(f"Unsupported XMLTV datetime: {value}")
    return datetime.strptime(value[:20], "%Y%m%d%H%M%S %z")


def child_text(element, child_name: str) -> str | None:
    for child in element:
        if child.tag.rsplit("}", 1)[-1] == child_name:
            text = (child.text or "").strip()
            return text or None
    return None


def load_mappings(path: Path) -> list[Mapping]:
    with path.open("r", encoding="utf-8-sig", newline="") as source:
        verified = [
            Mapping(
                rapidnas_id=row["rapidnas_id"].strip(),
                rapidnas_name=row["rapidnas_name"].strip(),
                channel_id=row["iptv_org_id"].strip(),
                channel_name=row["iptv_org_name"].strip(),
                programme_count=int(row.get("programme_count") or 0),
            )
            for row in csv.DictReader(source)
            if row.get("status", "").strip().lower() == "verified"
            and row.get("rapidnas_id", "").strip()
            and row.get("iptv_org_id", "").strip()
        ]
    if not verified:
        raise ValueError("No verified channel mappings found")
    best_by_channel: dict[str, Mapping] = {}
    for mapping in verified:
        current = best_by_channel.get(mapping.channel_id)
        if current is None or mapping.programme_count > current.programme_count:
            best_by_channel[mapping.channel_id] = mapping
    return list(best_by_channel.values())


def download_source(url: str, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    request = Request(url, headers={"User-Agent": "IPTV-EPG-API/1.0"})
    descriptor, temporary_name = tempfile.mkstemp(
        dir=target.parent,
        prefix="rapidnas-",
        suffix=".tmp",
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as temporary:
            with urlopen(request, timeout=180) as response:
                while chunk := response.read(1024 * 1024):
                    temporary.write(chunk)
        if temporary_path.stat().st_size < MIN_SOURCE_BYTES:
            raise ValueError("Downloaded RapidNAS source is unexpectedly small")
        with gzip.open(temporary_path, "rb") as source:
            source.read(1)
        temporary_path.replace(target)
    except Exception:
        temporary_path.unlink(missing_ok=True)
        raise


def is_recent(public_dir: Path, minimum_age_hours: float, now: datetime) -> bool:
    status_path = public_dir / "v1" / "status.json"
    if minimum_age_hours <= 0 or not status_path.exists():
        return False
    status = json.loads(status_path.read_text(encoding="utf-8"))
    generated_at = datetime.fromisoformat(status["generatedAt"])
    return now - generated_at < timedelta(hours=minimum_age_hours)


def parse_source(
    source_path: Path,
    mappings: list[Mapping],
    days: int,
    now: datetime,
) -> tuple[dict[str, list[dict]], dict[str, object]]:
    mapping_by_rapidnas = {row.rapidnas_id: row for row in mappings}
    target_ids = set(mapping_by_rapidnas)
    programmes_by_channel: dict[str, list[dict]] = defaultdict(list)
    discovered_target_ids: set[str] = set()
    total_channels = 0
    total_programmes = 0
    start_boundary = now.astimezone(MOSCOW).replace(
        hour=0,
        minute=0,
        second=0,
        microsecond=0,
    )
    end_boundary = start_boundary + timedelta(days=days)

    try:
        with gzip.open(source_path, "rb") as source:
            for _, element in iterparse(source, events=("end",)):
                tag = element.tag.rsplit("}", 1)[-1]
                if tag == "channel":
                    total_channels += 1
                    channel_id = element.attrib.get("id", "")
                    if channel_id in target_ids:
                        discovered_target_ids.add(channel_id)
                    element.clear()
                    continue
                if tag != "programme":
                    continue
                total_programmes += 1
                rapidnas_id = element.attrib.get("channel", "")
                if rapidnas_id not in target_ids:
                    element.clear()
                    continue
                start = parse_xmltv_datetime(element.attrib["start"]).astimezone(MOSCOW)
                stop_raw = element.attrib.get("stop")
                stop = (
                    parse_xmltv_datetime(stop_raw).astimezone(MOSCOW)
                    if stop_raw
                    else None
                )
                if start_boundary <= start < end_boundary:
                    channel_id = mapping_by_rapidnas[rapidnas_id].channel_id
                    programmes_by_channel[channel_id].append(
                        {
                            "start": start.isoformat(),
                            "stop": stop.isoformat() if stop else None,
                            "title": child_text(element, "title") or "Без названия",
                            "description": child_text(element, "desc"),
                            "category": child_text(element, "category"),
                        }
                    )
                element.clear()
    except (OSError, ParseError) as exc:
        raise ValueError(f"Invalid RapidNAS gzip/XML: {exc}") from exc

    minimum_mapped = max(1, int(len(mappings) * 0.7))
    if total_channels < MIN_XML_CHANNELS:
        raise ValueError(f"RapidNAS channel count is too small: {total_channels}")
    if total_programmes < MIN_XML_PROGRAMMES:
        raise ValueError(f"RapidNAS programme count is too small: {total_programmes}")
    if len(discovered_target_ids) < minimum_mapped:
        raise ValueError(
            "Too few mapped RapidNAS channels were found: "
            f"{len(discovered_target_ids)} < {minimum_mapped}"
        )
    for programmes in programmes_by_channel.values():
        programmes.sort(key=lambda item: item["start"])
    return programmes_by_channel, {
        "sourceChannels": total_channels,
        "sourceProgrammes": total_programmes,
        "mappedChannelsFound": len(discovered_target_ids),
    }


def group_programmes(programmes: list[dict]) -> list[dict]:
    grouped: dict[str, list[dict]] = defaultdict(list)
    for programme in programmes:
        grouped[programme["start"][:10]].append(programme)
    return [
        {"date": date, "programs": grouped[date]}
        for date in sorted(grouped)
    ]


def write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def build_publication(
    destination: Path,
    mappings: list[Mapping],
    programmes_by_channel: dict[str, list[dict]],
    source_stats: dict[str, object],
    generated_at: datetime,
    days: int,
) -> None:
    channels_dir = destination / "v1" / "channels"
    index_channels = []
    channels_with_programmes = 0
    for mapping in sorted(mappings, key=lambda row: row.channel_id.casefold()):
        programmes = programmes_by_channel.get(mapping.channel_id, [])
        if programmes:
            channels_with_programmes += 1
        relative_url = f"/v1/channels/{mapping.channel_id}.json"
        payload = {
            "apiVersion": API_VERSION,
            "channel": {
                "id": mapping.channel_id,
                "name": mapping.channel_name,
                "rapidnasId": mapping.rapidnas_id,
                "rapidnasName": mapping.rapidnas_name,
            },
            "timezone": "Europe/Moscow",
            "source": "RapidNAS XMLTV",
            "generatedAt": generated_at.isoformat(),
            "available": bool(programmes),
            "programmeCount": len(programmes),
            "days": group_programmes(programmes),
        }
        write_json(channels_dir / f"{mapping.channel_id}.json", payload)
        index_channels.append(
            {
                "id": mapping.channel_id,
                "name": mapping.channel_name,
                "url": relative_url,
                "available": bool(programmes),
                "programmeCount": len(programmes),
            }
        )

    status = {
        "apiVersion": API_VERSION,
        "status": "ok",
        "generatedAt": generated_at.isoformat(),
        "timezone": "Europe/Moscow",
        "scheduleDays": days,
        "publishedChannels": len(mappings),
        "channelsWithProgrammes": channels_with_programmes,
        **source_stats,
    }
    write_json(destination / "v1" / "status.json", status)
    write_json(
        destination / "v1" / "index.json",
        {
            "apiVersion": API_VERSION,
            "generatedAt": generated_at.isoformat(),
            "channelCount": len(index_channels),
            "channels": index_channels,
        },
    )
    (destination / "_headers").write_text(
        "/v1/*\n"
        "  Access-Control-Allow-Origin: *\n"
        "  Access-Control-Allow-Methods: GET, HEAD, OPTIONS\n"
        "  Cache-Control: public, max-age=900, s-maxage=21600, "
        "stale-while-revalidate=86400\n",
        encoding="utf-8",
    )
    (destination / "index.html").write_text(
        "<!doctype html><html lang=\"en\"><meta charset=\"utf-8\">"
        "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
        "<title>IPTV EPG API</title><body><main>"
        "<h1>IPTV EPG API</h1><p>Static schedule data is available under "
        "<a href=\"/v1/index.json\">/v1/index.json</a>.</p>"
        "</main></body></html>\n",
        encoding="utf-8",
    )


def replace_publication(staged_public: Path, public_dir: Path) -> None:
    backup = public_dir.with_name(f"{public_dir.name}.backup")
    if backup.exists():
        shutil.rmtree(backup)
    if public_dir.exists():
        public_dir.replace(backup)
    try:
        staged_public.replace(public_dir)
    except Exception:
        if backup.exists() and not public_dir.exists():
            backup.replace(public_dir)
        raise
    if backup.exists():
        shutil.rmtree(backup)


def build(
    mapping_path: Path,
    source_path: Path,
    public_dir: Path,
    days: int,
    now: datetime,
) -> dict[str, object]:
    mappings = load_mappings(mapping_path)
    programmes, source_stats = parse_source(source_path, mappings, days, now)
    public_dir.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=public_dir.parent) as temporary:
        staged_public = Path(temporary) / "public"
        staged_public.mkdir()
        build_publication(
            staged_public,
            mappings,
            programmes,
            source_stats,
            now,
            days,
        )
        status = json.loads(
            (staged_public / "v1" / "status.json").read_text(encoding="utf-8")
        )
        replace_publication(staged_public, public_dir)
    return status


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mapping", type=Path, default=DEFAULT_MAPPING)
    parser.add_argument("--cache", type=Path, default=DEFAULT_CACHE)
    parser.add_argument("--public", type=Path, default=DEFAULT_PUBLIC)
    parser.add_argument("--source", type=Path)
    parser.add_argument("--days", type=int, default=DEFAULT_DAYS)
    parser.add_argument("--min-age-hours", type=float, default=0)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    if not 1 <= args.days <= 14:
        parser.error("--days must be between 1 and 14")

    now = utc_now()
    if not args.force and is_recent(args.public, args.min_age_hours, now):
        print("EPG publication is still fresh; update skipped")
        return 0

    source_path = args.source or args.cache
    if args.source is None:
        print(f"Downloading {RAPIDNAS_URL}")
        download_source(RAPIDNAS_URL, source_path)
    status = build(args.mapping, source_path, args.public, args.days, now)
    print(
        "EPG API built: "
        f"channels={status['publishedChannels']}, "
        f"with_programmes={status['channelsWithProgrammes']}, "
        f"source_programmes={status['sourceProgrammes']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
