# FCC ICFS/IBFS Document Monitor

These scripts watch selected company filing pages and save newly discovered public ICFS/IBFS attachments into local folders.

FCC's official ICFS site can block non-browser script requests. For reliable automated monitoring, the script uses FCC.report as a searchable public index of replicated IBFS/ICFS records and saves the original FCC `[GOV]` page link in each `metadata.json`.

## Streamlit Cloud

Deploy this repo to Streamlit Cloud with:

- Main file path: `streamlit_app.py`
- Python dependencies: `requirements.txt`

In the app, use `Browser ZIP download` mode. Streamlit Cloud server folders are temporary, so ZIP download is the right mode for saving files to the phone or computer that opened the web page.

## Setup

1. Copy `config.example.json` to `config.json`.
2. Edit `companies` with the company names you want to monitor.
3. Run the Streamlit web app:

```powershell
powershell.exe -ExecutionPolicy Bypass -File .\run_streamlit.ps1
```

Or, if Streamlit is already installed:

```powershell
streamlit run .\streamlit_app.py
```

The app opens in your browser. Use "Browser ZIP download" when deploying to Streamlit Cloud so the user can download the collected files to the computer that opened the web page.

Streamlit currently supports Python 3.10 to 3.14. If the local `.venv` is broken, install Python from python.org, delete `C:\workspace\.venv`, and rerun `run_streamlit.ps1`.

On Streamlit Cloud, server-side folders are temporary. Use the ZIP download mode for files you want on the user's computer.

You can also run the Windows GUI:

```powershell
powershell.exe -STA -ExecutionPolicy Bypass -File .\fcc_icfs_monitor_gui.ps1
```

Or run a scan with PowerShell:

```powershell
.\fcc_icfs_monitor.ps1 -Config .\config.json
```

Or run the Python version:

```powershell
python .\fcc_icfs_monitor.py --config .\config.json
```

Use dry-run mode to preview without writing files:

```powershell
.\fcc_icfs_monitor.ps1 -Config .\config.json -DryRun
```

## Output

Files are saved like this:

```text
fcc_documents/
  SpaceX_Services_Inc/
    SES-STA-INTR2023-02678/
      metadata.json
      22890466_Attachment_Application.pdf
```

`fcc_monitor_state.json` records already downloaded attachments so later runs only save new documents.

If a download returns HTML instead of a real PDF, the script saves it as `.html`, records it under `failed_attachments`, and does not mark that attachment as successfully downloaded.

## Scheduling

On Windows Task Scheduler, create a daily task that runs:

```powershell
powershell.exe -ExecutionPolicy Bypass -File C:\workspace\fcc_icfs_monitor.ps1 -Config C:\workspace\config.json
```
