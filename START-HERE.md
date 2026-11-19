# Comece por aqui

Este repositório é o software do mapa de trabalho. Ele chega vazio: não traz empresa, pessoa, semana nem mapa de exemplo. Você é o assistente do fundador. Fale com ele em português. Rode os comandos na raiz deste repositório, com `python3`.

Não conecte conta, não envie mensagem e não ligue a captura. A captura só começa quando o fundador escolher, no computador dele e no de cada pessoa da equipe. Os passos do operador (quem prepara o repositório privado da empresa) estão só em `docs/operator-setup.md`.

## Versão

No Git, `founder/pilot-release.json` traz `code_commit` igual a `HEAD`. Isso quer dizer: a versão é o commit desta pasta. O pacote Python desta pasta é `0.1.3`.

```text
git rev-parse HEAD
```

Se o piloto já está em uso, atualize o código na pasta que o serviço usa, sem apagar a configuração nem o cérebro. O checkout do fundador está detached: não use `git pull`. Não rode `python3 -m pip install -e .` e não use `--break-system-packages`. O Python do sistema recusa instalação (PEP 668). Os comandos são `python3 -m company_pilot`, sem instalar. Na pasta do checkout: `git fetch origin` e `git checkout COMMIT_PUBLICADO`. A partir da `0.1.1`, uma citação da análise pode diferir um pouco da fonte (acento em NFC ou NFD, aspas, travessão, espaços, maiúsculas ou um erro curto de digitação) e o programa grava o trecho original; citação inventada continua recusada. Se `interview next` ou `status` pedir o caminho manual, use `operator prompt` e `operator apply`, ou `operator commit-literal`.

Na `0.1.2`, dois fatos da entrevista sobre o mesmo assunto deixam de ser marcados como conflito só porque o texto é diferente. Para uma instalação que já tem esses conflitos, use a configuração que o fundador já tem no lugar de `CONFIG`:

```text
python3 -m company_pilot --config CONFIG repair context-conflicts --dry-run
python3 -m company_pilot --config CONFIG repair context-conflicts
```

O primeiro comando só mostra. O segundo grava o reparo. Não apaga o histórico. Um nome confirmado ajuda a ligar a janela ao projeto. A partir da `0.1.3` ele não segura mais a captura: no computador de trabalho, o que não for claramente pessoal entra no mapa. Confirme só os nomes que o fundador aceitar, um par por vez:

```text
python3 -m company_pilot --config CONFIG setup confirm-names --name "NOME" --kind record_name
```

O tipo pode ser `record_name`, `record_ref`, `company_alias` ou `business_account`. Não confirme um nome que ele não aceitou. Não conecte conta, não envie mensagem e não ligue captura neste passo.

## 1. Rodar, sem instalar

Na raiz do repositório, com Python 3.12 ou mais novo, use o módulo direto. Não instale no Python do sistema.

```text
python3 -m company_pilot
```

Não rode `python3 -m pip install -e .` e não use `--break-system-packages`. Não crie conta e não baixe serviço.

## 2. Criar a configuração local

Pergunte ao fundador o nome da empresa dele. Use a resposta dele, do jeito que ele disser. Não invente nome e não reaproveite um nome de exemplo.

```text
python3 -m company_pilot --config "$PWD/.company-pilot-local/company.json" init --name "NOME_QUE_ELE_DISSE" --mode synthetic --store local --root "$PWD/.company-pilot-local/brain"
python3 -m company_pilot --config "$PWD/.company-pilot-local/company.json" status
```

Troque `NOME_QUE_ELE_DISSE` pelo nome que ele acabou de dar. O primeiro comando grava `company.json`. O segundo lê essa configuração. O modo é local e sintético: ainda não é o repositório privado da empresa e não pede GitHub.

Se aparecer `CONFIGURACAO_AUSENTE`, o `--config` não aponta para o arquivo que o `init` acabou de gravar. Rode de novo os dois comandos, nesta ordem, a partir da raiz do repositório.

## 3. Fontes, sem escrever JSON

```text
python3 -m company_pilot --config "$PWD/.company-pilot-local/company.json" setup sources --output "$PWD/.company-pilot-local/watchdog.json" --catalog "$PWD/.company-pilot-local/catalog.db" --archive-root "$PWD/.company-pilot-local/archive" --source-root "$PWD/.company-pilot-local/sources"
```

O comando grava o arquivo. A leitura fica desligada. Nenhuma conta é conectada. O roteiro longo, para quando o fundador quiser ligar uma fonte de verdade, está em `founder/fontes-empresa.md`.

## 4. Semana de observação, nos computadores dele e da equipe

Ainda não ligue a captura. Quando o fundador quiser observar o trabalho de verdade:

- No computador dele, siga `founder/configuracao-fundador.md`.
- Em cada computador da equipe, siga `founder/configuracao-equipe.md`.

