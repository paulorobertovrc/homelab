# Rodar como Administrador no PC-PR.
# Requer: cloudflared.exe já baixado (winget install --id Cloudflare.cloudflared,
# ou binário de https://github.com/cloudflare/cloudflared/releases) e o arquivo
# de credenciais do túnel já copiado para $configDir (ver instruções).
$ErrorActionPreference = "Stop"

$tunnelId = "da12da0e-24ef-4ca8-b0ee-8c3d3ac97ca6"
$configDir = "$env:ProgramData\Cloudflare\breakglass"
New-Item -ItemType Directory -Force -Path $configDir | Out-Null

$credFile = "$configDir\$tunnelId.json"
if (-not (Test-Path $credFile)) {
    throw "Copie $tunnelId.json (do WSL, ~/.cloudflared/) para $credFile antes de continuar."
}

@"
tunnel: $tunnelId
credentials-file: $credFile
ingress:
  - hostname: breakglass.gab.ia.br
    service: tcp://127.0.0.1:2222
    originRequest:
      access:
        required: true
        teamName: young-snow-b198
  - service: http_status:404
"@ | Set-Content "$configDir\config.yml"

cloudflared.exe service install --config "$configDir\config.yml"
Start-Service cloudflared
Write-Output "Servico cloudflared (break-glass) instalado -- confirme com: Get-Service cloudflared"
