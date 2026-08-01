# Acesso remoto ao gabinete-host via MacBook — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** SSH endurecido para o WSL e para o Windows, RDP restrito à tailnet, e um canal de break-glass independente do control plane do Headscale — cobrindo os dois casos de uso do usuário (trabalho normal no desktop Windows; consertar a máquina remotamente estando fora de casa).

**Architecture:** ACL do Headscale abre exatamente os acessos novos (default-deny preservado). SSH no WSL e no Windows via chave apenas, bind restrito ao IP de tailnet de cada host. RDP restrito por escopo de firewall. Um segundo túnel `cloudflared` no Windows dá acesso SSH que não depende de WSL/Headscale/Tailscale — é o canal de socorro se o control plane cair. Configs versionadas em `homelab/remote-access/` (não são segredo — mesmo padrão de config-as-code já usado em `controle-processos/ops`).

**Tech Stack:** Headscale v0.29.1 (ACL HuJSON), OpenSSH (WSL: `ssh.socket` systemd socket-activation; Windows: `Add-WindowsCapability`), Windows Firewall (`NetFirewallRule`), `cloudflared` + Cloudflare Access.

## Global Constraints

- ACL do Headscale é default-deny — toda regra nova precisa ser explícita, nunca ampliar `tag:gabinete` (a tag de cliente) como origem de SSH/RDP. (spec §Fase 1)
- SSH em ambos os hosts: chave apenas, nunca senha. (spec §Fase 2)
- Nenhum bind de serviço novo em `0.0.0.0` — sempre no IP de tailnet do host. (spec §Fase 2)
- Sem autologon/tela travada no Windows, a menos que o drill da Fase 3 falhe — decisão condicionada, não tomada agora. (spec §Risco central, §Fase 3)
- Fase 3 (break-glass) só é considerada pronta depois do drill de login **a frio** (navegador limpo, MacBook fora da LAN); sem esse drill passar, este plano não está completo. (spec §Fase 3)
- Nenhuma mudança em `derp.urls` (DERP público) — descartado no design. (spec §Risco central)
- Nenhuma migração de Headscale/DERP para VPS — fora de escopo. (spec §Fora de escopo)
- A Fase 0 (medição) decide se a Task 6 (UDP direto) executa ou é pulada — não presumir de antemão. (spec §Fase 0)

---

### Task 1: Fase 0 — Medir o transporte (requer o MacBook fisicamente fora da LAN)

**Requer o usuário.** Nenhum passo aqui é executável por um agente sem acesso físico ao MacBook. Quem executar este plano deve pausar aqui e pedir ao usuário para rodar os comandos abaixo do MacBook, em hotspot 4G/5G (fora da rede de casa), e reportar o resultado.

**Files:** nenhum (diagnóstico, não produz código).

**Interfaces:**
- Consumes: nada (primeira tarefa).
- Produces: uma decisão **DIRECT** ou **RELAY**, consumida pela Task 6 (se `RELAY`, Task 6 executa; se `DIRECT`, Task 6 é pulada inteira e marcada como não-aplicável no changelog do plano).

- [ ] **Step 1: Conectar o MacBook a uma rede fora de casa**

Hotspot do celular resolve. Confirmar que não é a rede residencial:
```bash
curl -s https://api.ipify.org
```
O IP retornado não pode ser `189.58.122.218` (o IP público de casa, já conhecido).

- [ ] **Step 2: Medir se a conexão ao PC-PR sai direta ou por relay**

```bash
tailscale ping PC-PR
```
Rodar a saída por ~10 segundos (ctrl-C para parar). Duas leituras possíveis:
- `pong from PC-PR (100.64.0.3) via DERP(gab) in Xms` — **RELAY**. Todo o tráfego passa pelo DERP embutido no WSL (região `999 gab`).
- `pong from PC-PR (100.64.0.3) via 189.58.122.218:PORT in Xms` (ou IP local, se por acaso a rede tiver rota) — **DIRECT**. WireGuard puro, sem passar pelo DERP.

Registrar qual dos dois apareceu — decide a Task 6.

- [ ] **Step 3: Medir latência e throughput utilizáveis para RDP**

