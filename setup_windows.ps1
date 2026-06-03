# setup_windows.ps1 — installs a daily scheduled task on Windows
# Run this once in PowerShell (as your regular user, no admin needed) after
# cloning the repo and filling in .env
#
# To run: right-click → "Run with PowerShell"
# Or from a PowerShell prompt: .\setup_windows.ps1

# ── Change this if you want a different run time ───────────────────────────
$RunTime = "21:00"   # 9 PM local time
# ──────────────────────────────────────────────────────────────────────────

$ScriptDir  = Split-Path -Parent $MyInvocation.MyCommand.Path
$EnvFile    = Join-Path $ScriptDir ".env"
$ScriptPath = Join-Path $ScriptDir "zoom_to_airtable.py"
$LogPath    = Join-Path $ScriptDir "zoom_to_airtable.log"
$TaskName   = "ZoomToAirtable"

if (-not (Test-Path $EnvFile)) {
    Write-Host "❌  No .env file found. Copy .env.example to .env and add your Airtable token first."
    exit 1
}

# Find python — try 'python' then 'python3'
$Python = $null
foreach ($cmd in @("python", "python3")) {
    try {
        $p = (Get-Command $cmd -ErrorAction Stop).Source
        # Make sure it's a real Python, not the Windows Store stub
        if ($p -notlike "*WindowsApps*") {
            $Python = $p
            break
        }
    } catch {}
}

if (-not $Python) {
    Write-Host "❌  Python not found. Install Python from https://www.python.org/downloads/"
    Write-Host "    Make sure to check 'Add Python to PATH' during installation."
    exit 1
}

# Check if task already exists
if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
    Write-Host "⚠️  A scheduled task named '$TaskName' already exists."
    Write-Host "    To remove it: Unregister-ScheduledTask -TaskName '$TaskName' -Confirm:`$false"
    exit 0
}

# Build the task — logs stdout+stderr to the log file
$Argument  = "-u `"$ScriptPath`" >> `"$LogPath`" 2>&1"
$Action    = New-ScheduledTaskAction -Execute $Python -Argument $Argument -WorkingDirectory $ScriptDir
$Trigger   = New-ScheduledTaskTrigger -Daily -At $RunTime
$Settings  = New-ScheduledTaskSettingsSet -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Hours 2)
$Principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive

Register-ScheduledTask -TaskName $TaskName -Action $Action -Trigger $Trigger `
    -Settings $Settings -Principal $Principal -Force | Out-Null

Write-Host "✅  Scheduled task installed — runs daily at $RunTime local time"
Write-Host "    Script : $ScriptPath"
Write-Host "    Log    : $LogPath"
Write-Host ""
Write-Host "To check it   : Get-ScheduledTask -TaskName '$TaskName'"
Write-Host "To run now    : Start-ScheduledTask -TaskName '$TaskName'"
Write-Host "To remove it  : Unregister-ScheduledTask -TaskName '$TaskName' -Confirm:`$false"
