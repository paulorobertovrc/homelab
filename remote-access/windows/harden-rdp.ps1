# Rodar como Administrador no PC-PR.
$ErrorActionPreference = "Stop"

$rules = @(
    "RemoteDesktop-UserMode-In-TCP",
    "RemoteDesktop-UserMode-In-UDP",
    "RemoteDesktop-Shadow-In-TCP"
)
foreach ($r in $rules) {
    Set-NetFirewallRule -Name $r -RemoteAddress 100.64.0.0/10
}

# Confirma NLA (Network Level Authentication) ativo.
$nla = (Get-WmiObject -Class "Win32_TSGeneralSetting" -Namespace root\cimv2\terminalservices `
    -Filter "TerminalName='RDP-tcp'").UserAuthenticationRequired
Write-Output "NLA ativo: $($nla -eq 1)"
if ($nla -ne 1) {
    Write-Warning "NLA NAO esta ativo -- habilitar em Configuracoes > Sistema > Area de Trabalho Remota."
}