```bash
tailscale ping PC-PR | head -5
```
Anotar a latência típica (ms). Não é preciso um teste de banda formal — RDP em `DIRECT` com <100ms é usável; em `RELAY` com latência alta ou perda visível (`tailscale ping` mostrando timeouts), a recomendação prática é preferir SSH+linha de comando a RDP quando estiver longe, e reservar RDP para quando a Task 6 tiver corrigido o caminho.

- [ ] **Step 4: Registrar a decisão**

Anotar no início da Task 6 deste arquivo (editar o checkbox da Task 6 com `[DIRECT — pulada]` ou `[RELAY — executar]`) antes de prosseguir para a Task 2.

**Resultado real, medido em 2026-08-01 (MacBook em 5G, IP `189.93.53.169`, confirmadamente fora da
LAN de casa):** assimétrico, não um DIRECT/RELAY único para o host inteiro.

- `tailscale ping PC-PR`: DERP na 1ª resposta (91ms), **DIRECT** já na 2ª (`189.58.122.218:41641`,
  117ms) — o UPnP do Archer mapeia sozinho a porta fixa 41641 do Windows, sem forward manual.
- `tailscale ping gabinete-host`: **6 de 6 via DERP** (79–107ms), nunca direto, em ~6s de rajada —
  consistente com a porta efêmera do `tailscaled` do WSL (ver Task 6).

Decisão: como o RDP (o caso mais sensível a latência) tem como alvo o `PC-PR`, que já está DIRECT,
a Task 6 foi **pulada** — só beneficiaria o SSH do `gabinete-host`, que tolera bem DERP. Decisão do
usuário, registrada explicitamente, não uma suposição.

---

### Task 2: Fase 1 — ACL mínima no Headscale

**Files:**
- Modify: `~/dev/controle-processos/ops/headscale/policy.hujson` (fonte de verdade, versionada nesse repo — é o arquivo que `setup-host.sh` copia para `/etc/headscale/policy.hujson`)
- Deploy target (não versionado): `/etc/headscale/policy.hujson`

**Interfaces:**
- Consumes: nada de outras tasks deste plano.
- Produces: tag `tag:admin` (origem, só o MacBook) e `tag:app-host` também no `PC-PR` (destino, somado ao que o `gabinete-host` já carrega). Tasks 3, 4 e 5 dependem dessas duas mudanças estarem aplicadas — sem elas, toda tentativa de SSH/RDP dá timeout (característica do Headscale: ACL bloqueando não dá erro explícito, dá timeout).

- [ ] **Step 1: Editar o arquivo de política, fonte de verdade**

Conteúdo atual (`~/dev/controle-processos/ops/headscale/policy.hujson`):
```hujson
// ACL do tailnet (F4). Menor-privilégio: clientes do gabinete só alcançam a
// porta do app no host. Sem acesso lateral entre devices.
{
  "tagOwners": {
    "tag:app-host":  ["gabinete-admin@"],
    "tag:gabinete":  ["gabinete-admin@"],
  },
  "acls": [
    // Clientes (Mac/celular) → porta do app (Caddy escuta no IP de tailnet:443).
    { "action": "accept", "src": ["tag:gabinete"], "dst": ["tag:app-host:8443"] },
  ],
  // Nada além disso: sem device↔device, sem outras portas.
}
```

Substituir pelo conteúdo completo abaixo:
```hujson
// ACL do tailnet (F4). Menor-privilégio: clientes do gabinete só alcançam a
// porta do app no host. Sem acesso lateral entre devices.
//
// 2026-08-01: tag:admin somada — acesso remoto pessoal (SSH+RDP) ao
// gabinete-host/PC-PR a partir do MacBook. PC-PR passou de tag:gabinete
// (cliente) para tag:app-host (destino) — ele é o lado Windows do mesmo
// hardware do gabinete-host, não um cliente do app.
//
// CAUTELA: tag:app-host cobre os DOIS nós (gabinete-host + PC-PR). A regra
// do app (linha "8443") tecnicamente também casaria com PC-PR:8443, mas
// nada escuta 8443 lá — inofensivo hoje. Se algo um dia escutar 8443 no
// PC-PR, revisar esta ACL.
{
  "tagOwners": {
    "tag:app-host":  ["gabinete-admin@"],
    "tag:gabinete":  ["gabinete-admin@"],
    "tag:admin":     ["gabinete-admin@"],
  },
  "acls": [
    // Clientes (Mac/celular) → porta do app (Caddy escuta no IP de tailnet:443).
    { "action": "accept", "src": ["tag:gabinete"], "dst": ["tag:app-host:8443"] },
    // Admin (só o MacBook) → SSH em qualquer host tag:app-host (gabinete-host + PC-PR).
    { "action": "accept", "src": ["tag:admin"], "dst": ["tag:app-host:22"] },
    // Admin (só o MacBook) → RDP no PC-PR (harmless no gabinete-host, nada escuta 3389 lá).
    { "action": "accept", "src": ["tag:admin"], "dst": ["tag:app-host:3389"] },
  ],
  // Nada além disso: sem device↔device, sem outras portas.
}
```

