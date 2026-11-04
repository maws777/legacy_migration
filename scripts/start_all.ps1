<#
.SYNOPSIS
    Start the four APIs (ports 8000-8003), the Streamlit UI (8501) and the Client_App, each in its own window.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\start_all.ps1
    powershell -ExecutionPolicy Bypass -File scripts\start_all.ps1 -VarDir var\demo
    powershell -ExecutionPolicy Bypass -File scripts\start_all.ps1 -Source data\synthetic_edge_cases.csv -VarDir var\demo_edge -NoClient

    Stop everything with scripts\stop_all.ps1. Process ids are written to var\processes.json.
#>
param(
    [string]$Source = "data/legacy/hackathon-2026-digital-continuity-dataset.csv",
    [string]$VarDir = "var",
    [string]$Vins = "client_app/vins.txt",
    [double]$Interval = 1.0,
    [switch]$NoClient
)
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$python = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) { throw "Virtual environment not found: $python (see README.md, Installation)." }
if (-not (Test-Path (Join-Path $root $Source))) { throw "Source CSV not found: $Source" }
$pidFile = Join-Path $root "var\processes.json"
if (Test-Path $pidFile) { throw "Services seem to be running already ($pidFile exists). Run scripts\stop_all.ps1 first." }
New-Item -ItemType Directory -Force -Path (Join-Path $root "var"), (Join-Path $root $VarDir) | Out-Null

if (-not (Test-Path (Join-Path $root "legacy_b\data\legacy_b_records.csv"))) {
    Write-Host "Generating the Legacy B data..."
    Push-Location $root
    try { & $python -m legacy_b.simulator } finally { Pop-Location }
}

$targetDb = (Join-Path $VarDir "target.db") -replace "\\", "/"
$services = @(
    @{ Name = "Legacy_A_API :8001"; Port = 8001; Health = "/legacy-a/health"; Env = @{ LEGACY_A_CSV = $Source }
       Args = "-m uvicorn legacy_a.api:app --port 8001" },
    @{ Name = "Legacy_B_API :8002"; Port = 8002; Health = "/legacy-b/health"; Env = @{}
       Args = "-m uvicorn legacy_b.api:app --port 8002" },
    @{ Name = "Target_API :8003"; Port = 8003; Health = "/target/health"; Env = @{ TARGET_DB = $targetDb }
       Args = "-m uvicorn target.api:app --port 8003" },
    @{ Name = "Orchestrator :8000"; Port = 8000; Health = "/ops/phase"
       Env = @{ MIGRATION_SOURCE = $Source; MIGRATION_VAR_DIR = $VarDir }
       Args = "-m uvicorn agents.orchestrator:app --port 8000" },
    @{ Name = "UI :8501"; Port = 8501; Health = $null; Env = @{ ORCHESTRATOR_URL = "http://127.0.0.1:8000" }
       Args = "-m streamlit run ui/app.py --server.port 8501 --server.headless true" }
)
if (-not $NoClient) {
    $services += @{ Name = "Client_App"; Port = $null; Health = $null; Env = @{}
                    Args = "-m client_app --vins $Vins --interval $Interval" }
}

function Quote([string]$s) { "'" + $s.Replace("'", "''") + "'" }

function Wait-Health([int]$port, [string]$path, [int]$seconds = 60) {
    $deadline = (Get-Date).AddSeconds($seconds)
    while ((Get-Date) -lt $deadline) {
        try {
            $r = Invoke-WebRequest -UseBasicParsing -TimeoutSec 5 "http://127.0.0.1:$port$path"
            if ($r.StatusCode -eq 200) { return $true }
        } catch { Start-Sleep -Milliseconds 500 }
    }
    return $false
}

$started = @()
foreach ($s in $services) {
    if ($s.Name -eq "Client_App") {
        # The Client_App talks to the Orchestrator only: start it once the Orchestrator answers.
        if (-not (Wait-Health 8000 "/ops/phase")) { Write-Warning "Orchestrator not ready; starting the Client_App anyway." }
    }
    $lines = @("`$Host.UI.RawUI.WindowTitle = $(Quote $s.Name)", "Set-Location -LiteralPath $(Quote $root)")
    foreach ($kv in $s.Env.GetEnumerator()) { $lines += "`$env:$($kv.Key) = $(Quote $kv.Value)" }
    $lines += "& $(Quote $python) $($s.Args)"
    $encoded = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($lines -join "; "))
    $proc = Start-Process powershell.exe -WorkingDirectory $root -PassThru `
        -ArgumentList @("-NoExit", "-NoProfile", "-ExecutionPolicy", "Bypass", "-EncodedCommand", $encoded)
    $started += [pscustomobject]@{ name = $s.Name; pid = $proc.Id; port = $s.Port }
    $started | ConvertTo-Json | Set-Content -Encoding UTF8 $pidFile
    Write-Host ("Started {0,-20} (window pid {1})" -f $s.Name, $proc.Id)
    if ($s.Health) {
        if (Wait-Health $s.Port $s.Health) { Write-Host "  ready on http://127.0.0.1:$($s.Port)" }
        else { Write-Warning "  $($s.Name) did not answer $($s.Health) within 60 s; check its window." }
    }
}
Write-Host ""
Write-Host "UI:           http://127.0.0.1:8501"
Write-Host "Orchestrator: http://127.0.0.1:8000/docs"
Write-Host "Data:         source $Source, databases in $VarDir"
Write-Host "Stop all:     powershell -ExecutionPolicy Bypass -File scripts\stop_all.ps1"
