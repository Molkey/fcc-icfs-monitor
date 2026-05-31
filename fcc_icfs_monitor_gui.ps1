Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$monitorScript = Join-Path $scriptDir "fcc_icfs_monitor.ps1"
$guiConfig = Join-Path $scriptDir "config.gui.json"

$form = New-Object System.Windows.Forms.Form
$form.Text = "FCC ICFS/IBFS Document Monitor"
$form.Size = New-Object System.Drawing.Size(760, 620)
$form.StartPosition = "CenterScreen"

$companyBox = New-Object System.Windows.Forms.GroupBox
$companyBox.Text = "Companies"
$companyBox.Location = New-Object System.Drawing.Point(12, 12)
$companyBox.Size = New-Object System.Drawing.Size(350, 170)
$form.Controls.Add($companyBox)

$companies = @(
    @{ Label = "SpaceX"; Name = "SpaceX Services Inc"; Checked = $true },
    @{ Label = "Amazon / Project Kuiper"; Name = "Kuiper Systems Llc"; Checked = $true },
    @{ Label = "AST SpaceMobile"; Name = "Ast Science"; Checked = $true }
)

$checks = @()
$y = 26
foreach ($company in $companies) {
    $check = New-Object System.Windows.Forms.CheckBox
    $check.Text = "$($company.Label)  [$($company.Name)]"
    $check.Tag = $company.Name
    $check.Checked = $company.Checked
    $check.Location = New-Object System.Drawing.Point(14, $y)
    $check.Size = New-Object System.Drawing.Size(310, 24)
    $companyBox.Controls.Add($check)
    $checks += $check
    $y += 32
}

$customLabel = New-Object System.Windows.Forms.Label
$customLabel.Text = "Custom company names, one per line"
$customLabel.Location = New-Object System.Drawing.Point(14, 124)
$customLabel.Size = New-Object System.Drawing.Size(250, 18)
$companyBox.Controls.Add($customLabel)

$customText = New-Object System.Windows.Forms.TextBox
$customText.Multiline = $true
$customText.ScrollBars = "Vertical"
$customText.Location = New-Object System.Drawing.Point(370, 35)
$customText.Size = New-Object System.Drawing.Size(360, 147)
$form.Controls.Add($customText)

$outputLabel = New-Object System.Windows.Forms.Label
$outputLabel.Text = "Output folder"
$outputLabel.Location = New-Object System.Drawing.Point(12, 200)
$outputLabel.Size = New-Object System.Drawing.Size(100, 22)
$form.Controls.Add($outputLabel)

$outputText = New-Object System.Windows.Forms.TextBox
$outputText.Text = (Join-Path $scriptDir "fcc_documents")
$outputText.Location = New-Object System.Drawing.Point(120, 198)
$outputText.Size = New-Object System.Drawing.Size(500, 24)
$form.Controls.Add($outputText)

$browseButton = New-Object System.Windows.Forms.Button
$browseButton.Text = "Browse"
$browseButton.Location = New-Object System.Drawing.Point(630, 196)
$browseButton.Size = New-Object System.Drawing.Size(90, 28)
$form.Controls.Add($browseButton)

$limitLabel = New-Object System.Windows.Forms.Label
$limitLabel.Text = "Filings per company"
$limitLabel.Location = New-Object System.Drawing.Point(12, 236)
$limitLabel.Size = New-Object System.Drawing.Size(130, 22)
$form.Controls.Add($limitLabel)

$limitInput = New-Object System.Windows.Forms.NumericUpDown
$limitInput.Minimum = 1
$limitInput.Maximum = 500
$limitInput.Value = 25
$limitInput.Location = New-Object System.Drawing.Point(150, 234)
$limitInput.Size = New-Object System.Drawing.Size(80, 24)
$form.Controls.Add($limitInput)

$downloadCheck = New-Object System.Windows.Forms.CheckBox
$downloadCheck.Text = "Download attachments"
$downloadCheck.Checked = $true
$downloadCheck.Location = New-Object System.Drawing.Point(260, 234)
$downloadCheck.Size = New-Object System.Drawing.Size(170, 24)
$form.Controls.Add($downloadCheck)