- [ ] **Step 2: Validar a sintaxe antes de aplicar**

```bash
sudo headscale policy check --file ~/dev/controle-processos/ops/headscale/policy.hujson
```
Expected: sem erro (`Policy is valid` ou saída vazia com exit 0).

- [ ] **Step 3: Copiar para o destino live e aplicar**

```bash
sudo cp ~/dev/controle-processos/ops/headscale/policy.hujson /etc/headscale/policy.hujson
sudo systemctl restart headscale
```
`policy.mode: file` neste host recarrega a política ao iniciar o processo — não há `ExecReload` na unit, então é `restart`, não `reload`. Isso causa um blip breve no control plane (handshakes novos pausam por ~1-2s); conexões WireGuard já estabelecidas (inclusive a do app em produção) não caem.

- [ ] **Step 4: Verificar que o app do `controle-processos` não regrediu**

```bash
sleep 3
systemctl is-active headscale caddy assessoria-app tailscaled
sudo headscale policy get | diff - /etc/headscale/policy.hujson
```
Expected: todos `active`; `diff` sem saída (política live bate com o arquivo).

- [ ] **Step 5: Re-taguear o `PC-PR`**

```bash
sudo headscale nodes list
```
Confirmar o ID do `PC-PR` (era `3` no levantamento). Então:
```bash
sudo headscale nodes tag -i 3 -t tag:app-host --force
sudo headscale nodes list
```
Verificar a coluna `Tags` do `PC-PR`: deve mostrar `tag:app-host`. **Se `tag:gabinete` ainda aparecer junto** (o comando pode ser aditivo, não substitutivo — não verificado nesta versão), rodar de novo com a lista completa desejada:
```bash
sudo headscale nodes tag -i 3 -t tag:app-host --force
```
e conferir outra vez até restar só `tag:app-host`.

- [ ] **Step 6: Commit**