Os dois roteiros instalam o coletor com a captura desligada. A captura só é ligada no passo `enable-capture`, depois que a pessoa daquele computador aceitar. Uma pessoa só mencionada no trabalho não foi inscrita. Não envie WhatsApp e não conecte conta neste passo.

## 5. Ler as capturas dele e montar o mapa

O ciclo local não traz semana pronta. Ele só lê um arquivo JSON que você montar com as janelas reais dele. Cada item precisa de `when` (data e hora em ISO, com fuso), `app`, `record_ref` e `text`. O `text` é o texto da janela, copiado, sem resumir e sem completar.

Grave esse arquivo, por exemplo em `.company-pilot-local/capturas.json`, e rode, de novo com o nome da empresa e o nome dele:

```text
python3 -m company_pilot.local_cycle --root "$PWD/.company-pilot-local/cycle" --captures "$PWD/.company-pilot-local/capturas.json" --company "NOME_QUE_ELE_DISSE" --founder "NOME_DELE"
```

O comando não envia mensagem. No fim, ele imprime os caminhos de:

- `views/mapa.md`
- `views/insights.md`
- `views/leitura-segunda.md`
- `notes/indice.md`

Abra esses arquivos na pasta `.company-pilot-local/cycle`. O detalhe do que o leitor faz com o texto está em `docs/ciclo-local.md`.

## 6. Se ainda não houver captura

Rode o ciclo sem arquivo:

```text
python3 -m company_pilot.local_cycle --root "$PWD/.company-pilot-local/cycle"
```

A resposta é só esta, e a pasta continua vazia:

```text
Ainda não há captura para ler.

Grave as janelas da sua empresa num arquivo JSON e rode de novo, na raiz do repositório:

python3 -m company_pilot.local_cycle --root "$PWD/.company-pilot-local/cycle" --captures "$PWD/.company-pilot-local/capturas.json" --company "NOME_DA_EMPRESA" --founder "SEU_NOME"

Cada item do arquivo precisa de when, app, record_ref e text, com o texto real da janela. Nada foi inventado e nenhuma mensagem foi enviada.
```

Não invente captura, tarefa ou pessoa para preencher o mapa.

## Atualizar uma instalação que já captura

Na `0.1.3` a captura do computador de trabalho entra como trabalho. O que é claramente pessoal fica só no Mac: as exclusões já gravadas pela pessoa, mais banco e corretora pessoais, saúde pessoal, namoro, streaming, redes sociais pessoais, gerenciador de senha, janela anônima e tela bloqueada. WhatsApp, LinkedIn e YouTube não estão nessa lista. Senha continua redigida. A lista completa está no `CHANGELOG.md`, na entrada `0.1.3`.

A imagem da janela de trabalho também entra, ligada à fonte em `attachments/`. Sem a permissão de Gravação de Tela, o texto continua.

Na pasta do checkout que o serviço usa, com `CONFIG` da empresa e `DEVICE_CONFIG` do coletor:

```text
git fetch origin
git checkout COMMIT_PUBLICADO
python3 -m company_pilot --config CONFIG setup screenshots --founder yes --team yes
python3 -m company_pilot --config CONFIG setup consent --participate yes --screenshots yes --reviewed-defaults
python3 -m company_pilot.desktop enable-capture --config DEVICE_CONFIG
launchctl kickstart -k "gui/$(id -u)/com.company-pilot.desktop"
python3 -m company_pilot.desktop screenshots --config DEVICE_CONFIG
python3 -m company_pilot.desktop status --config DEVICE_CONFIG
```

Não use `git pull`, não instale com pip e não use `--break-system-packages`. Se `screenshots` disser que a Gravação de Tela não está autorizada, abra Ajustes do Sistema > Privacidade e Segurança > Gravação de Tela, adicione o coletor e rode de novo o `launchctl kickstart`. Confira `actually_taking` e, no repositório da empresa, as primeiras capturas em `views/fontes.md` e as atividades em `views/mapa.md`. A quarentena que ainda estiver no Mac é relida nesse ciclo.

## Grupo da Nora

O nome do grupo e quem pode responder entram na rota, na hora da configuração, e não ficam neste repositório. Não há nome de pessoa nem número aqui. O fundador preenche os dele. O operador, que envia pela rota, não responde quando há pessoas configuradas para responder. Se duas respostas divergirem, a Nora manda uma frase só: cita as duas respostas e faz uma pergunta. O modelo da mensagem, em português e com o nome de quem recebe, está em `company_pilot/nora/presentation.py`. O exemplo de rota, com espaços em branco para preencher, está em `deploy/nora-vps/README.md`. Nada disso é enviado aqui.

## O que só o operador faz

Preparar o repositório privado da empresa e o link único. Isso não faz parte dos comandos acima. Está em `docs/operator-setup.md`. Nesse repositório privado, não rode `init`: o operador já preparou o cérebro.

Neste exercício inicial, pare se algo pedir senha, conta real ou envio de mensagem. Não ligue a captura aqui. Uma instalação que já captura segue a seção de atualização acima, inclusive a imagem da janela de trabalho.