$dryRunCheck = New-Object System.Windows.Forms.CheckBox
$dryRunCheck.Text = "Preview only"
$dryRunCheck.Location = New-Object System.Drawing.Point(450, 234)
$dryRunCheck.Size = New-Object System.Drawing.Size(120, 24)
$form.Controls.Add($dryRunCheck)

$runButton = New-Object System.Windows.Forms.Button
$runButton.Text = "Run Scan"
$runButton.Location = New-Object System.Drawing.Point(600, 232)
$runButton.Size = New-Object System.Drawing.Size(120, 32)
$form.Controls.Add($runButton)

$logBox = New-Object System.Windows.Forms.TextBox
$logBox.Multiline = $true
$logBox.ScrollBars = "Vertical"
$logBox.ReadOnly = $true
$logBox.Font = New-Object System.Drawing.Font("Consolas", 9)
$logBox.Location = New-Object System.Drawing.Point(12, 280)
$logBox.Size = New-Object System.Drawing.Size(718, 285)
$form.Controls.Add($logBox)

function Add-Log($Text) {
    if ($form.InvokeRequired) {
        $form.BeginInvoke([Action[string]]{ param($message) Add-Log $message }, $Text) | Out-Null
        return
    }
    $logBox.AppendText("$Text`r`n")
}

$browseButton.Add_Click({
    $dialog = New-Object System.Windows.Forms.FolderBrowserDialog
    $dialog.SelectedPath = $outputText.Text
    if ($dialog.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK) {
        $outputText.Text = $dialog.SelectedPath
    }
})

$runButton.Add_Click({
    $selected = New-Object System.Collections.Generic.List[string]
    foreach ($check in $checks) {
        if ($check.Checked) {
            $selected.Add([string]$check.Tag)
        }
    }
    foreach ($line in ($customText.Text -split "`r?`n")) {
        $trimmed = $line.Trim()
        if ($trimmed) {
            $selected.Add($trimmed)
        }
    }

    if ($selected.Count -eq 0) {
        [System.Windows.Forms.MessageBox]::Show("Select at least one company.", "FCC Monitor") | Out-Null
        return
    }

    $stateFile = Join-Path $scriptDir "fcc_monitor_state_gui.json"
    $configData = [pscustomobject]@{
        companies = @($selected)
        output_dir = $outputText.Text
        state_file = $stateFile
        max_filings_per_company = [int]$limitInput.Value
        download_attachments = [bool]$downloadCheck.Checked
        pause_seconds = 1.5
    }
    $configData | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $guiConfig -Encoding UTF8

    $runButton.Enabled = $false
    $logBox.Clear()
    Add-Log "Starting scan..."
    Add-Log "Config: $guiConfig"

    $process = New-Object System.Diagnostics.Process
    $process.StartInfo.FileName = "powershell.exe"
    $args = @("-ExecutionPolicy", "Bypass", "-File", "`"$monitorScript`"", "-Config", "`"$guiConfig`"")
    if ($dryRunCheck.Checked) {
        $args += "-DryRun"
    }
    $process.StartInfo.Arguments = ($args -join " ")
    $process.StartInfo.WorkingDirectory = $scriptDir
    $process.StartInfo.UseShellExecute = $false
    $process.StartInfo.RedirectStandardOutput = $true
    $process.StartInfo.RedirectStandardError = $true
    $process.StartInfo.CreateNoWindow = $true
    $process.EnableRaisingEvents = $true

    $process.add_OutputDataReceived({ if ($_.Data) { Add-Log $_.Data } })
    $process.add_ErrorDataReceived({ if ($_.Data) { Add-Log $_.Data } })
    $process.add_Exited({
        Add-Log "Finished with exit code $($process.ExitCode)."
        $form.BeginInvoke([Action]{ $runButton.Enabled = $true }) | Out-Null
        $process.Dispose()
    })

    $process.Start() | Out-Null
    $process.BeginOutputReadLine()
    $process.BeginErrorReadLine()
})

[void]$form.ShowDialog()
