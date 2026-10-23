<#
Deploy WishBridge as a Databricks App (option 3) into a workspace - Wishtree's for testing, the client's later.

  .\deploy-databricks-app.ps1 -Profile DEFAULT -WarehouseId 1234abcd5678ef90 `
      -ProjectsVolume /Volumes/workspace/wishbridge/projects -TargetCatalogs workspace

  -Profile         the Databricks CLI login for the workspace (`databricks auth login --profile ...`)
  -WarehouseId     the SQL warehouse the app uses (SQL > SQL Warehouses > the warehouse > its ID)
  -ProjectsVolume  /Volumes/<catalog>/<schema>/<volume>: where the app keeps the projects (created if missing)
  -TargetCatalogs  catalogs the app may create its test schemas in (it gets USE CATALOG + CREATE SCHEMA there)
  -ShareWith       group(s) that also get the test schemas the app creates, e.g. "wishtree-migration-team"
                   (the person using the app always gets them)
  -ClaudeKeySecret scope/key of a Databricks secret holding an Anthropic API key, for Claude in the app
                   (databricks secrets create-scope wishbridge; databricks secrets put-secret wishbridge anthropic-api-key)
  -AppName         default "wishbridge" (lower-case letters, digits and -)

Run it again to deploy a new version of WishBridge: the app, its volume and its projects are kept.
The person running it needs to be allowed to create apps and to grant on the catalogs named above.
#>
param(
    [Parameter(Mandatory = $true)][Alias("Profile")][string]$DbProfile,
    [Parameter(Mandatory = $true)][string]$WarehouseId,
    [string]$ProjectsVolume = "/Volumes/workspace/wishbridge/projects",
    [string[]]$TargetCatalogs = @(),
    [string]$ShareWith = "",
    [string]$ClaudeKeySecret = "",
    [string]$AppName = "wishbridge"
)
$ErrorActionPreference = "Stop"
$repo = $PSScriptRoot

function Dbx([string[]]$CliArgs, [switch]$AllowFail) {
    # Windows PowerShell treats anything the CLI writes to stderr as an error: judge by the exit code only
    $ErrorActionPreference = "Continue"
    $out = & databricks @CliArgs --profile $DbProfile 2>&1 | ForEach-Object { "$_" }
    if ($LASTEXITCODE -ne 0) {
        if ($AllowFail) { return $null }
        throw "databricks $($CliArgs -join ' ') failed:`n$($out -join "`n")"
    }
    return ($out -join "`n")
}

function JsonFile($obj) {
    $f = [System.IO.Path]::GetTempFileName()
    ($obj | ConvertTo-Json -Depth 10 -Compress) | Set-Content -Path $f -Encoding ascii
    return "@$f"
}

if (-not (Get-Command databricks -ErrorAction SilentlyContinue)) { throw "The Databricks CLI is not installed (winget install Databricks.DatabricksCLI)." }
if ($AppName -notmatch '^[a-z0-9-]+$') { throw "AppName: lower-case letters, digits and - only." }
if ($ProjectsVolume -notmatch '^/Volumes/([A-Za-z0-9_]+)/([A-Za-z0-9_]+)/([A-Za-z0-9_]+)(/.*)?$') {
    throw "ProjectsVolume must look like /Volumes/<catalog>/<schema>/<volume>"
}
$cat, $sch, $vol = $Matches[1], $Matches[2], $Matches[3]

Write-Host "1/6 Signed in as:" -NoNewline
$me = (Dbx @("current-user", "me", "-o", "json") | ConvertFrom-Json).userName
Write-Host " $me"

Write-Host "2/6 Projects volume $cat.$sch.$vol"
if (-not (Dbx @("schemas", "get", "$cat.$sch") -AllowFail)) { Dbx @("schemas", "create", $sch, $cat, "--comment", "WishBridge app storage") | Out-Null }
if (-not (Dbx @("volumes", "read", "$cat.$sch.$vol") -AllowFail)) { Dbx @("volumes", "create", $cat, $sch, $vol, "MANAGED", "--comment", "WishBridge projects") | Out-Null }

Write-Host "3/6 App '$AppName'"
$app = Dbx @("apps", "get", $AppName, "-o", "json") -AllowFail
if (-not $app) {
    $body = @{ name = $AppName; description = "Wishtree WishBridge - data warehouse migration to Databricks"
               resources = @(@{ name = "sql-warehouse"; sql_warehouse = @{ id = $WarehouseId; permission = "CAN_USE" } }) }
    Write-Host "    creating it (this starts its compute, a few minutes)..."
    $app = Dbx @("apps", "create", "--json", (JsonFile $body), "-o", "json")
}
$app = $app | ConvertFrom-Json
$sp = $app.service_principal_client_id
if (-not $sp) { throw "The app has no service principal yet - wait a minute and run this again." }
Write-Host "    runs as service principal $sp"

