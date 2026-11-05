<#
.SYNOPSIS
    Legacy B asks the Query_Agent three questions of its own and prints the answers.

.DESCRIPTION
    Needs the services started by scripts\start_all.ps1 (Orchestrator on port 8000, a migrated Target_DB).
    The Legacy B agent writes its three questions with the LLM (fixed questions with -NoLLM or when no
    model is available), sends them to POST /ops/chat as a legacy_b consumer and prints each SQL, the rows
    and, when rows list VINs with a model, the comparison with its own records. Its LLM calls are metered
    in <VarDir>\ops.db, so they appear in the report and the stats page.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\legacy_b_asks.ps1 -VarDir var\demo
#>
param(
    [string]$VarDir = "var",
    [string]$Url = "http://127.0.0.1:8000",
    [switch]$NoLLM
)
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$python = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) { throw "Virtual environment not found: $python (see README.md, Installation)." }
$argsList = @("-m", "agents.legacy_b_asker", "--url", $Url, "--var", $VarDir)
if ($NoLLM) { $argsList += "--no-llm" }
Push-Location $root
try {
    $env:PYTHONIOENCODING = "utf-8"
    & $python @argsList
    exit $LASTEXITCODE
} finally {
    Pop-Location
}
