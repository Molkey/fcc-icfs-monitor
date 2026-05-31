#!/usr/bin/env python3
"""
Monitor FCC ICFS/IBFS company filings and download new public documents.

This script uses FCC.report as a searchable index of public IBFS/ICFS filings,
then stores the discovered filing pages and attachment files in local folders.
FCC.report pages include the original FCC [GOV] links; direct FCC downloads may
be blocked by FCC/Akamai for non-browser clients, so mirrored PDF downloads are
used by default for reliability.
"""

from __future__ import annotations

import argparse
import html as html_lib
import html.parser
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Iterable


BASE_URL = "https://fcc.report"
BUSINESS_LIST_URL = f"{BASE_URL}/IBFS/Business-List/"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0 Safari/537.36"
)


class LinkParser(html.parser.HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.links: list[tuple[str, str]] = []
        self._href_stack: list[str] = []
        self._text_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "a":
            return
        attr_map = dict(attrs)
        href = attr_map.get("href")
        if href:
            self._href_stack.append(href)
            self._text_parts = []

    def handle_data(self, data: str) -> None:
        if self._href_stack:
            self._text_parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() != "a" or not self._href_stack:
            return
        href = self._href_stack.pop()
        text = " ".join("".join(self._text_parts).split())
        self.links.append((href, text))
        self._text_parts = []


@dataclass(frozen=True)
class Filing:
    company: str
    file_number: str
    page_url: str
    filed_date: date | None = None
    applicant: str = ""
    filing_type: str = ""


@dataclass(frozen=True)
class Attachment:
    title: str
    page_url: str
    file_url: str
    attachment_key: str


def fetch(url: str, timeout: int = 45, retries: int = 3) -> bytes:
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9,ko;q=0.8",
    }
    req = urllib.request.Request(url, headers=headers)
    last_error: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as response:
                return response.read()
        except (urllib.error.URLError, TimeoutError) as exc:
            last_error = exc
            if attempt < retries:
                time.sleep(2 * attempt)
    raise RuntimeError(f"Failed to fetch {url}: {last_error}")


def parse_links(html: str, base_url: str = BASE_URL) -> list[tuple[str, str]]:
    parser = LinkParser()
    parser.feed(html)
    return [(urllib.parse.urljoin(base_url, href), text) for href, text in parser.links]


def slug(value: str) -> str:
    value = re.sub(r"[^A-Za-z0-9._ -]+", "", value).strip()
    value = re.sub(r"\s+", "_", value)
    return value[:120] or "item"


def load_json(path: Path, default):
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def save_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")


def find_company_pages(company_names: Iterable[str]) -> dict[str, str]:
    html = fetch(BUSINESS_LIST_URL).decode("utf-8", errors="replace")
    links = parse_links(html)
    found: dict[str, str] = {}
    normalized_links = [(text.casefold(), url, text) for url, text in links if "/company/" in url]

    for company in company_names:
        needle = company.casefold()
        exact = [item for item in normalized_links if item[0] == needle]
        contains = [item for item in normalized_links if needle in item[0]]
        match = (exact or contains)[:1]
        if not match:
            print(f"[WARN] company not found in business list: {company}", file=sys.stderr)
            continue
        _, url, matched_name = match[0]
        print(f"[INFO] matched company '{company}' -> '{matched_name}'")
        found[company] = url
    return found


def clean_html(value: str) -> str:
    value = re.sub(r"<br\s*/?>", " ", value, flags=re.IGNORECASE)
    value = re.sub(r"<[^>]+>", " ", value)
    value = html_lib.unescape(value)
    return re.sub(r"\s+", " ", value).strip()


def parse_date(value: str) -> date | None:
    match = re.search(r"\d{4}-\d{2}-\d{2}", value)
    if not match:
        return None
    return datetime.strptime(match.group(0), "%Y-%m-%d").date()


def list_company_filings(company: str, company_url: str, limit: int | None = None) -> list[Filing]:
    html = fetch(company_url).decode("utf-8", errors="replace")
    rows = re.findall(r"<tr>(.*?)</tr>", html, flags=re.IGNORECASE | re.DOTALL)
    filings: list[Filing] = []
    seen: set[str] = set()

    for row in rows:
        link_match = re.search(
            r'<a\s+href=["\'](?P<href>/IBFS/[^"\']+)["\'][^>]*>(?P<file>[^<]+)</a>',
            row,
            flags=re.IGNORECASE,
        )
        if not link_match:
            continue
        file_number = clean_html(link_match.group("file"))
        if not re.match(r"^[A-Z]{2,4}-[A-Z/]+-(?:INTR)?\d{4}-\d{5}$", file_number):
            continue
        if file_number in seen:
            continue

        cells = re.findall(r"<td[^>]*>(.*?)</td>", row, flags=re.IGNORECASE | re.DOTALL)
        filed_date = parse_date(clean_html(cells[0])) if cells else None
        applicant = clean_html(cells[2]) if len(cells) >= 3 else ""
        filing_type = clean_html(cells[4]) if len(cells) >= 5 else ""
        page_url = urllib.parse.urljoin(BASE_URL, link_match.group("href"))

        seen.add(file_number)
        filings.append(
            Filing(
                company=company,
                file_number=file_number,
                page_url=page_url,
                filed_date=filed_date,
                applicant=applicant,
                filing_type=filing_type,
            )
        )
        if limit is not None and len(filings) >= limit:
            break

    return filings


