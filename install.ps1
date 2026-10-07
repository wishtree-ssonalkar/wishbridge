<#
.SYNOPSIS
  One-click setup for Wishtree WishBridge on Windows.

.DESCRIPTION
  Installs whatever is missing - Python, Java, the Databricks CLI (via winget) -
  signs in to Databricks, installs Databricks Labs LakeBridge and its converters,
  installs WishBridge into .venv next to this script, and adds a desktop shortcut.
  Safe to run again: steps that are already done are skipped.

.EXAMPLE
  .\install.ps1 -WorkspaceUrl https://dbc-1234.cloud.databricks.com
.EXAMPLE
  .\install.ps1 -DryRun        # only report what would be done
#>
param(
    [string]$WorkspaceUrl = "",
    [string]$DatabricksProfile = "DEFAULT",
    [string]$ShortcutDir = [Environment]::GetFolderPath("Desktop"),
    [switch]$NoShortcut,
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"
$Root = $PSScriptRoot
$Failed = $false

function Step([string]$m) { Write-Host "`n== $m" -ForegroundColor Cyan }
function Ok([string]$m) { Write-Host "   [ok]   $m" -ForegroundColor Green }
function Todo([string]$m) { Write-Host "   [todo] $m" -ForegroundColor Yellow }
function Bad([string]$m) { Write-Host "   [fail] $m" -ForegroundColor Red; $script:Failed = $true }
function Have([string]$cmd) { [bool](Get-Command $cmd -ErrorAction SilentlyContinue) }
function Refresh-Path {
    $env:Path = [Environment]::GetEnvironmentVariable("Path", "Machine") + ";" + [Environment]::GetEnvironmentVariable("Path", "User")
}
function Network-Hint {
    Write-Host "   If this was a certificate / SSL error, the office firewall is inspecting traffic:" -ForegroundColor Yellow
    Write-Host "   run this setup on another network (e.g. a mobile hotspot) or ask IT to trust its certificate." -ForegroundColor Yellow
}
function Winget-Install([string]$id, [string]$name) {
    if ($DryRun) { Todo "would install $name (winget $id)"; return }
    if (-not (Have "winget")) { Bad "winget is not available - install $name manually"; return }
    Write-Host "   installing $name ..."
    & winget install --id $id --exact --silent --accept-source-agreements --accept-package-agreements
    Refresh-Path
}
function Python-Ok {
    if (-not (Have "python")) { return $false }
    try {
        $v = & python -c "import sys; print('%d.%d' % sys.version_info[:2])" 2>$null
        return ($v -and [version]$v -ge [version]"3.10")
    } catch { return $false }
}

Write-Host "Wishtree WishBridge setup" -ForegroundColor White
if ($DryRun) { Write-Host "(dry run - nothing will be changed)" -ForegroundColor Yellow }

# 1. Prerequisites ------------------------------------------------------------
Step "1/6 Prerequisites"
if (Python-Ok) { Ok "Python $(& python --version 2>&1)" } else { Winget-Install "Python.Python.3.12" "Python 3.12"; if (-not $DryRun) { if (Python-Ok) { Ok "Python installed" } else { Bad "Python 3.10+ not found - install it from python.org (tick 'Add to PATH') and run setup again" } } }
if (Have "java") { Ok "Java found" } else { Winget-Install "EclipseAdoptium.Temurin.17.JDK" "Java 17"; if (-not $DryRun) { if (Have "java") { Ok "Java installed" } else { Bad "Java not found - install Java 11+ and run setup again" } } }
if (Have "databricks") { Ok "Databricks CLI $(& databricks --version 2>$null)" } else { Winget-Install "Databricks.DatabricksCLI" "Databricks CLI"; if (-not $DryRun) { if (Have "databricks") { Ok "Databricks CLI installed" } else { Bad "Databricks CLI not found - see https://docs.databricks.com/dev-tools/cli/install" } } }
if ($Failed) { Write-Host "`nFix the items above, then run setup again." -ForegroundColor Red; exit 1 }

# 2. Databricks login ---------------------------------------------------------
Step "2/6 Databricks login (profile $DatabricksProfile)"
$valid = $false
if (Have "databricks") {
    $line = (& databricks auth profiles 2>$null) | Where-Object { ($_ -split "\s+")[0] -eq $DatabricksProfile } | Select-Object -First 1
    $valid = [bool]($line -and ($line -split "\s+")[-1] -eq "YES")
}
if ($valid) { Ok "signed in" }
elseif ($DryRun) { Todo "would sign in to Databricks (browser opens)" }
else {
    if (-not $WorkspaceUrl) { $WorkspaceUrl = Read-Host "   Databricks workspace URL (e.g. https://dbc-1234.cloud.databricks.com)" }
    & databricks auth login --host $WorkspaceUrl --profile $DatabricksProfile
    if ($LASTEXITCODE -eq 0) { Ok "signed in" } else { Bad "sign-in failed"; exit 1 }
}
$env:DATABRICKS_CONFIG_PROFILE = $DatabricksProfile

# 3. LakeBridge ---------------------------------------------------------------
Step "3/6 Databricks Labs LakeBridge"
$lakebridge = Join-Path $HOME ".databricks\labs\lakebridge"
if (Test-Path $lakebridge) { Ok "installed" }
elseif ($DryRun) { Todo "would run: databricks labs install lakebridge" }
else {
    Write-Host "   installing (press Enter to accept the defaults if asked) ..."
    & databricks labs install lakebridge
    if ($LASTEXITCODE -eq 0 -and (Test-Path $lakebridge)) { Ok "installed" } else { Bad "LakeBridge install failed"; Network-Hint; exit 1 }
}

# 4. Converters ---------------------------------------------------------------
Step "4/6 LakeBridge converters"
$transpilers = Join-Path $HOME ".databricks\labs\remorph-transpilers"
$missing = @("databricks-morph-plugin", "bladebridge") | Where-Object { -not (Test-Path (Join-Path $transpilers "$_\lib\config.yml")) }
if (-not $missing) { Ok "Morph and BladeBridge installed" }
elseif ($DryRun) { Todo "would install: $($missing -join ', ')" }
else {
    & databricks labs lakebridge install-transpile --interactive false
    if ($LASTEXITCODE -eq 0) { Ok "converters installed" } else { Bad "converter install failed"; Network-Hint; exit 1 }
}

# 5. WishBridge ---------------------------------------------------------------
Step "5/6 WishBridge"
$venv = Join-Path $Root ".venv"
$exe = Join-Path $venv "Scripts\wishbridge.exe"
if ($DryRun) {
    if (Test-Path $exe) { Ok "installed ($exe) - would refresh it" } else { Todo "would create .venv and install WishBridge" }
} else {
    if (-not (Test-Path (Join-Path $venv "Scripts\python.exe"))) { & python -m venv $venv }
    & (Join-Path $venv "Scripts\python.exe") -m pip install --quiet --disable-pip-version-check -e "$Root[ai,ui]"
    if ($LASTEXITCODE -eq 0 -and (Test-Path $exe)) { Ok "$(& $exe --version)" } else { Bad "pip install failed"; Network-Hint; exit 1 }
}

# 6. Desktop shortcut ---------------------------------------------------------
Step "6/6 Desktop shortcut"
$launcher = Join-Path $Root "Start WishBridge.cmd"
$lnk = Join-Path $ShortcutDir "Wishtree WishBridge.lnk"
if ($NoShortcut) { Ok "skipped (-NoShortcut)" }
elseif ($DryRun) { Todo "would create $lnk" }
else {
    $shell = New-Object -ComObject WScript.Shell
    $sc = $shell.CreateShortcut($lnk)
    $sc.TargetPath = $launcher
    $sc.WorkingDirectory = $Root
    $sc.IconLocation = "$env:SystemRoot\System32\shell32.dll,13"
    $sc.Description = "Wishtree WishBridge - migration to Databricks"
    $sc.Save()
    Ok "created $lnk"
}

Write-Host ""
if ($DryRun) { Write-Host "Dry run finished." -ForegroundColor Yellow }
else { Write-Host "WishBridge is ready. Double-click 'Wishtree WishBridge' on your desktop to open the app." -ForegroundColor Green }
