# Design: acesso remoto ao gabinete-host a partir do MacBook

**Data:** 2026-08-01 · **Status:** implementado (Fases 0–3), revalidado ao vivo em 2026-08-04
**Scope owner:** `homelab/remote-access` (nova área; primeiro trabalho do repo)

> **Medição da Fase 0 — executada em 2026-08-04**, do MacBook fora da LAN de casa
> (a Fase 0 nunca havia sido registrada; o plano seguiu direto para a implementação).
>
> - `gabinete-host` (100.64.0.1): **só relaiado** — `via DERP(gab)`, 52–59ms,
>   `direct connection not established`.
> - `PC-PR` (100.64.0.3): **direta** — `189.58.124.237:41641`, 54ms, após um
>   primeiro pong por DERP.
> - Consequência para o risco central: o caminho até o WSL depende do DERP que
>   roda **dentro** do WSL — circular, sem rota alternativa. O caminho até o
>   Windows não tem essa dependência, o que dá alguma chance de o RDP/SSH do
>   Windows sobreviver a uma queda do WSL (não confirmado: depende de netmap em
>   cache e de o Tailscale do Windows rodar sem logon).
> - A ACL descrita abaixo como "default-deny, só `tag:gabinete → tag:app-host:8443`"
>   **não corresponde ao estado atual**: 22 (WSL), 2222 (Windows) e 3389 passam.
>   Auditar a policy antes de confiar na descrição desta seção.

## Problema

O usuário quer acessar esta máquina (Windows 11 + WSL2, host `gabinete-host` /
`PC-PR`) a partir de um MacBook Pro, para dois casos de uso: (a) trabalho normal
no desktop Windows, e (b) consertar a máquina remotamente quando algo quebrou —
estando fora de casa na maior parte das vezes.

A ideia inicial era RustDesk + SSH em paralelo. A investigação mudou o
diagnóstico: o transporte de rede já existe (tailnet Headscale, ver abaixo);
faltam os serviços finais (SSH, RDP) e falta endurecer o que já está de pé.

## Estado real do host (levantado, não presumido)

- **Tailnet Headscale já em produção**, servindo hoje o app `controle-processos`
  (`~/dev/controle-processos/docs/ops/acesso-remoto-OPERACAO.md`). Nós:
  `gabinete-host` (100.64.0.1, tag `app-host`, é o WSL), `PC-PR` (100.64.0.3,
  tag `gabinete` — é o mesmo hardware, o lado Windows), `MacBook-Pro`
  (100.64.0.2, tag `gabinete`), `PC-ADRIANA` (100.64.0.4, tag `gabinete`).
- **Control plane + DERP embutido rodam dentro do WSL** (`headscale.service`),
  atrás do Caddy na 8443, com forward 443→8443 feito pelo Windows
  (`netsh portproxy`) por causa do módulo bancário Warsaw ocupando a 443
  nativa. `derp.urls: []` no config — o DERP map público da Tailscale está
  desligado; só existe a região própria (`999 gab`).
- **O WSL só sobe com logon interativo** no Windows (tarefas `ONLOGON`, não
  `ONSTART`); `AutoAdminLogon` está vazio hoje. Sem logon após um reboot, o
  systemd do WSL não sobe — e com ele caem `headscale`, `caddy`, `tailscaled`.
- **ACL do Headscale é default-deny**, com uma única regra hoje:
  `tag:gabinete → tag:app-host:8443`. Nem SSH nem RDP passam nela como está.
- **`:22` (WSL) e `:3389` (Windows) já escutam em `0.0.0.0`**, abertos para a
  LAN inteira — `.wslconfig` tem `networkingMode=Mirrored`, `firewall=false`.
  SSH do WSL é ativado por `ssh.socket` (systemd socket-activation), então o
  bind se controla na socket unit, não em `ListenAddress` do `sshd_config`.
- **OpenSSH Server não está instalado no Windows** (`sshd.exe` ausente).
- **`cloudflared` já roda em produção** no WSL, expondo `equipe.gab.ia.br` via
  Cloudflare Access (team `young-snow-b198`), para o app do
  `controle-processos`. É um padrão já operado e documentado, reaproveitável.
- **Resíduo de infra**: adaptadores NordLynx/OpenVPN da migração para AirVPN
  (ver memória `media-gluetun-flap-healthcheck`), inofensivos mas ociosos no
  Windows.

## Risco central identificado

O caso de uso (b) — consertar remotamente, de fora — colide com uma falha de
arquitetura: **o control plane da tailnet vive dentro da própria coisa que
pode quebrar.** Se o WSL não sobe (reboot sem logon), o Headscale cai, o único
DERP cai junto, e SSH/RDP sobre a tailnet ficam inalcançáveis — inclusive o
lado Windows, que não depende do WSL para nada além de rotear por ali.

Alternativas descartadas para esse risco, com o porquê:

- **DERP público como fallback** (`derp.urls` apontando para o map da
  Tailscale) — não resolve o lockout de fato. A migração de região de um peer
  se propaga pelo *netmap*, que vem do control plane; com o Headscale caído,
  MacBook e PC-PR muito provavelmente migram para regiões públicas diferentes
  (cada um para a mais próxima geograficamente) e nunca se encontram. E um
  MacBook que reinicia em viagem provavelmente perde o netmap em cache por
  completo — arquitetura do Tailscale sugere refetch a cada start do daemon,
  não persistência entre reboots. Não verificado em fonte oficial; tratado
  como pessimista por padrão.
- **Autologon + tela travada no Windows** — resolveria o boot do WSL, mas tem
  custo de segurança real (senha em LSA secret) e só é necessário se não
  houver canal de socorro independente do WSL. Descartado por ora; reavaliar
  se a Fase 3 (abaixo) falhar no drill.
- **VPS dedicado para Headscale+DERP** — é a correção estrutural de verdade
  (tira o control plane de dentro do que ele protege, elimina o hack da
  8443/Warsaw, o portproxy, o oneshot de `/etc/hosts`). Fora de escopo aqui:
  é uma migração de infra compartilhada com o `controle-processos`, que está
  em produção para a equipe — não se justifica só pela conveniência pessoal
  deste projeto. Fica registrado como próximo passo se o risco se repetir.
- **RustDesk** — a ideia original. Descartada como instrumento, mas o
  princípio que ela carregava (canal de socorro independente do que pode
  quebrar) é real e vira a Fase 3, com um instrumento já operado no host
  (`cloudflared` + Access) em vez de um serviço novo.

## Decisão

Quatro fases, nesta ordem — a Fase 0 vem primeiro porque decide o formato das
seguintes; construir SSH/RDP antes de medir o transporte seria trabalho
descartável se o caminho relaiado for inviável para RDP.

### Fase 0 — Medir, antes de mudar qualquer coisa

Do MacBook, em rede fora da LAN de casa (hotspot 4G resolve):

- `tailscale status` (ou `debug netmap`) contra `PC-PR`: conexão sai `direct`
  ou `relay`? O `netcheck` rodado no host indica UPnP/NAT-PMP disponíveis no
  roteador Archer — é possível que o caminho direto já funcione sem nenhum
  forward manual. Não presumir; medir.
- Latência e banda de subida pelo caminho medido — decide se RDP relaiado por
  DERP/TCP é usável ou se vale a pena abrir o UDP do WireGuard no roteador
  (forward de porta + o `stun_listen_addr: 0.0.0.0:3478` do Headscale, que já
  está ativo, só falta o forward externo).

Resultado desta fase decide se a Fase 2 inclui ou não o pin de porta UDP do
tailscaled do WSL (hoje em porta efêmera, 45634) e o forward no Archer.

### Fase 1 — ACL mínima

Duas mudanças na `policy.hujson`, sem inventar taxonomia de tags nova além do
necessário:

- Re-taguear `PC-PR`: sai de `tag:gabinete` (tag de cliente) e entra em
  `tag:app-host` (reaproveita a tag que o host já carrega) — ele é destino,
  não origem.
- Uma tag de origem restrita no MacBook (ex.: `tag:admin`), somada à
  `tag:gabinete` que ele já tem (não substituída — a regra do app depende
  dela). Alternativa mais simples se não houver outro admin previsto: `src`
  por IP de tailnet direto (`100.64.0.2`) em vez de tag nova.
- Duas regras `accept`: origem admin → `gabinete-host:22` e origem admin →
  `PC-PR:22` + `PC-PR:3389`.

Não usar `tag:gabinete` como origem dessas regras — daria SSH/RDP a todo
device já tagueado como cliente, incluindo `PC-ADRIANA`.

Aviso operacional: bloqueio de ACL do Headscale se manifesta como timeout, não
como erro explícito — se algo não conectar depois disso, checar a ACL antes de
suspeitar de firewall do SO.

### Fase 2 — Os dois acessos pedidos

**SSH no WSL:**
- Chave apenas (`PasswordAuthentication no`, `PermitRootLogin no`,
  `AllowUsers prvrc`).
- Bind restrito a `100.64.0.1`. Como o SSH aqui é `ssh.socket`
  (socket-activation), o bind se define num drop-in da socket unit, não no
  `sshd_config`. Usar `FreeBind=true` na socket unit — evita a corrida de boot
  contra a interface `tailscale0` (mesma classe de bug já visto com o
  qBittorrent e a `tun0`: bind cedo demais falha silenciosamente e volta para
  a interface errada).

**OpenSSH Server no Windows:**
- Instalar (`Add-WindowsCapability OpenSSH.Server`), habilitar o serviço.
- Bindar em `100.64.0.3`. Ordem importa: instalar e configurar o bind do
  Windows **depois** de restringir o `ssh.socket` do WSL — os dois hoje
  disputariam a porta 22 na pilha de rede mirrored compartilhada.
- Se a pilha mirrored não permitir os dois binds distintos na 22
  simultaneamente (testar, não presumir), fallback: Windows na 2222.