def list_filings(company: str, company_url: str, limit: int) -> list[Filing]:
    return list_company_filings(company, company_url, limit)


def list_attachments(filing: Filing) -> list[Attachment]:
    html = fetch(filing.page_url).decode("utf-8", errors="replace")
    attachments: list[Attachment] = []
    seen: set[str] = set()

    for url, text in parse_links(html):
        pattern = rf"/IBFS/{re.escape(filing.file_number)}/(\d+)$"
        match = re.search(pattern, urllib.parse.urlparse(url).path)
        if not match:
            continue
        key = match.group(1)
        if key in seen:
            continue
        seen.add(key)
        title = text or f"Attachment {key}"
        attachments.append(
            Attachment(
                title=title,
                page_url=url,
                file_url=f"{url}.pdf",
                attachment_key=key,
            )
        )

    return attachments


def download_file(url: str, path: Path) -> tuple[bool, Path, str | None]:
    data = fetch(url)
    path.parent.mkdir(parents=True, exist_ok=True)
    if data.startswith(b"%PDF-"):
        path.write_bytes(data)
        return True, path, None

    html_path = path.with_suffix(".html")
    html_path.write_bytes(data)
    return False, html_path, "Downloaded content was not a PDF. Saved diagnostic HTML instead."


def save_filing_metadata(filing: Filing, attachments: list[Attachment], out_dir: Path) -> None:
    metadata = {
        "company": filing.company,
        "file_number": filing.file_number,
        "filed_date": filing.filed_date.isoformat() if filing.filed_date else None,
        "applicant": filing.applicant,
        "filing_type": filing.filing_type,
        "source_page": filing.page_url,
        "official_gov_page": f"{filing.page_url}/GOV",
        "attachments": [attachment.__dict__ for attachment in attachments],
    }
    save_json(out_dir / "metadata.json", metadata)


def run(config: dict, dry_run: bool = False) -> int:
    companies = config.get("companies", [])
    if not companies:
        print("No companies configured. Add names to config.json.", file=sys.stderr)
        return 2

    output_dir = Path(config.get("output_dir", "fcc_documents"))
    state_path = Path(config.get("state_file", "fcc_monitor_state.json"))
    max_filings = int(config.get("max_filings_per_company", 25))
    pause_seconds = float(config.get("pause_seconds", 1.5))
    download = bool(config.get("download_attachments", True))

    state = load_json(
        state_path,
        {"downloaded_attachments": {}, "failed_attachments": {}, "seen_filings": {}},
    )
    downloaded = state.setdefault("downloaded_attachments", {})
    failed = state.setdefault("failed_attachments", {})
    seen_filings = state.setdefault("seen_filings", {})

    company_pages = find_company_pages(companies)
    new_count = 0

    for company, company_url in company_pages.items():
        print(f"[INFO] scanning {company}: {company_url}")
        filings = list_filings(company, company_url, max_filings)
        company_key = slug(company)

        for filing in filings:
            filing_key = f"{company}|{filing.file_number}"
            if filing_key not in seen_filings:
                seen_filings[filing_key] = filing.page_url
                print(f"[NEW] filing {filing.file_number}")

            attachments = list_attachments(filing)
            filing_dir = output_dir / company_key / slug(filing.file_number)
            if not dry_run:
                save_filing_metadata(filing, attachments, filing_dir)

            for attachment in attachments:
                attachment_key = f"{filing.file_number}|{attachment.attachment_key}"
                if attachment_key in downloaded:
                    continue
                new_count += 1
                filename = f"{attachment.attachment_key}_{slug(attachment.title)}.pdf"
                destination = filing_dir / filename
                print(f"[NEW] attachment {filing.file_number} {attachment.title} -> {destination}")
                download_ok = False
                saved_path = destination
                download_error = None
                if download and not dry_run:
                    download_ok, saved_path, download_error = download_file(
                        attachment.file_url, destination
                    )
                    if not download_ok:
                        print(
                            f"[WARN] {filing.file_number} {attachment.attachment_key}: "
                            f"{download_error}",
                            file=sys.stderr,
                        )
                elif not download:
                    download_ok = True
                if not dry_run:
                    if download_ok:
                        downloaded[attachment_key] = {
                            "company": company,
                            "file_number": filing.file_number,
                            "title": attachment.title,
                            "url": attachment.file_url,
                            "path": str(saved_path),
                        }
                        failed.pop(attachment_key, None)
                    else:
                        failed[attachment_key] = {
                            "company": company,
                            "file_number": filing.file_number,
                            "title": attachment.title,
                            "url": attachment.file_url,
                            "diagnostic_path": str(saved_path),
                            "error": download_error,
                            "checked_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                        }
                    save_json(state_path, state)
                if not dry_run:
                    time.sleep(pause_seconds)

    if not dry_run:
        save_json(state_path, state)
    print(f"[DONE] new attachments: {new_count}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Monitor FCC ICFS/IBFS filings by company.")
    parser.add_argument("--config", default="config.json", help="Path to config JSON.")
    parser.add_argument("--dry-run", action="store_true", help="Scan without writing or downloading.")
    args = parser.parse_args()

    config_path = Path(args.config)
    if not config_path.exists():
        print(f"Config not found: {config_path}", file=sys.stderr)
        print("Copy config.example.json to config.json and edit the company list.", file=sys.stderr)
        return 2

    config = load_json(config_path, {})
    return run(config, dry_run=args.dry_run)


if __name__ == "__main__":
    raise SystemExit(main())
