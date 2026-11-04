<#
.SYNOPSIS
    Prepare a campaign on data\synthetic_edge_cases.csv to demonstrate "apply a value to the group".

.DESCRIPTION
    Validates the Column_Mapping and runs Run 1 in its own database folder (default var\demo_edge),
    with --no-llm so the Unknown_Variants become questions instead of being resolved by the LLM.
    The campaign pauses with 6 open questions; "originTool|UNKNOWN_VARIANT|CLIP" (2 rows, same raw value
    and label) offers apply_value.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\prepare_edge_demo.ps1 -Reset
    powershell -ExecutionPolicy Bypass -File scripts\start_all.ps1 -Source data\synthetic_edge_cases.csv -VarDir var\demo_edge -NoClient -NoLLM
#>
param(
    [string]$VarDir = "var/demo_edge",
    [string]$Operator = "demo",
    [switch]$Reset
)
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$python = Join-Path $root ".venv\Scripts\python.exe"
$source = "data/synthetic_edge_cases.csv"
Push-Location $root
try {
    if ($Reset -and (Test-Path $VarDir)) { Remove-Item -Recurse -Force $VarDir }
    & $python -m migration schema --source $source --var $VarDir --operator $Operator --no-llm
    if ($LASTEXITCODE -ne 0) { throw "Column_Mapping validation failed" }
    & $python -m migration run --source $source --var $VarDir --operator $Operator --no-llm
    if ($LASTEXITCODE -ne 0) { throw "Run 1 failed" }
} finally {
    Pop-Location
}
Write-Host ""
Write-Host "Campaign ready in $VarDir. Start the services on it:"
Write-Host "  powershell -ExecutionPolicy Bypass -File scripts\start_all.ps1 -Source $source -VarDir $VarDir -NoClient -NoLLM"
