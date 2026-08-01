# Rodar como Administrador no PC-PR.
# Passo 2 de 2: só rodar DEPOIS de confirmar que a chave do MacBook loga
# sem senha (ssh "Paulo Roberto"@100.64.0.3 whoami). Sem essa confirmação,
# isto pode trancar o acesso SSH por senha sem um fallback de chave válido.
$ErrorActionPreference = "Stop"

Add-Content -Path "$env:ProgramData\ssh\sshd_config" -Value "PasswordAuthentication no"
Restart-Service sshd
Write-Output "PasswordAuthentication desligado. Teste de novo: ssh 'Paulo Roberto'@100.64.0.3 whoami"