Write-Host "4/6 Permissions for the app"
function Grant($type, $name, [string[]]$privs) {
    $changes = @{ changes = @(@{ principal = $sp; add = $privs }) }
    Dbx @("grants", "update", $type, $name, "--json", (JsonFile $changes)) | Out-Null
    Write-Host "    $($privs -join ', ') on $type $name"
}
Grant "catalog" $cat @("USE_CATALOG")
Grant "schema" "$cat.$sch" @("USE_SCHEMA")
Grant "volume" "$cat.$sch.$vol" @("READ_VOLUME", "WRITE_VOLUME")
foreach ($c in $TargetCatalogs) { Grant "catalog" $c @("USE_CATALOG", "CREATE_SCHEMA") }
if ($ClaudeKeySecret) {
    $scope = $ClaudeKeySecret.Split("/")[0]
    Dbx @("secrets", "put-acl", $scope, $sp, "READ") | Out-Null
    Write-Host "    READ on secret scope $scope (Claude API key)"
}

Write-Host "5/6 Upload WishBridge"
# a fixed build folder: `databricks sync` remembers what it sent, so a new version only uploads the changes
# outside the repo: sync skips what git ignores
$stage = Join-Path $env:LOCALAPPDATA "WishBridge\app-build\$AppName"
if (Test-Path $stage) { Get-ChildItem $stage -Force | Where-Object Name -ne ".databricks" | Remove-Item -Recurse -Force }
else { New-Item -ItemType Directory -Path $stage -Force | Out-Null }
# the same layout as the repository (what a Git deployment uses): app.yaml, requirements.txt, databricks_app/, src/
Copy-Item (Join-Path $repo "app.yaml"), (Join-Path $repo "requirements.txt") $stage
Copy-Item (Join-Path $repo "databricks_app") (Join-Path $stage "databricks_app") -Recurse
New-Item -ItemType Directory -Path (Join-Path $stage "src") -Force | Out-Null
Copy-Item (Join-Path $repo "src\wishbridge") (Join-Path $stage "src\wishbridge") -Recurse
Get-ChildItem $stage -Recurse -Directory -Filter "__pycache__" | Remove-Item -Recurse -Force
# this workspace's values over the defaults in app.yaml
$yaml = Join-Path $stage "app.yaml"
$text = Get-Content $yaml -Raw
$text = [regex]::Replace($text, '(name: WISHBRIDGE_PROJECTS_VOLUME\s+value: )"[^"]*"', ('${1}"' + $ProjectsVolume + '"'))
$text = [regex]::Replace($text, '(name: WISHBRIDGE_SHARE_WITH\s+value: )"[^"]*"', ('${1}"' + $ShareWith + '"'))
$text = [regex]::Replace($text, '(name: WISHBRIDGE_CLAUDE_SECRET\s+value: )"[^"]*"', ('${1}"' + $ClaudeKeySecret + '"'))
[System.IO.File]::WriteAllText($yaml, $text)
# never upload code that does not compile
$py = (Get-Command python -ErrorAction SilentlyContinue).Source
if ($py) {
    & $py -m compileall -q (Join-Path $stage "src\wishbridge") | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "WishBridge does not compile - fix it before deploying (python -m compileall src)." }
    Get-ChildItem $stage -Recurse -Directory -Filter "__pycache__" | Remove-Item -Recurse -Force
}
$remote = "/Workspace/Users/$me/apps/$AppName"
Dbx @("workspace", "mkdirs", $remote) | Out-Null
Dbx @("sync", "--full", $stage, $remote) | Out-Null
# leftovers of the layout before 23 Oct 2026 (package and start file at the top) - they would shadow src/
foreach ($old in @("wishbridge", "app_main.py")) { Dbx @("workspace", "delete", "$remote/$old", "--recursive") -AllowFail | Out-Null }
Write-Host "    $remote"

Write-Host "6/6 Deploy and start (a few minutes)..."
Dbx @("apps", "deploy", $AppName, "--source-code-path", $remote) | Out-Null
$url = (Dbx @("apps", "get", $AppName, "-o", "json") | ConvertFrom-Json).url
Write-Host ""
Write-Host "WishBridge is running: $url" -ForegroundColor Green
Write-Host "Give people access in Databricks: Compute > Apps > $AppName > Permissions (CAN USE)."
