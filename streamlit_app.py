from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
import io
import time
import zipfile

import streamlit as st

from fcc_icfs_monitor import (
    Attachment,
    Filing,
    download_file,
    find_company_pages,
    list_attachments,
    list_company_filings,
    load_json,
    save_filing_metadata,
    save_json,
    slug,
)


DEFAULT_COMPANIES = {
    "SpaceX": "SpaceX Services Inc",
    "Amazon / Project Kuiper": "Kuiper Systems Llc",
    "AST SpaceMobile": "Ast Science",
}
FCC_REPORT_LEGACY_CUTOFF = date(2023, 6, 14)


def default_output_dir() -> str:
    return str(Path.cwd() / "fcc_documents")


def state_path_for(output_dir: Path) -> Path:
    return output_dir / "fcc_streamlit_state.json"


def filter_by_date(filings: list[Filing], start_date: date, end_date: date) -> list[Filing]:
    return [
        filing
        for filing in filings
        if filing.filed_date is not None and start_date <= filing.filed_date <= end_date
    ]


def scan_filings(
    companies: list[str], start_date: date, end_date: date, max_per_company: int
) -> tuple[list[Filing], dict]:
    company_pages = find_company_pages(companies)
    rows: list[Filing] = []
    raw_count = 0
    for company, url in company_pages.items():
        filings = list_company_filings(company, url, max_per_company)
        raw_count += len(filings)
        rows.extend(filter_by_date(filings, start_date, end_date))
    return rows, {
        "requested_companies": len(companies),
        "matched_companies": len(company_pages),
        "checked_filings": raw_count,
        "matched_filings": len(rows),
    }


def fetch_attachment_bytes(attachment: Attachment) -> tuple[bool, bytes, str | None]:
    from fcc_icfs_monitor import fetch

    data = fetch(attachment.file_url)
    if data.startswith(b"%PDF-"):
        return True, data, None
    return False, data, "Downloaded content was not a PDF. Diagnostic HTML included instead."