- Chave apenas, mesmo padrão do WSL.

**RDP:**
- As 3 regras de firewall existentes (`Profile: Any`, abertas à LAN) trocam
  para escopo `100.64.0.0/10`.
- Confirmar NLA ativo.

**No MacBook:** `~/.ssh/config` com host `gab` → `gabinete-host.gab.internal`
(MagicDNS já ativo) e host `gab-win` → IP de tailnet do PC-PR.

> **Correção 2026-08-04 — "MagicDNS já ativo" é falso no MacBook.** O servidor
> responde certo (`dig @100.100.100.100 pc-pr.gab.internal` → `100.64.0.3`,
> `NOERROR`), mas o cliente Tailscale do macOS registrou apenas o *search
> domain* `gab.internal` e **nunca instalou o mapeamento de resolver**:
> `100.100.100.100` não aparece como nameserver em nenhum resolver do sistema
> (`scutil --dns`), e a resolução pelo sistema dá `NXDOMAIN`. Por isso o
> `~/.ssh/config` real usa IPs, não nomes.
>
> Sintoma no RDP: **erro `0x104`** ("PC can't be found") ao usar
> `pc-pr.gab.internal` no Windows App. **Conectar por `100.64.0.3`** — validado
> ao vivo em 2026-08-04, fora da LAN. Conserto do MagicDNS (não aplicado, nada
> depende dele hoje): `tailscale set --accept-dns=false && tailscale set
> --accept-dns=true`.
>
> Receita de RDP que funciona, para não redescobrir: *PC name* `100.64.0.3`;
> usuário `PC-PR\Paulo Roberto` (conta **Local**, senha do Windows — RDP não
> aceita PIN do Hello); acesso vem de `Administrators` (o grupo
> `Remote Desktop Users` está vazio, e não precisa). No macOS 15+ é obrigatório
> liberar **Privacidade e Segurança → Rede Local → Windows App**, e relançar o
> app para a permissão valer.

### Fase 3 — Break-glass independente

Segundo túnel `cloudflared`, rodando como serviço no Windows, expondo o
`sshd` local via Cloudflare Access (mesmo padrão já operado para
`equipe.gab.ia.br`). Propriedades que resolvem o risco central: nenhuma porta
inbound aberta, não depende de WSL, Headscale, Tailscale nem DERP — só de
internet e da Cloudflare.

Ressalva que precisa ser testada, não assumida: o canal introduz um login
interativo de SSO no caminho de emergência. **Drill obrigatório**: completar o
login do Access **a frio** — navegador limpo, sem sessão prévia, do MacBook
fora da LAN. Se esse login não fechar em condições ruins (provedor de
identidade fora, OTP inacessível, sessão expirada), este canal não serve como
break-glass e a alternativa vira forward TCP cru no Archer para o `sshd` do
Windows, key-only (mais exposto, menos peças móveis).

Se este drill falhar, reabrir a discussão do autologon (descartado acima) como
mitigação do boot do WSL.

## Fora de escopo (deliberado)

- Monitoramento contínuo do canal de acesso (alerta se o caminho externo
  degradar) — projeto próprio; um teste de dentro de casa não prova nada sobre
  o caminho de fora, que é o que importaria.
- Migração do Headscale/DERP para VPS dedicado — ver descarte acima.
- RustDesk como instrumento — princípio absorvido pela Fase 3.
- Limpeza dos adaptadores residuais NordVPN no Windows — não relacionado, sem
  impacto neste projeto; registrar e não tocar aqui.

## Desconhecidos que a implementação precisa resolver por teste

- Se o Tailscale do Windows sobrevive sem sessão logada (modo unattended) —
  campo não confirmado no dump de prefs desta versão.
- Se `wsl.exe -d Ubuntu-26.04 -- <comando>` executado via sessão SSH
  efetivamente acorda a distro (plausível, não verificado).
- Se há BitLocker/TPM com PIN de boot no Windows — não verificável sem
  elevação; se houver, autologon sozinho não bastaria para desbloquear o
  volume.
- Persistência do netmap do Tailscale entre reboots do daemon (relevante só
  se a Fase 3 falhar e o DERP público voltar à mesa).

## Processo desta decisão

Este design passou por uma rodada de refutação adversarial depois de fechado:
cada conclusão foi atacada com o melhor contra-argumento disponível, incluindo
contra a premissa do projeto inteiro. Resultado: a conclusão "DERP público
resolve o lockout" foi derrubada (era o núcleo da segunda versão do plano); a
ordem das fases foi invertida para colocar a medição primeiro; o RustDesk foi
descartado como instrumento mas seu princípio (canal independente) sobreviveu
e virou a Fase 3, com um instrumento melhor (`cloudflared`, já operado no
host) no lugar de um serviço novo. Registrado aqui porque o plano final não
nasceu correto na primeira tentativa, e a próxima pessoa a mexer nisso deve
saber que passou por esse teste antes de aceitar as conclusões como dadas.