```bash
cd ~/dev/controle-processos
git add ops/headscale/policy.hujson
git commit -m "feat(ops): ACL para acesso remoto pessoal (SSH+RDP) via tag:admin

PC-PR passa de tag:gabinete para tag:app-host — ele é o lado Windows
do gabinete-host, não um cliente do app. tag:admin (só o MacBook)
ganha SSH e RDP em qualquer host tag:app-host. Default-deny preservado.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

- [ ] **Step 7: Emitir a pre-auth key e taguear o MacBook**

```bash
sudo headscale preauthkeys create --user 2 --reusable --expiration 1h --tags tag:admin,tag:gabinete
```
A chave começa com `hskey-auth-…` — secreta, não colar em chat/commit. No MacBook (ele já está na tailnet com `tag:gabinete`; isto adiciona `tag:admin` via reauth):
```bash
sudo tailscale up --login-server https://headscale.gab.ia.br --authkey hskey-auth-XXXXX --force-reauth
```
Verificar:
```bash
sudo headscale nodes list
```
`MacBook-Pro` deve mostrar `tag:admin,tag:gabinete` (ou `tag:gabinete,tag:admin` — ordem não importa).

---

### Task 3: Fase 2 — SSH endurecido no WSL

**Files:**
- Create: `remote-access/wsl/ssh.socket.d/override.conf`
- Create: `remote-access/wsl/sshd_config.d/10-tailnet-only.conf`
- Deploy targets (não versionados): `/etc/systemd/system/ssh.socket.d/override.conf`, `/etc/ssh/sshd_config.d/10-tailnet-only.conf`

**Interfaces:**
- Consumes: ACL da Task 2 já aplicada (senão o teste de conectividade do Step 5 dá timeout mesmo com tudo certo aqui).
- Produces: nada que outra task consuma — mas o **Step 1 é uma pré-condição de segurança para todas as outras**: sem uma chave em `authorized_keys`, desabilitar `PasswordAuthentication` tranca o próprio usuário fora.

- [ ] **Step 1: Registrar a chave pública do MacBook ANTES de tocar em qualquer config**

`~/.ssh/authorized_keys` está **vazio** hoje neste host — checar antes de prosseguir:
```bash
cat ~/.ssh/authorized_keys 2>/dev/null; echo "(vazio se nada apareceu acima)"
```
Se vazio, pedir ao usuário a chave pública do MacBook (`cat ~/.ssh/id_ed25519.pub` no Mac; gerar com `ssh-keygen -t ed25519` se não existir) e adicionar:
```bash
mkdir -p ~/.ssh && chmod 700 ~/.ssh
echo "<conteúdo da chave pública do MacBook>" >> ~/.ssh/authorized_keys
chmod 600 ~/.ssh/authorized_keys
```
**Não prosseguir para o Step 4 (desabilitar senha) sem confirmar que este arquivo tem a chave.**

- [ ] **Step 2: Criar o override do `ssh.socket`, versionado**

`remote-access/wsl/ssh.socket.d/override.conf`:
```ini
[Socket]
# Reseta os ListenStream herdados da unit base (0.0.0.0:22 e [::]:22) antes
# de restringir ao IP de tailnet — sem a linha vazia, os dois convivem.
ListenStream=
ListenStream=100.64.0.1:22
# FreeBind já vem "yes" na unit base do pacote Ubuntu (systemctl cat ssh.socket
# confirma) — não precisa redeclarar aqui. Ele é o que evita a corrida de boot
# contra a interface tailscale0 (mesma classe de bug já visto com o
# qBittorrent e a tun0: bind cedo demais falha silenciosamente).
```

- [ ] **Step 3: Criar o drop-in de `sshd_config`, versionado**

`remote-access/wsl/sshd_config.d/10-tailnet-only.conf`:
```
PubkeyAuthentication yes
PasswordAuthentication no
PermitRootLogin no
AllowUsers prvrc
```

- [ ] **Step 4: Aplicar — bind primeiro, senha por último**

```bash
sudo mkdir -p /etc/systemd/system/ssh.socket.d
sudo cp remote-access/wsl/ssh.socket.d/override.conf /etc/systemd/system/ssh.socket.d/override.conf
sudo systemctl daemon-reload
sudo systemctl restart ssh.socket
```
Verificar o novo bind:
```bash
ss -tlnp | grep :22
```
Expected: só `100.64.0.1:22` (não mais `0.0.0.0:22`).

Testar login por chave **antes** de desabilitar senha (ainda seria possível por senha se a chave falhar):
```bash
ssh -o BatchMode=yes -o ConnectTimeout=5 prvrc@100.64.0.1 true && echo "chave OK"
```
Expected: `chave OK`. **Só prosseguir se isto passar.**

Agora aplicar o drop-in que desabilita senha:
```bash
sudo cp remote-access/wsl/sshd_config.d/10-tailnet-only.conf /etc/ssh/sshd_config.d/10-tailnet-only.conf
sudo sshd -t
```
Expected: sem erro. Então recarregar (sshd é ativado por socket; a config é lida por instância nova, mas para pegar imediatamente):
```bash
sudo systemctl restart ssh.socket
ssh -o BatchMode=yes -o ConnectTimeout=5 prvrc@100.64.0.1 true && echo "ainda OK, sem senha"
```

- [ ] **Step 5: Commit**

```bash
cd ~/dev/homelab
git add remote-access/wsl/
git commit -m "feat(remote-access): SSH do WSL restrito à tailnet, chave apenas

ssh.socket bindava 0.0.0.0:22 (LAN inteira, .wslconfig tem
firewall=false). Restringe a 100.64.0.1 e desabilita senha —
FreeBind da unit base evita a corrida de boot contra tailscale0.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 4: Fase 2 — OpenSSH Server no Windows

**Requer PowerShell como Administrador no PC-PR** (elevação necessária — `Get-WindowsCapability` falhou sem ela no levantamento).