def build_download_zip(filings: list[Filing], progress_slot, log_slot) -> dict:
    zip_buffer = io.BytesIO()
    attempted = 0
    pdf_success = 0
    html_count = 0
    logs: list[str] = []

    with zipfile.ZipFile(zip_buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        total_filings = len(filings)
        for index, filing in enumerate(filings, start=1):
            progress_slot.progress(
                index / max(total_filings, 1),
                text=f"Checking attachments for {filing.file_number}",
            )
            attachments = list_attachments(filing)

            metadata_lines = [
                f"company: {filing.company}",
                f"file_number: {filing.file_number}",
                f"filed_date: {filing.filed_date.isoformat() if filing.filed_date else ''}",
                f"applicant: {filing.applicant}",
                f"type: {filing.filing_type}",
                f"source_page: {filing.page_url}",
                f"official_gov_page: {filing.page_url}/GOV",
                "",
                "attachments:",
            ]
            for attachment in attachments:
                metadata_lines.append(f"- {attachment.title}: {attachment.file_url}")

            base_folder = f"{slug(filing.company)}/{slug(filing.file_number)}"
            archive.writestr(f"{base_folder}/metadata.txt", "\n".join(metadata_lines))

            for attachment in attachments:
                attempted += 1
                filename_base = f"{attachment.attachment_key}_{slug(attachment.title)}"
                data_ok, data, error = fetch_attachment_bytes(attachment)
                if data_ok:
                    pdf_success += 1
                    archive.writestr(f"{base_folder}/{filename_base}.pdf", data)
                    logs.append(f"PDF: {filing.file_number} / {attachment.title}")
                else:
                    html_count += 1
                    archive.writestr(f"{base_folder}/{filename_base}.html", data)
                    archive.writestr(
                        f"{base_folder}/{filename_base}.error.txt",
                        error or "Downloaded content was not a PDF.",
                    )
                    logs.append(f"HTML: {filing.file_number} / {attachment.title}")
                log_slot.text("\n".join(logs[-12:]))
                time.sleep(0.3)

    progress_slot.progress(1.0, text="ZIP is ready")
    zip_buffer.seek(0)
    return {
        "data": zip_buffer.getvalue(),
        "attempted": attempted,
        "pdf_success": pdf_success,
        "html_count": html_count,
    }


def download_selected_to_server_folder(
    filings: list[Filing],
    output_dir: Path,
    overwrite_failed: bool,
    progress_slot,
    log_slot,
) -> dict:
    state_path = state_path_for(output_dir)
    state = load_json(
        state_path,
        {"downloaded_attachments": {}, "failed_attachments": {}, "seen_filings": {}},
    )
    downloaded = state.setdefault("downloaded_attachments", {})
    failed = state.setdefault("failed_attachments", {})
    seen_filings = state.setdefault("seen_filings", {})

    attempted = 0
    success = 0
    failed_count = 0
    skipped = 0
    logs: list[str] = []

    total_filings = len(filings)
    for index, filing in enumerate(filings, start=1):
        progress_slot.progress(
            index / max(total_filings, 1), text=f"Checking {filing.file_number}"
        )
        filing_key = f"{filing.company}|{filing.file_number}"
        seen_filings[filing_key] = filing.page_url

        attachments = list_attachments(filing)
        filing_dir = output_dir / slug(filing.company) / slug(filing.file_number)
        save_filing_metadata(filing, attachments, filing_dir)

        for attachment in attachments:
            attachment_key = f"{filing.file_number}|{attachment.attachment_key}"
            if attachment_key in downloaded:
                skipped += 1
                continue
            if attachment_key in failed and not overwrite_failed:
                skipped += 1
                continue

            attempted += 1
            filename = f"{attachment.attachment_key}_{slug(attachment.title)}.pdf"
            destination = filing_dir / filename
            ok, saved_path, error = download_file(attachment.file_url, destination)
            if ok:
                success += 1
                downloaded[attachment_key] = {
                    "company": filing.company,
                    "file_number": filing.file_number,
                    "title": attachment.title,
                    "url": attachment.file_url,
                    "path": str(saved_path),
                }
                failed.pop(attachment_key, None)
                logs.append(f"OK: {filing.file_number} / {attachment.title}")
            else:
                failed_count += 1
                failed[attachment_key] = {
                    "company": filing.company,
                    "file_number": filing.file_number,
                    "title": attachment.title,
                    "url": attachment.file_url,
                    "diagnostic_path": str(saved_path),
                    "error": error,
                    "checked_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                }
                logs.append(f"HTML: {filing.file_number} / {attachment.title}")

            save_json(state_path, state)
            log_slot.text("\n".join(logs[-12:]))
            time.sleep(0.5)

    save_json(state_path, state)
    progress_slot.progress(1.0, text="Done")
    return {
        "attempted": attempted,
        "success": success,
        "failed": failed_count,
        "skipped": skipped,
        "state_path": state_path,
    }


st.set_page_config(page_title="FCC ICFS Monitor", layout="wide")
st.title("FCC ICFS/IBFS Document Monitor")

with st.sidebar:
    st.header("Search")
    selected_labels = st.multiselect(
        "Companies",
        options=list(DEFAULT_COMPANIES.keys()),
        default=list(DEFAULT_COMPANIES.keys()),
    )
    custom_companies = st.text_area("Additional company names", placeholder="One per line")

    today = date.today()
    start_date = st.date_input("Start date", date(2023, 1, 1))
    end_date = st.date_input("End date", today)
    max_per_company = st.number_input(
        "Max filings per company", min_value=1, max_value=1000, value=200
    )

    delivery_mode = st.radio(
        "Delivery mode",
        ["Browser ZIP download", "Save to server/local folder"],
        index=0,
    )
    output_dir = Path(st.text_input("Server/local output folder", default_output_dir())).expanduser()
    overwrite_failed = st.checkbox("Retry previous failures", value=True)

companies = [DEFAULT_COMPANIES[label] for label in selected_labels]
companies.extend(line.strip() for line in custom_companies.splitlines() if line.strip())

col1, col2 = st.columns([1, 1])
with col1:
    scan_button = st.button("Find filings in date range", type="primary", disabled=not companies)
with col2:
    st.caption("For Streamlit Cloud, use Browser ZIP download.")

if end_date > FCC_REPORT_LEGACY_CUTOFF:
    st.warning(
        "This app currently searches FCC.report's legacy IBFS mirror. That mirror appears "
        "to stop around June 2023 for many ICFS/IBFS filing lists, so 2024+ ICFS filings "
        "may not appear here even though they exist on FCC systems and in FCC public notices."
    )

if start_date > end_date:
    st.error("Start date cannot be later than end date.")
    st.stop()

if "filings" not in st.session_state:
    st.session_state.filings = []
if "search_attempted" not in st.session_state:
    st.session_state.search_attempted = False
if "search_summary" not in st.session_state:
    st.session_state.search_summary = None

if scan_button:
    with st.spinner("Searching FCC.report company pages..."):
        try:
            filings_result, search_summary = scan_filings(
                companies, start_date, end_date, int(max_per_company)
            )
            st.session_state.filings = filings_result
            st.session_state.search_summary = search_summary
            st.session_state.search_attempted = True
            st.session_state.pop("zip_data", None)
            st.session_state.pop("zip_name", None)
        except Exception as exc:
            st.error(f"Search failed: {exc}")
            st.stop()

filings: list[Filing] = st.session_state.filings

st.subheader("Results")
if st.session_state.search_summary:
    summary = st.session_state.search_summary
    st.caption(
        f"Matched companies: {summary['matched_companies']}/{summary['requested_companies']} | "
        f"Checked filings: {summary['checked_filings']} | "
        f"Filings in date range: {summary['matched_filings']}"
    )

if filings:
    st.dataframe(
        [
            {
                "date": filing.filed_date.isoformat() if filing.filed_date else "",
                "company": filing.company,
                "file_number": filing.file_number,
                "applicant": filing.applicant,
                "type": filing.filing_type,
                "url": filing.page_url,
            }
            for filing in filings
        ],
        use_container_width=True,
        hide_index=True,
    )
elif st.session_state.search_attempted:
    st.warning(
        "No filings were found in that date range. Try widening the dates or increasing "
        "Max filings per company."
    )
    if end_date > FCC_REPORT_LEGACY_CUTOFF:
        st.info(
            "For 2024 SpaceX examples, FCC public documents reference ICFS file numbers "
            "such as SAT-AMD-20240322-00061, SAT-MOD-20240423-00089, and "
            "SAT-AMD-20241017-00228. These are not reliably present in the FCC.report "
            "legacy IBFS mirror used by this version of the app."
        )
else:
    st.info("Choose companies and dates, then click Find filings in date range.")

prepare_button = st.button("Prepare attachments", disabled=not filings)
if prepare_button:
    progress_slot = st.empty()
    log_slot = st.empty()
    if delivery_mode == "Browser ZIP download":
        try:
            zip_summary = build_download_zip(filings, progress_slot, log_slot)
        except Exception as exc:
            st.error(f"ZIP preparation failed: {exc}")
            st.stop()

        st.session_state.zip_data = zip_summary["data"]
        st.session_state.zip_name = f"fcc_icfs_{start_date}_{end_date}.zip"
        st.success(
            "ZIP ready: "
            f"{zip_summary['attempted']} attachments, "
            f"{zip_summary['pdf_success']} PDFs, "
            f"{zip_summary['html_count']} diagnostic HTML files"
        )
    else:
        output_dir.mkdir(parents=True, exist_ok=True)
        try:
            summary = download_selected_to_server_folder(
                filings, output_dir, overwrite_failed, progress_slot, log_slot
            )
        except Exception as exc:
            st.error(f"Download failed: {exc}")
            st.stop()

        st.success(
            "Done: "
            f"{summary['attempted']} attempted, "
            f"{summary['success']} PDFs, "
            f"{summary['failed']} HTML/failures, "
            f"{summary['skipped']} skipped"
        )
        st.write(f"Output folder: `{output_dir}`")
        st.write(f"State file: `{summary['state_path']}`")

if st.session_state.get("zip_data"):
    st.download_button(
        "Download ZIP",
        data=st.session_state.zip_data,
        file_name=st.session_state.zip_name,
        mime="application/zip",
        type="primary",
    )

st.warning(
    "Some FCC/FCC.report attachment links return ICFS login HTML instead of a real PDF. "
    "Those files are included as .html diagnostics instead of being mislabeled as PDFs."
)
