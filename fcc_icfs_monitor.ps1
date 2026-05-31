param(
    [string]$Config = ".\config.json",
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"
$BaseUrl = "https://fcc.report"
$BusinessListUrl = "$BaseUrl/IBFS/Business-List/"
$Headers = @{
    "User-Agent" = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0 Safari/537.36"
    "Accept" = "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
    "Accept-Language" = "en-US,en;q=0.9,ko;q=0.8"
}

function Get-TextFileJson($Path, $Default) {
    if (Test-Path -LiteralPath $Path) {
        return (Get-Content -LiteralPath $Path -Raw -Encoding UTF8 | ConvertFrom-Json)
    }
    return $Default
}

function Save-TextFileJson($Path, $Data) {
    $parent = Split-Path -Parent $Path
    if ($parent) {
        New-Item -ItemType Directory -Force -Path $parent | Out-Null
    }
    $Data | ConvertTo-Json -Depth 10 | Set-Content -LiteralPath $Path -Encoding UTF8
}

function ConvertTo-Slug($Value) {
    $clean = $Value -replace '[^A-Za-z0-9._ -]+', ''
    $clean = $clean.Trim() -replace '\s+', '_'
    if ($clean.Length -gt 120) {
        $clean = $clean.Substring(0, 120)
    }
    if ([string]::IsNullOrWhiteSpace($clean)) {
        return "item"
    }
    return $clean
}

function Get-UrlContent($Url) {
    $attempt = 0
    while ($attempt -lt 3) {
        try {
            return (Invoke-WebRequest -Uri $Url -Headers $Headers -MaximumRedirection 5 -UseBasicParsing).Content
        } catch {
            $attempt += 1
            if ($attempt -ge 3) {
                throw
            }
            Start-Sleep -Seconds (2 * $attempt)
        }
    }
}

function Save-RemotePdf($Url, $Destination) {
    $tempPath = "$Destination.tmp"
    Invoke-WebRequest -Uri $Url -Headers $Headers -MaximumRedirection 5 -UseBasicParsing -OutFile $tempPath
    $bytes = Get-Content -LiteralPath $tempPath -Encoding Byte -TotalCount 8
    $isPdf = $bytes.Count -ge 5 -and $bytes[0] -eq 37 -and $bytes[1] -eq 80 -and $bytes[2] -eq 68 -and $bytes[3] -eq 70 -and $bytes[4] -eq 45
    if ($isPdf) {
        Move-Item -LiteralPath $tempPath -Destination $Destination -Force
        return [pscustomobject]@{ Success = $true; Path = $Destination; Error = $null }
    }

    $htmlPath = [System.IO.Path]::ChangeExtension($Destination, ".html")
    Move-Item -LiteralPath $tempPath -Destination $htmlPath -Force
    return [pscustomobject]@{
        Success = $false
        Path = $htmlPath
        Error = "Downloaded content was not a PDF. Saved diagnostic HTML instead."
    }
}

function Join-Url($Href) {
    if ($Href -match '^https?://') {
        return $Href
    }
    return ([System.Uri]::new([System.Uri]$BaseUrl, $Href)).AbsoluteUri
}

function Get-Links($Html) {
    $matches = [regex]::Matches($Html, '<a\b[^>]*href=["''](?<href>[^"'']+)["''][^>]*>(?<text>.*?)</a>', 'IgnoreCase,Singleline')
    foreach ($match in $matches) {
        $href = Join-Url ([System.Net.WebUtility]::HtmlDecode($match.Groups["href"].Value))
        $text = [regex]::Replace($match.Groups["text"].Value, '<[^>]+>', ' ')
        $text = [System.Net.WebUtility]::HtmlDecode($text)
        $text = ($text -replace '\s+', ' ').Trim()
        [pscustomobject]@{ Url = $href; Text = $text }
    }
}

function Find-CompanyPages($Companies) {
    $html = Get-UrlContent $BusinessListUrl
    $links = @(Get-Links $html | Where-Object { $_.Url -like "$BaseUrl/company/*" })
    $result = @{}

    foreach ($company in $Companies) {
        $needle = $company.ToLowerInvariant()
        $match = $links | Where-Object { $_.Text.ToLowerInvariant() -eq $needle } | Select-Object -First 1
        if (-not $match) {
            $match = $links | Where-Object { $_.Text.ToLowerInvariant().Contains($needle) } | Select-Object -First 1
        }
        if ($match) {
            Write-Host "[INFO] matched company '$company' -> '$($match.Text)'"
            $result[$company] = $match.Url
        } else {
            Write-Warning "company not found in business list: $company"
        }
    }
    return $result
}

function Get-Filings($Company, $CompanyUrl, $Limit) {
    $html = Get-UrlContent $CompanyUrl
    $seen = @{}
    $filings = New-Object System.Collections.Generic.List[object]
    foreach ($link in Get-Links $html) {
        if ($link.Url -notlike "$BaseUrl/IBFS/*") {
            continue
        }
        if ($link.Text -notmatch '^[A-Z]{2,4}-[A-Z/]+-(?:INTR)?\d{4}-\d{5}$') {
            continue
        }
        if ($seen.ContainsKey($link.Text)) {
            continue
        }
        $seen[$link.Text] = $true
        $filings.Add([pscustomobject]@{
            Company = $Company
            FileNumber = $link.Text
            PageUrl = $link.Url
        })
        if ($filings.Count -ge $Limit) {
            break
        }
    }
    return $filings
}

function Get-Attachments($Filing) {
    $html = Get-UrlContent $Filing.PageUrl
    $seen = @{}
    $attachments = New-Object System.Collections.Generic.List[object]
    $escapedFileNumber = [regex]::Escape($Filing.FileNumber)
    foreach ($link in Get-Links $html) {
        $path = ([System.Uri]$link.Url).AbsolutePath
        $match = [regex]::Match($path, "/IBFS/$escapedFileNumber/(\d+)$")
        if (-not $match.Success) {
            continue
        }
        $key = $match.Groups[1].Value
        if ($seen.ContainsKey($key)) {
            continue
        }
        $seen[$key] = $true
        $title = $link.Text
        if ([string]::IsNullOrWhiteSpace($title)) {
            $title = "Attachment $key"
        }
        $attachments.Add([pscustomobject]@{
            Title = $title
            PageUrl = $link.Url
            FileUrl = "$($link.Url).pdf"
            AttachmentKey = $key
        })
    }
    return $attachments
}

if (-not (Test-Path -LiteralPath $Config)) {
    throw "Config not found: $Config. Copy config.example.json to config.json and edit the company list."
}

$configData = Get-TextFileJson $Config ([pscustomobject]@{})
$outputDir = if ($configData.output_dir) { $configData.output_dir } else { "fcc_documents" }
$statePath = if ($configData.state_file) { $configData.state_file } else { "fcc_monitor_state.json" }
$maxFilings = if ($configData.max_filings_per_company) { [int]$configData.max_filings_per_company } else { 25 }
$pauseSeconds = if ($configData.pause_seconds) { [double]$configData.pause_seconds } else { 1.5 }
$downloadAttachments = if ($null -ne $configData.download_attachments) { [bool]$configData.download_attachments } else { $true }

$state = Get-TextFileJson $statePath ([pscustomobject]@{
    downloaded_attachments = [pscustomobject]@{}
    failed_attachments = [pscustomobject]@{}
    seen_filings = [pscustomobject]@{}
})

$downloaded = @{}
$state.downloaded_attachments.PSObject.Properties | ForEach-Object { $downloaded[$_.Name] = $_.Value }
$failed = @{}
if ($state.PSObject.Properties.Name -contains "failed_attachments") {
    $state.failed_attachments.PSObject.Properties | ForEach-Object { $failed[$_.Name] = $_.Value }
}
$seenFilings = @{}
$state.seen_filings.PSObject.Properties | ForEach-Object { $seenFilings[$_.Name] = $_.Value }

$companyPages = Find-CompanyPages $configData.companies
$newCount = 0

foreach ($company in $companyPages.Keys) {
    Write-Host "[INFO] scanning $company`: $($companyPages[$company])"
    $filings = Get-Filings $company $companyPages[$company] $maxFilings
    $companyFolder = ConvertTo-Slug $company

    foreach ($filing in $filings) {
        $filingKey = "$company|$($filing.FileNumber)"
        if (-not $seenFilings.ContainsKey($filingKey)) {
            $seenFilings[$filingKey] = $filing.PageUrl
            Write-Host "[NEW] filing $($filing.FileNumber)"
        }

        $attachments = @(Get-Attachments $filing)
        $filingDir = Join-Path (Join-Path $outputDir $companyFolder) (ConvertTo-Slug $filing.FileNumber)
        if (-not $DryRun) {
            New-Item -ItemType Directory -Force -Path $filingDir | Out-Null
            Save-TextFileJson (Join-Path $filingDir "metadata.json") ([pscustomobject]@{
                company = $filing.Company
                file_number = $filing.FileNumber
                source_page = $filing.PageUrl
                official_gov_page = "$($filing.PageUrl)/GOV"
                attachments = $attachments
            })
        }

        foreach ($attachment in $attachments) {
            $attachmentKey = "$($filing.FileNumber)|$($attachment.AttachmentKey)"
            if ($downloaded.ContainsKey($attachmentKey)) {
                continue
            }
            $newCount += 1
            $filename = "$($attachment.AttachmentKey)_$(ConvertTo-Slug $attachment.Title).pdf"
            $destination = Join-Path $filingDir $filename
            Write-Host "[NEW] attachment $($filing.FileNumber) $($attachment.Title) -> $destination"
            $downloadOk = $false
            $savedPath = $destination
            $downloadError = $null
            if ($downloadAttachments -and -not $DryRun) {
                $result = Save-RemotePdf $attachment.FileUrl $destination
                $downloadOk = $result.Success
                $savedPath = $result.Path
                $downloadError = $result.Error
                if (-not $downloadOk) {
                    Write-Warning "$($filing.FileNumber) $($attachment.AttachmentKey): $downloadError"
                }
            } elseif (-not $downloadAttachments) {
                $downloadOk = $true
            }
            if (-not $DryRun) {
                if ($downloadOk) {
                    $downloaded[$attachmentKey] = [pscustomobject]@{
                        company = $company
                        file_number = $filing.FileNumber
                        title = $attachment.Title
                        url = $attachment.FileUrl
                        path = $savedPath
                    }
                    if ($failed.ContainsKey($attachmentKey)) {
                        $failed.Remove($attachmentKey)
                    }
                } else {
                    $failed[$attachmentKey] = [pscustomobject]@{
                        company = $company
                        file_number = $filing.FileNumber
                        title = $attachment.Title
                        url = $attachment.FileUrl
                        diagnostic_path = $savedPath
                        error = $downloadError
                        checked_at = (Get-Date).ToString("s")
                    }
                }
                $state = [pscustomobject]@{
                    downloaded_attachments = $downloaded
                    failed_attachments = $failed
                    seen_filings = $seenFilings
                }
                Save-TextFileJson $statePath $state
            }
            if (-not $DryRun) {
                Start-Sleep -Seconds $pauseSeconds
            }
        }
    }
}

if (-not $DryRun) {
    $state = [pscustomobject]@{
        downloaded_attachments = $downloaded
        failed_attachments = $failed
        seen_filings = $seenFilings
    }
    Save-TextFileJson $statePath $state
}

Write-Host "[DONE] new attachments: $newCount"
