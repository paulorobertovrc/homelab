# Rodar como Administrador no PC-PR.
# Requer: cloudflared.exe já baixado (winget install --id Cloudflare.cloudflared,
# ou binário de https://github.com/cloudflare/cloudflared/releases) e o arquivo
# de credenciais do túnel já copiado para $configDir (ver instruções).
#
# Usa Tarefa Agendada, não `cloudflared.exe service install`. Tentado ao vivo
# em 2026-08-01: o service install tem dois problemas — (1) não persiste
# --config para os starts seguintes (o serviço sempre sobe "pelado" e cai no
# caminho padrão, que resolve para o perfil de SYSTEM, não $env:ProgramData);
# (2) `sc.exe delete` deixa órfã a chave de registro do EventLog
# (SYSTEM\CurrentControlSet\Services\EventLog\Application\Cloudflared), que
# quebra silenciosamente a próxima reinstalação. A Tarefa Agendada evita as
# duas coisas: argumento explícito sempre, sem chave de EventLog nenhuma. E,
# ao contrário do wsl.exe (Task 3), o cloudflared não depende de um usuário
# logado — dispara "At startup" como SYSTEM, mais disponível que o próprio
# WSL/Headscale, que é o ponto inteiro do break-glass.
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
    # 127.0.0.1, não 100.64.0.3 (corrigido 2026-08-04): apontar o origin para
    # o IP de tailnet fazia este canal depender do Tailscale do Windows estar
    # de pé -- contradizendo a propriedade que justifica a Fase 3 inteira
    # ("não depende de WSL, Headscale, Tailscale nem DERP"). Como a falha que
    # o break-glass cobre é reboot sem logon, e é justamente aí que não se
    # sabe se o Tailscale sobe, o canal podia morrer no seu próprio cenário.
    # O sshd passou a bindar também no loopback (install-openssh.ps1) para
    # permitir esta correção. Em 2026-08-01 a config original apontava para
    # 127.0.0.1 e falhava com "connection actively refused" -- a causa era o
    # sshd não escutar no loopback, não o loopback ser o alvo errado.
    service: tcp://127.0.0.1:2222
    originRequest:
      access:
        required: true
        teamName: young-snow-b198
        audTag:
          - 6b4da48e9e6c07617c472772cc4b6ac2a725381f1c097daeb7bb23eeeb66bd71
  - service: http_status:404
"@ | Set-Content "$configDir\config.yml"

$cloudflaredExe = "C:\Program Files (x86)\cloudflared\cloudflared.exe"
if (-not (Test-Path $cloudflaredExe)) {
    throw "cloudflared.exe não encontrado em $cloudflaredExe -- ajuste o caminho se instalado noutro lugar."
}

$action = New-ScheduledTaskAction -Execute $cloudflaredExe -Argument "--config `"$configDir\config.yml`" tunnel run"
$trigger = New-ScheduledTaskTrigger -AtStartup
$principal = New-ScheduledTaskPrincipal -UserId "SYSTEM" -LogonType ServiceAccount -RunLevel Highest
$settings = New-ScheduledTaskSettingsSet -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit ([TimeSpan]::Zero)
Register-ScheduledTask -TaskName "cloudflared-breakglass" -Action $action -Trigger $trigger `
    -Principal $principal -Settings $settings -Force | Out-Null

Start-ScheduledTask -TaskName "cloudflared-breakglass"
Start-Sleep -Seconds 5
Write-Output "Tarefa agendada cloudflared-breakglass instalada e iniciada -- confirme com:"
Write-Output "  Get-ScheduledTask -TaskName cloudflared-breakglass | Select-Object TaskName,State"
Write-Output "  (do WSL) cloudflared tunnel info gabinete-breakglass"
