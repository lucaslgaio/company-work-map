# Configurar seu computador

**Versão em revisão. Instalação e operação na empresa aguardam a liberação do operador.** Uma revisão de arquivos ou teste sintético não comprova captura nativa nem autoriza ativação.

Este roteiro é para seu assistente local com acesso a comandos. A nota individual entregue pelo fundador identifica você como participante pretendido; acesso e consentimento podem continuar pendentes. Você decide sua participação e o uso de imagens neste computador. O fundador não dá esse aceite por você. Uma pessoa apenas mencionada no trabalho não foi inscrita automaticamente.

## 1. Conferir o link e a própria conta

Use o único link privado da empresa e seu `START-HERE.md`. Entre na própria conta GitHub e aceite o acesso. O assistente confere `gh auth status`, `gh api user --jq .login`, sistema, ferramentas e recibos disponíveis. Convite pendente ou conta divergente volta ao operador, preservando sua escolha de acesso; não receba credencial de outra pessoa.

O computador precisa de macOS 14+ ou Windows 11+, Python 3.12+, GitHub CLI e, no Mac, Command Line Tools da Apple para compilar o helper. Windows usa Windows PowerShell; confira também a disponibilidade de fuso `America/Sao_Paulo`. Se a distribuição Python não tiver a base de fusos, instale `tzdata` nesse interpretador. Sistema ou ferramenta ausente fica pendente com diagnóstico.

O bootstrap do START-HERE baixa o software **do próprio repositório da empresa** e verifica hash/revisão antes de usá-lo. Para equipe, o assistente acrescenta `--role team` ao comando do bootstrap. A origem do código fica registrada; você não precisa acessar outro repositório. Guarde `source_directory`, `interpreter`, `config` e a URL imutável do recibo. Não troque pela versão mais nova para contornar falha.

**Concluído quando:** conta, acesso, versão, sistema e ferramentas estão conferidos. Com `review_candidate`, aguarde liberação do operador antes da instalação. A equipe mantém sua própria decisão pessoal pendente até a etapa 3.

## 2. Instalar o pacote verificado

Execute o instalador da pasta `source_directory` já verificada. Ele copia os arquivos e prepara scripts de início com captura desativada; usa o Python existente e não cria ambiente virtual. Escolha uma pasta de instalação fora da pasta de fontes e preserve qualquer instalação com trabalho existente.

No Mac, com os caminhos detectados pelo assistente:

```sh
pilot_prefix="$HOME/Library/Application Support/CompanyPilot"
python3 install/bootstrap.py --prefix "$pilot_prefix" --config "$pilot_prefix/desktop.json"
```

No PowerShell:

```powershell
$pilotPrefix = Join-Path $env:LOCALAPPDATA 'CompanyPilot'
py -3 install\bootstrap.py --prefix $pilotPrefix --config (Join-Path $pilotPrefix 'desktop.json')
```

Use exatamente o interpretador compatível já conferido; `python3`/`py -3` acima são exemplos de invocação. Guarde `installation.json`. A partir daqui, execute na pasta instalada. `PYTHON` é seu campo `interpreter`; `COMPANY_CONFIG` é a configuração privada gerada pelo bootstrap; `CONFIG` será o caminho emitido por `setup device`. O assistente resolve esses caminhos sem pedir que você preencha JSON. No PowerShell use `&` ao executar um caminho em variável.

**Concluído quando:** o instalador termina sem erro, os arquivos/helper correspondem à instalação e o início automático ainda não foi registrado. Isso não comprova captura.

## 3. Conectar e decidir sua participação

```text
PYTHON -m company_pilot --config COMPANY_CONFIG setup device --installation-receipt INSTALLATION_JSON
PYTHON -m company_pilot --config COMPANY_CONFIG policy show
```

`setup device` gera a configuração do dispositivo com destino e branch já conferidos, sem iniciar captura. Se ela já existir, confere o destino e preserva o estado, sem substituir uma instalação diferente. O comando não altera permissões GitHub. Leitura isolada ou outra escolha incompatível permanece registrada e bloqueia a contribuição; não amplie direitos para passar o teste. Não execute `init` nem inicie operador ou Nora neste computador.

