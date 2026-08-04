# Rodar como Administrador no PC-PR.
# Passo 1 de 2: instala o sshd e o binda em dois endereços — o IP de tailnet
# (acesso normal) e o loopback (break-glass, ver abaixo).
# NÃO desliga PasswordAuthentication ainda — isso só acontece em
# disable-ssh-password.ps1, depois de confirmar que a chave pública
# do MacBook autentica (evita lockout, mesma ordem usada no WSL).
#
# Porta 2222, não 22: na rede WSL2 "mirrored" (.wslconfig), Windows e WSL
# compartilham a mesma pilha de porta. O WSL já ocupa 100.64.0.1:22 (SSH
# do gabinete-host, Task 3) — Windows tentando bindar a 22 (mesmo em IP
# diferente, 100.64.0.3) falha com "Permission denied". Confirmado ao
# vivo em 2026-08-01: 22 falhou, 2222 bindou limpo.
#
# IMPORTANTE sobre sshd_config: o template padrão termina com um bloco
# `Match Group administrators`. Diretivas globais (Port, ListenAddress,
# PubkeyAuthentication) *precisam* vir ANTES desse bloco — Add-Content
# simples (que só acrescenta no fim do arquivo) as colocaria DENTRO do
# Match, onde "Port" é erro fatal de sintaxe e "ListenAddress" é
# silenciosamente ignorado (sshd cai pro wildcard 0.0.0.0/::, mascarando
# o problema real). Por isso este script insere ANTES do Match, não
# faz append simples.
$ErrorActionPreference = "Stop"

Add-WindowsCapability -Online -Name OpenSSH.Server~~~~0.0.1.0
Set-Service -Name sshd -StartupType Automatic

# ListenAddress 127.0.0.1 além do IP de tailnet: o break-glass
# (cloudflared-breakglass.ps1) precisa alcançar este sshd SEM depender da
# interface Tailscale. O cenário que o break-glass existe para cobrir é
# reboot sem logon — exatamente o cenário em que não se sabe se o Tailscale
# do Windows sobe. Com bind só em 100.64.0.3, o canal de socorro morre junto
# com aquilo de que deveria ser independente; pior, o sshd nem inicia (falha
# ao bindar, e o Windows não tem equivalente ao FreeBind do systemd que
# resolve isso no lado WSL). O loopback sempre existe, então o serviço sobe
# em qualquer condição. Adicionado 2026-08-04.
$configPath = "$env:ProgramData\ssh\sshd_config"
$insert = "Port 2222`r`nListenAddress 100.64.0.3`r`nListenAddress 127.0.0.1`r`nPubkeyAuthentication yes`r`n`r`n"
$content = Get-Content $configPath -Raw
if ($content -notmatch "(?m)^Port 2222") {
    $content = $content -replace "(?m)^Match Group administrators", "$insert`Match Group administrators"
    Set-Content -Path $configPath -Value $content -NoNewline -Encoding ascii
}

& "C:\Windows\System32\OpenSSH\sshd.exe" -t
Start-Service sshd
Write-Output "sshd instalado, porta 2222, bind em 100.64.0.3 + 127.0.0.1 — confirme com: Get-NetTCPConnection -LocalPort 2222 -State Listen"