**Files:**
- Create: `remote-access/windows/sshd_config_admin.txt` (conteúdo a acrescentar em `C:\ProgramData\ssh\sshd_config`)
- Create: `remote-access/windows/install-openssh.ps1`
- Deploy target (não versionado): `C:\ProgramData\ssh\sshd_config`, `C:\ProgramData\ssh\administrators_authorized_keys`

**Interfaces:**
- Consumes: ACL da Task 2 (regra `tag:admin → tag:app-host:22` cobre o `PC-PR` também).
- Produces: nada consumido por outra task.

**Gotcha crítico, verificado no levantamento:** o usuário `Paulo Roberto` é membro do grupo `Administrators`. O OpenSSH do Windows trata contas de administrador como caso especial: chaves em `~/.ssh/authorized_keys` são **ignoradas** para essas contas — é preciso usar `C:\ProgramData\ssh\administrators_authorized_keys`, com ACL restrita a `SYSTEM` e `Administrators` apenas (o sshd recusa a chave em silêncio se as permissões do arquivo forem mais abertas que isso).

- [ ] **Step 1: Script de instalação, versionado**

`remote-access/windows/install-openssh.ps1`:
```powershell
# Rodar como Administrador no PC-PR.
$ErrorActionPreference = "Stop"

Add-WindowsCapability -Online -Name OpenSSH.Server~~~~0.0.1.0
Set-Service -Name sshd -StartupType Automatic
Start-Service sshd

# Restringe o bind ao IP de tailnet do PC-PR (não 0.0.0.0, LAN inteira hoje).
Add-Content -Path "$env:ProgramData\ssh\sshd_config" -Value "`nListenAddress 100.64.0.3"
Add-Content -Path "$env:ProgramData\ssh\sshd_config" -Value "PasswordAuthentication no"
Add-Content -Path "$env:ProgramData\ssh\sshd_config" -Value "PubkeyAuthentication yes"

Restart-Service sshd
Write-Output "sshd instalado e restrito a 100.64.0.3 — confirme com: Get-NetTCPConnection -LocalPort 22 -State Listen"
```

- [ ] **Step 2: Rodar o script (Administrador)**

```powershell
powershell -ExecutionPolicy Bypass -File remote-access\windows\install-openssh.ps1
```
Verificar o bind:
```powershell
Get-NetTCPConnection -LocalPort 22 -State Listen | Select-Object LocalAddress,LocalPort
```
Expected: `100.64.0.3` na linha, **não** `0.0.0.0`. Se o bind falhar por conflito de porta com a pilha mirrored compartilhada (o `ssh.socket` do WSL já deve estar restrito a `100.64.0.1` pela Task 3 — se a Task 3 não rodou antes desta, rodar primeiro), fallback:
```powershell
Add-Content -Path "$env:ProgramData\ssh\sshd_config" -Value "Port 2222"
Restart-Service sshd
```
e registrar no `~/.ssh/config` do MacBook (Task 4, Step 4) a porta 2222 em vez de 22 para o host `PC-PR`.

- [ ] **Step 3: Instalar a chave pública do MacBook no arquivo de administrador**

No PC-PR, como Administrador:
```powershell
$key = "<conteúdo da chave pública do MacBook, a mesma da Task 3>"
Add-Content -Path "$env:ProgramData\ssh\administrators_authorized_keys" -Value $key
icacls "$env:ProgramData\ssh\administrators_authorized_keys" /inheritance:r
icacls "$env:ProgramData\ssh\administrators_authorized_keys" /grant "SYSTEM:F" "Administrators:F"
```
Testar do MacBook (ou de dentro do WSL, que também está na tailnet):
```bash
ssh -o BatchMode=yes -o ConnectTimeout=5 "Paulo Roberto"@100.64.0.3 whoami
```
Expected: retorna o nome do usuário Windows, sem pedir senha.

- [ ] **Step 4: Registrar no `~/.ssh/config` do MacBook**

```
Host gab
    HostName gabinete-host.gab.internal
    User prvrc

Host gab-win
    HostName 100.64.0.3
    User Paulo Roberto
```
(Trocar `HostName` do `gab-win` para a porta 2222 — `Port 2222` — se o Step 2 caiu no fallback.)

- [ ] **Step 5: Commit**

```bash
cd ~/dev/homelab
git add remote-access/windows/install-openssh.ps1
git commit -m "feat(remote-access): OpenSSH Server no Windows, restrito à tailnet

