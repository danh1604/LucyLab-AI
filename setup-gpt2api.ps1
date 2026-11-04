param(
  [string] $AdminBaseUrl = "http://127.0.0.1:17188",
  [string] $UserBaseUrl = "http://127.0.0.1:17180",
  [string] $AdminUser = "admin",
  [string] $AdminPassword = "admin123",
  [string] $LocalUser = "local@gpt2api.test",
  [string] $LocalPassword = "gpt2api-local-123",
  [string] $AccountName = "chatgpt-local",
  [string] $ClientID = "app_EMoamEEZ73f0CkXaXp7hrann"
)

$ErrorActionPreference = "Stop"

function Invoke-Gpt2ApiJson {
  param([string] $Method, [string] $Uri, [hashtable] $Body, [hashtable] $Headers = @{})
  $json = if ($null -ne $Body) { $Body | ConvertTo-Json -Depth 20 } else { $null }
  try {
    $params = @{ Method = $Method; Uri = $Uri; Headers = $Headers; ContentType = "application/json" }
    if ($null -ne $json) { $params.Body = $json }
    return Invoke-RestMethod @params
  } catch {
    $detail = $_.Exception.Message
    throw "Request failed: $Method $Uri`n$detail"
  }
}

# Get OAuth tokens from user
Write-Host ""
Write-Host "Paste your OpenAI/ChatGPT OAuth tokens:" -ForegroundColor Cyan
Write-Host "(Press Enter to skip optional fields)" -ForegroundColor DarkGray
Write-Host ""

$accessToken = Read-Host "access_token (optional if you have refresh_token)"
$refreshToken = Read-Host "refresh_token (recommended)"
$sessionToken = Read-Host "session_token / id_token (optional)"

if (-not $accessToken -and -not $refreshToken) {
  Write-Host ""
  Write-Host "ERROR: You must provide at least access_token or refresh_token." -ForegroundColor Red
  Write-Host ""
  Write-Host "How to get tokens:" -ForegroundColor Yellow
  Write-Host "  1. Go to https://chatgpt.com and log in with Google" -ForegroundColor Yellow
  Write-Host "  2. Open DevTools (F12) -> Application -> Cookies" -ForegroundColor Yellow
  Write-Host "  3. Copy __Secure-next-auth.session-token" -ForegroundColor Yellow
  exit 1
}

# Login to admin
Write-Host ""
Write-Host "[1/4] Logging into gpt2api admin..." -ForegroundColor Yellow
$adminLogin = Invoke-Gpt2ApiJson -Method "POST" -Uri "$AdminBaseUrl/admin/api/v1/auth/login" -Body @{ account = $AdminUser; password = $AdminPassword }
$adminToken = [string] $adminLogin.data.token.access_token
if (-not $adminToken) { throw "Admin login failed - no access token returned." }
Write-Host "  OK" -ForegroundColor Green

# Create GPT account
Write-Host "[2/4] Creating GPT OAuth upstream account..." -ForegroundColor Yellow
$account = Invoke-Gpt2ApiJson -Method "POST" -Uri "$AdminBaseUrl/admin/api/v1/accounts" `
  -Headers @{ Authorization = "Bearer $adminToken" } `
  -Body @{
    provider = "gpt"
    name = $AccountName
    auth_type = "oauth"
    access_token = $accessToken
    refresh_token = $refreshToken
    session_token = $sessionToken
    client_id = $ClientID
    weight = 10
    remark = "LucyLab local GPT account"
  }
$accountId = [string] $account.data.id
Write-Host "  Account ID: $accountId" -ForegroundColor Green

# Test account
if ($accountId) {
  Write-Host "[3/4] Testing account..." -ForegroundColor Yellow
  try {
    $test = Invoke-Gpt2ApiJson -Method "POST" -Uri "$AdminBaseUrl/admin/api/v1/accounts/$accountId/test" `
      -Headers @{ Authorization = "Bearer $adminToken" } -Body @{}
    Write-Host "  Account test passed" -ForegroundColor Green
  } catch {
    Write-Warning "Account test failed (token may need refresh later): $_"
  }
} else {
  Write-Host "[3/4] Skipping test (no account ID)" -ForegroundColor DarkGray
}

# Create local user + API key
Write-Host "[4/4] Creating local API key..." -ForegroundColor Yellow
try {
  $userLogin = Invoke-Gpt2ApiJson -Method "POST" -Uri "$UserBaseUrl/api/v1/auth/register" -Body @{ account = $LocalUser; password = $LocalPassword }
} catch {
  $userLogin = Invoke-Gpt2ApiJson -Method "POST" -Uri "$UserBaseUrl/api/v1/auth/login" -Body @{ account = $LocalUser; password = $LocalPassword }
}
$userToken = [string] $userLogin.data.token.access_token
if (-not $userToken) { throw "User login failed." }

$keyResp = Invoke-Gpt2ApiJson -Method "POST" -Uri "$UserBaseUrl/api/v1/keys" `
  -Headers @{ Authorization = "Bearer $userToken" } `
  -Body @{ name = "lucylab-local"; scope = "chat,image,video"; rpm_limit = 60 }
$apiKey = [string] $keyResp.data.plain
if (-not $apiKey) { throw "API key creation failed." }

# Save API key
$gpt2apiRoot = Split-Path -Parent $PSScriptRoot
$localDir = Join-Path $gpt2apiRoot "gpt2api\.local"
New-Item -ItemType Directory -Force -Path $localDir | Out-Null
$keyPath = Join-Path $localDir "openai-compatible-api-key.txt"
Set-Content -LiteralPath $keyPath -Value $apiKey -NoNewline

Write-Host ""
Write-Host "========================================" -ForegroundColor Green
Write-Host "  gpt2api setup complete!" -ForegroundColor Green
Write-Host ""
Write-Host "  API key saved to: $keyPath" -ForegroundColor Cyan
Write-Host "  OpenAI endpoint:  http://127.0.0.1:17200/v1" -ForegroundColor Cyan
Write-Host "  Admin panel:      http://127.0.0.1:17088" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Green
