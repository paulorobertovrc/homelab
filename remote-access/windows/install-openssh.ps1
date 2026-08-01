# Rodar como Administrador no PC-PR.
# Passo 1 de 2: instala o sshd, restringe o bind ao IP de tailnet.
# NÃO desliga PasswordAuthentication ainda — isso só acontece em
# disable-ssh-password.ps1, depois de confirmar que a chave pública
# do MacBook autentica (evita lockout, mesma ordem usada no WSL).
$ErrorActionPreference = "Stop"

Add-WindowsCapability -Online -Name OpenSSH.Server~~~~0.0.1.0
Set-Service -Name sshd -StartupType Automatic
Start-Service sshd

Add-Content -Path "$env:ProgramData\ssh\sshd_config" -Value "`nListenAddress 100.64.0.3"
Add-Content -Path "$env:ProgramData\ssh\sshd_config" -Value "PubkeyAuthentication yes"

Restart-Service sshd
Write-Output "sshd instalado e restrito a 100.64.0.3 — confirme com: Get-NetTCPConnection -LocalPort 22 -State Listen"