O assistente explica participação, limite de imagens da empresa e suas opções, uma pergunta por vez. Recusa mantém captura bloqueada. Confira os padrões locais `cartão pessoal`, `conta pessoal`, `e-mail pessoal`, `conteúdo pessoal`, `uso pessoal` e `perfil pessoal`; acrescente apenas termos que você escolher. Banco/navegador pode servir a contas pessoais e da empresa; a exclusão deve identificar seu contexto pessoal, sem bloquear o aplicativo inteiro por suposição.

Após sua decisão e revisão desses padrões, o assistente usa:

```text
PYTHON -m company_pilot --config COMPANY_CONFIG setup consent --participate yes --screenshots no --reviewed-defaults
```

O exemplo representa participação com recusa de imagens; **não é um aceite sugerido**. Substitua `yes`/`no` pelas suas escolhas e acrescente `--personal-exclusion TERMO` somente para termos escolhidos neste computador. O comando gera a política privada e registra na empresa apenas escolhas, versão e hash. Os termos pessoais não são enviados. Imagens exigem permissão da empresa **e** sua permissão.

Confira `company_pilot.desktop status --config CONFIG`. Aceite ausente, acesso não verificado, política nova ou arquivo privado ausente/alterado mantém captura bloqueada. Se a política mudar, reveja as escolhas; não reaplique um aceite antigo para liberar a máquina. O reconhecimento pessoal tem limites e não identifica necessariamente todo uso pessoal.

Registre apenas o início automático do seu sistema:

```sh
sh "$pilot_prefix/startup/register-macos.sh"
```

```powershell
& (Join-Path $pilotPrefix 'startup\register-windows.ps1')
```

A pessoa concede as permissões da instalação real: Acessibilidade no Mac e, se imagens forem permitidas, Gravação de Tela. Identifique o executável do agente; permissão ao terminal não comprova acesso em segundo plano. No Windows, confira UI Automation na sessão interativa real. O registro inicia diagnóstico com captura desativada.

```text
PYTHON -m company_pilot.desktop doctor --config CONFIG
PYTHON -m company_pilot.desktop enable-capture --config CONFIG
PYTHON -m company_pilot.desktop status --config CONFIG
```

**Concluído quando:** arquivo privado e escolhas correspondem à política atual; `doctor` retorna `ready: true`, transporte online e saúde recente do próprio agente; a habilitação termina e `status` confirma o estado. Código 2 mantém a etapa pendente. Testes sintéticos não substituem recibos nativos, de logon e permissões no seu computador.

## 4. Conferir uma contribuição

Faça uma tarefa real da empresa. Consulte `company_pilot.desktop status` e `flush`, ambos com `--config CONFIG`. Confirme o registro online ou uma lacuna sanitizada de contexto; guarde IDs/links. Confira `coverage`: pendências de contexto e expirações aparecem sem títulos pessoais. Exclusões pessoais não contam como trabalho perdido. Conteúdo incerto pode aguardar contexto sem envio do corpo/imagem; uma descrição pendente não significa ausência de trabalho.

O fundador verifica pergunta, resposta e mudança no mapa pelo roteiro dele. Depois da configuração, seu trabalho normal basta: sem logs, resumos recorrentes, início/pausa manual ou listas fixas de janelas.

**Concluído quando:** uma contribuição real tem registro online verificável. Pendência permanece aberta; informe ao operador código do diagnóstico, dispositivo e IDs disponíveis. Preserve fila e mirror, sem enviar credenciais ou exclusões pessoais.

## 5. Parar ou pedir remoção

Os scripts `disable-startup-macos.sh` e `disable-startup-windows.ps1`, na pasta `startup`, param o agente e desabilitam seu início automático; não alteram o aceite nem apagam a configuração. Confira o estado depois da parada. Isso não apaga fila, recibos, política, mirror ou registros online. Metadados incertos têm rotina de expiração de até 24 horas quando o coletor executa a limpeza; parar o processo não dispara essa limpeza.

Remoção do software, revogação de acesso e exclusão de registros são pedidos separados ao operador, com caminhos/IDs. Os registros e o histórico online permanecem acessíveis a quem tem acesso ao repositório. Este roteiro não executa exclusão nem define prazo de retenção online.

**Concluído quando:** a parada foi conferida e pedidos de remoção/acesso/dados têm responsável e estado explícitos.
