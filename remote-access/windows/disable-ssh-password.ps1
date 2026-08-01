# Rodar como Administrador no PC-PR.
# Passo 2 de 2: só rodar DEPOIS de confirmar que a chave do MacBook loga
# sem senha (ssh -p 2222 "Paulo Roberto"@100.64.0.3 whoami). Sem essa
# confirmação, isto pode trancar o acesso SSH por senha sem um fallback
# de chave válido.
#
# Insere ANTES do bloco `Match Group administrators`, não faz append
# simples no fim do arquivo — mesmo motivo documentado em
# install-openssh.ps1 (Add-Content puro cairia dentro do Match, onde
# PasswordAuthentication seria ignorado silenciosamente).
$ErrorActionPreference = "Stop"

$configPath = "$env:ProgramData\ssh\sshd_config"
$content = Get-Content $configPath -Raw
if ($content -notmatch "(?m)^PasswordAuthentication no") {
    $content = $content -replace "(?m)^Match Group administrators", "PasswordAuthentication no`r`n`r`nMatch Group administrators"
    Set-Content -Path $configPath -Value $content -NoNewline -Encoding ascii
}

& "C:\Windows\System32\OpenSSH\sshd.exe" -t
Restart-Service sshd
Write-Output "PasswordAuthentication desligado. Teste de novo: ssh -p 2222 'Paulo Roberto'@100.64.0.3 whoami"