Chave apenas, bind em 100.64.0.3. administrators_authorized_keys
(não ~/.ssh/authorized_keys) porque o usuário é membro do grupo
Administrators — OpenSSH trata essas contas como caso especial.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 5: Fase 2 — RDP restrito à tailnet

**Requer PowerShell como Administrador no PC-PR.**

**Files:**
- Create: `remote-access/windows/harden-rdp.ps1`

**Interfaces:**
- Consumes: ACL da Task 2 (`tag:admin → tag:app-host:3389`).
- Produces: nada consumido por outra task.

- [ ] **Step 1: Script de hardening, versionado**

`remote-access/windows/harden-rdp.ps1`:
```powershell
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
    Write-Warning "NLA NÃO está ativo — habilitar em Configurações > Sistema > Área de Trabalho Remota."
}
```

- [ ] **Step 2: Rodar e verificar**

```powershell
powershell -ExecutionPolicy Bypass -File remote-access\windows\harden-rdp.ps1
Get-NetFirewallRule -DisplayGroup "Remote Desktop" | Select-Object Name,@{n='Remote';e={($_ | Get-NetFirewallAddressFilter).RemoteAddress}}
```
Expected: as três regras mostram `100.64.0.0/10` em `Remote`, não `Any`. `NLA ativo: True` no output do script — se `False`, tratar como bloqueio (não prosseguir sem NLA).

- [ ] **Step 3: Testar do MacBook (ou simular do WSL via tailnet)**

```bash
nc -zv -w5 100.64.0.3 3389
```
Expected: conecta (porta aberta pela tailnet). De uma rede fora da tailnet (ex.: 4G sem VPN), o mesmo teste deve **falhar** — confirma o escopo.

- [ ] **Step 4: Commit**

```bash
cd ~/dev/homelab
git add remote-access/windows/harden-rdp.ps1
git commit -m "feat(remote-access): RDP restrito à faixa da tailnet (100.64.0.0/10)

As 3 regras de firewall estavam em Profile: Any, abertas à LAN
inteira. Confirma NLA ativo.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 6: Fase 2d — Caminho UDP direto (CONDICIONAL — só se a Task 1 resultou em RELAY)

**[PULADA em 2026-08-01]** — resultado da Task 1 foi assimétrico (PC-PR/RDP já DIRECT via UPnP;
gabinete-host/SSH ficou RELAY). Decisão do usuário: não vale o esforço de mexer no roteador só para
melhorar a latência do SSH, que já tolera bem DERP — o caso sensível (RDP) não precisa disso. Fica
descrita abaixo para o caso de precisar revisitar (ex.: se o SSH relayado se mostrar problemático na
prática).

**Antes de começar:** conferir o resultado registrado na Task 1. Se foi `DIRECT`, marcar esta task como `[N/A — Fase 0 já mostrou caminho direto]` e pular para a Task 7.

**Requer acesso à UI do roteador Archer (192.168.0.1)** — manual, não scriptável.

**Achado do levantamento, importante:** o `tailscaled` do WSL está hoje em porta UDP **efêmera (45634)**, não na porta padrão 41641 — porque, na rede *mirrored*, a porta 41641 já está ocupada pelo `tailscaled` do **Windows** (`PC-PR`), que já é fixo em 41641. Os dois processos compartilham a pilha de rede do host; não podem usar a mesma porta. O WSL precisa de uma porta **diferente**, fixada explicitamente.

**Files:**
- Modify: `/etc/default/tailscaled` (WSL, não versionado — é config de pacote, não deste projeto)

**Interfaces:**
- Consumes: decisão `RELAY` da Task 1.
- Produces: nada consumido por outra task.

- [ ] **Step 1: Fixar a porta do `tailscaled` do WSL numa porta livre**

```bash
sudo ss -ulnp | grep -E ":(41641|41642)\s"
```
Confirmar que `41642` está livre (nada deve aparecer). Editar `/etc/default/tailscaled`:
```
PORT="41642"
```
Aplicar:
```bash
sudo systemctl restart tailscaled
ss -ulnp | grep tailscaled
```
Expected: `0.0.0.0:41642` (não mais `45634`).

- [ ] **Step 2: Forward de porta no Archer (manual, UI do roteador)**

Acessar `http://192.168.0.1` (admin do TP-Link Archer). Em Encaminhamento/Virtual Servers/Port Forwarding, adicionar duas regras:
- `UDP 41641` externo → `192.168.0.151:41641` interno (tailscaled do Windows — porta já fixa, sem mudança).
- `UDP 41642` externo → `192.168.0.151:41642` interno (tailscaled do WSL — a porta nova desta task).

