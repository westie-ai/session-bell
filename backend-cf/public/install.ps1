# SessionBell Windows 一键接入:
#   在 iPhone 上拷贝配对命令后,PowerShell 里运行:
#   irm https://sessionbell.westie.ai/install.ps1 | iex
# 配对码自动从剪贴板读取;也可以先下载再 sessionbell pair <配对码>。
$ErrorActionPreference = "Stop"
# Windows PowerShell 5.1 默认 TLS 1.0,连不上 Cloudflare/GitHub
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

$dir = Join-Path $env:LOCALAPPDATA "SessionBell"
$exe = Join-Path $dir "sessionbell.exe"
New-Item -ItemType Directory -Force -Path $dir | Out-Null

Write-Host "⬇️  下载 sessionbell.exe …"
$url = "https://github.com/westie-ai/session-bell/releases/latest/download/sessionbell.exe"
Invoke-WebRequest -Uri $url -OutFile $exe -UseBasicParsing

# 加进用户 PATH,新开的终端里可以直接敲 sessionbell
$userPath = [Environment]::GetEnvironmentVariable("Path", "User")
if ($userPath -notlike "*$dir*") {
    [Environment]::SetEnvironmentVariable("Path", "$userPath;$dir", "User")
    Write-Host "✓ 已加入 PATH(新终端生效)"
}

& $exe pair