(Opcional, ajuda descoberta de caminho direto via STUN) `UDP 3478` externo → `192.168.0.151:3478` — o Headscale já escuta isso (`stun_listen_addr: 0.0.0.0:3478`), só falta o forward externo.

- [ ] **Step 3: Repetir a medição da Task 1, do MacBook fora da LAN**

```bash
tailscale ping PC-PR
```
Expected: agora `via <IP>:<porta>` (não mais `via DERP`). Se ainda vier `via DERP` depois do forward, o NAT do provedor de internet pode estar bloqueando UDP inbound apesar do forward — registrar como limitação conhecida, não repetir o forward indefinidamente.

- [ ] **Step 4: Commit (só a mudança documentada — `/etc/default/tailscaled` não é deste repo)**

```bash
cd ~/dev/homelab
cat >> remote-access/docs/superpowers/specs/2026-08-01-acesso-remoto-macbook-design.md <<'EOF'

## Addendum — Fase 2d aplicada (preencher a data real)

tailscaled do WSL fixado em UDP 41642 (`/etc/default/tailscaled`, PORT=41642)
porque 41641 já é ocupado pelo tailscaled do Windows na pilha mirrored
compartilhada. Forward no Archer: UDP 41641→Windows, UDP 41642→WSL,
UDP 3478→WSL (STUN). Resultado do teste pós-forward: <DIRECT ou ainda RELAY>.
EOF
git add remote-access/docs/superpowers/specs/2026-08-01-acesso-remoto-macbook-design.md
git commit -m "docs(remote-access): registra a porta fixa do tailscaled WSL e o forward do Archer

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 7: Fase 3 — Break-glass via `cloudflared` no Windows

**Files:**
- Create: `remote-access/windows/cloudflared-breakglass.ps1`

**Interfaces:**
- Consumes: OpenSSH do Windows já instalado e endurecido (Task 4) — este túnel expõe exatamente esse `sshd`.
- Produces: nada consumido por outra task. **Última task do plano — sem o drill do Step 3 passar, o plano não está completo.**

- [ ] **Step 1: Criar o túnel Cloudflare (no host que já tem `cloudflared` autenticado — o WSL, via `cloudflared tunnel login` já feito para `equipe.gab.ia.br`)**

```bash
cloudflared tunnel create gabinete-breakglass
```
Anotar o `Tunnel ID` gerado. Criar o DNS record apontando para o túnel (ex.: `breakglass.gab.ia.br`):
```bash
cloudflared tunnel route dns gabinete-breakglass breakglass.gab.ia.br
```

- [ ] **Step 2: Config do túnel, rodando como serviço no Windows**

`remote-access/windows/cloudflared-breakglass.ps1` (rodar como Administrador no PC-PR, após instalar `cloudflared.exe` de https://github.com/cloudflare/cloudflared/releases — mesmo binário, build Windows):
```powershell
# Rodar como Administrador no PC-PR.
$ErrorActionPreference = "Stop"

$configDir = "$env:ProgramData\Cloudflare\breakglass"
New-Item -ItemType Directory -Force -Path $configDir | Out-Null

# Credenciais do túnel (arquivo .json gerado por `cloudflared tunnel create`
# no WSL) — copiar manualmente para cá antes de rodar este script.
$credFile = "$configDir\<TUNNEL_ID>.json"
if (-not (Test-Path $credFile)) {
    throw "Copie o arquivo de credenciais do túnel para $credFile antes de continuar."
}

@"
tunnel: <TUNNEL_ID>
credentials-file: $credFile
ingress:
  - hostname: breakglass.gab.ia.br
    service: tcp://127.0.0.1:22
    originRequest:
      access:
        required: true
        teamName: young-snow-b198
  - service: http_status:404
"@ | Set-Content "$configDir\config.yml"

cloudflared.exe service install --config "$configDir\config.yml"
Start-Service cloudflared
Write-Output "Serviço cloudflared (break-glass) instalado — confirme com: Get-Service cloudflared"
```

Configurar a política de Access para `breakglass.gab.ia.br` no dashboard da Cloudflare (mesmo team `young-snow-b198` já usado em `equipe.gab.ia.br`), restrita ao e-mail/identidade do usuário — passo manual no painel, não scriptável.

- [ ] **Step 3: Drill obrigatório — login a frio, do MacBook fora da LAN**

Este é o passo que valida a Fase 3 inteira. Requer o usuário:
1. MacBook em hotspot 4G (fora de casa), navegador **sem sessão prévia** da Cloudflare Access (aba anônima ou navegador limpo).
2. `cloudflared access ssh --hostname breakglass.gab.ia.br -- whoami` (ou `ssh` via `cloudflared access` proxy, conforme a doc do Access para SSH).
3. Completar o login SSO do zero — cronometrar quanto tempo leva.

Expected: login completa e o comando retorna o usuário do Windows, **sem** depender de WSL, Headscale, Tailscale ou DERP estarem no ar (para provar isso de verdade, rodar `wsl --shutdown` no Windows antes do drill e confirmar que o break-glass ainda funciona).

**Se o login não fechar** (provedor de identidade fora, OTP inacessível, sessão expirada): este canal não serve como break-glass. Registrar a falha, e reabrir a discussão do autologon + tela travada no Windows (descartado no design) como mitigação alternativa do boot do WSL — decisão do usuário, não deste plano.

**Addendum — execução real em 2026-08-01:** drill funcional confirmado ao vivo (MacBook em hotspot,
`cloudflared access ssh --hostname breakglass.gab.ia.br --url localhost:PORT` + `ssh` na porta local,
login via GitHub OAuth através do Cloudflare Access, retornou `whoami` do Windows). Três bugs reais
corrigidos no caminho, todos commitados no script:

1. `service install` não persiste `--config` entre starts (sempre roda "pelado", cai no perfil de
   SYSTEM) — trocado por Tarefa Agendada (`AtStartup`, como `SYSTEM`, sem depender de logon).
2. `sc.exe delete` deixa órfã a chave de registro do EventLog, quebrando a reinstalação seguinte em
   silêncio — removida manualmente antes de reinstalar.
3. O `service:` do túnel apontava para `127.0.0.1:2222`; o `sshd` (Task 4) só escuta em
   `100.64.0.3:2222` (`ListenAddress` explícito, não default) — corrigido para o IP certo. Esta foi
   provavelmente a causa raiz do "websocket: bad handshake" original, não a política/identidade do
   Access (que também foi restringida no caminho, a IdP único "One-time PIN" em vez de aceitar
   qualquer provedor — mudança válida por si, mas não era a causa).

**Não testado:** a independência real de WSL/Headscale (rodar `wsl --shutdown` antes do drill). Decisão
consciente do usuário em 2026-08-01 — o teste destrutivo derrubaria a própria sessão do Claude Code
(que roda dentro deste WSL) além do Headscale/Caddy/app da equipe em produção, e o WSL não resobe
sozinho depois (as tarefas `ONLOGON` só disparam em evento de logon, não porque a distro caiu). Fica
como dívida em aberto: o mecanismo é arquiteturalmente independente do WSL (Tarefa Agendada no
Windows, túnel outbound próprio), mas isso nunca foi *provado* sob a condição real de falha que o
break-glass existe para cobrir. Antes de contar com este canal numa emergência real, rodar esse teste
numa janela de manutenção combinada — `wsl --shutdown` no Windows, depois repetir o Step 3 deste
drill do MacBook, depois `wsl -d Ubuntu-26.04` (ou logoff/login) para religar o WSL.

- [ ] **Step 4: Commit**

```bash
cd ~/dev/homelab
git add remote-access/windows/cloudflared-breakglass.ps1
git commit -m "feat(remote-access): break-glass SSH via segundo túnel cloudflared no Windows

Canal independente de WSL/Headscale/Tailscale/DERP — só depende de
internet e da Cloudflare. Reusa o padrão já operado para
equipe.gab.ia.br. Validado por drill de login a frio (ver spec).

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```
