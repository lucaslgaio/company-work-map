# Conectar as fontes da empresa ao watchdog

**Rascunho em validação. Não há conta de empresa ou destino Supabase conectado nesta entrega.** Este roteiro é para o assistente com comandos do operador central. A preparação local não comprova importação real, permissão efetiva do arquivo privado, capacidade do acervo inteiro ou interpretação do seu conteúdo.

O watchdog deve conseguir ler o conjunto de dados da empresa: arquivos, Drive e bases de dados. O objetivo é usar os registros existentes e suas mudanças para explicar o trabalho e suas conexões. O arquivo original continua na origem; a cópia versionada permite conferir qual conteúdo sustentou uma descrição. Uma alteração numa planilha não comprova, sozinha, quem executou ou aprovou uma atividade.

## 1. Descobrir o conjunto de fontes

Use as identidades e conexões já confirmadas para inventariar contas da empresa, Drives compartilhados, pastas empresariais e sistemas de dados. Mostre o conjunto encontrado e o que falta conectar. Não use a conta pessoal ou o Drive da Tom do operador por suposição. Uma conta Google autenticada não representa automaticamente todas as contas da empresa.

Para cada identidade Drive, confirme `about.user.permissionId` e a identidade visível com a própria pessoa responsável. O conector confere esse identificador antes de ler o acervo. Ele pagina a lista de Drives compartilhados e mantém inventário e cursor por origem. Listagem incompleta e arquivo sem download ficam pendentes, com diagnóstico. Atalho preserva a referência ao alvo; não comprova cópia de um alvo fora do conjunto conectado.

Os adaptadores entregues são Google Drive, diretório empresarial explícito e snapshot consistente de SQLite. Outras bases precisam do nome real do sistema e do conector correspondente; não chame uma exportação manual isolada de sincronização contínua. Registre a fonte ainda não atendida. Diretórios são reconciliados por nova varredura; não existe observador nativo de arquivos nesse adaptador. Links simbólicos exigem uma origem explícita e não são seguidos para outra área do computador.

## 2. Gerar a configuração, sem escrevê-la à mão

Não crie `watchdog.json` e não invente um identificador. No computador do operador, com a configuração da empresa já gerada:

```text
PYTHON -m company_pilot --config COMPANY_CONFIG setup sources --output CAMINHO_PRIVADO/watchdog.json --catalog CAMINHO_PRIVADO/catalogo.db --archive-root PASTA_PRIVADA_ARQUIVO --source-root PASTA_DAS_FONTES_SINTETICAS
```

O comando grava o arquivo e deixa a leitura desligada. Nenhuma conta Google, Supabase ou WhatsApp é conectada. A pasta de arquivo local só vale para o exercício sintético. Conta real continua pendente até o operador preparar um destino privado de propósito.

## 3. O que o arquivo gerado faz

O comando `setup sources` grava a configuração e deixa a leitura desligada. Não edite esse arquivo à mão e não invente um identificador. Nenhuma conta Google, Supabase ou WhatsApp é conectada neste passo.

O arquivo completo, quando o operador preparar um destino privado de propósito, fica fora do mapa. O cérebro recebe só referências e trechos. Não coloque vídeos, backups ou um Drive inteiro no repositório do mapa. Enquanto o destino real não estiver conferido, a leitura continua desligada. Um exercício sintético usa a pasta local que o comando já gravou.

Credenciais, se um dia existirem, ficam só no ambiente do operador. Não as escreva no arquivo, no Git, em cópias da equipe ou em mensagens.

## 4. Conferir importação, atualização e leitura do mapa

Ative `enabled` somente após liberação `pilot_ready`, identidade, inventário, acesso e destino verificados. Os comandos usam o Python e o código do operador central:

```text
PYTHON -m company_pilot.watchdog --config WATCHDOG_CONFIG sync
PYTHON -m company_pilot.watchdog --config WATCHDOG_CONFIG status
PYTHON -m company_pilot.watchdog --config WATCHDOG_CONFIG changes --limit 50 --after 0
```

`status` e `changes` leem o catálogo local, sem consultar credenciais. `content_complete` se refere ao inventário de conteúdo daquele escopo; fila pendente do mapa aparece separadamente. Fonte não configurada não está coberta. Percorra `has_more` usando `next_after` até o fim do histórico desejado. Nenhum código zero substitui a conferência dos estados e do conjunto de fontes.

Na verificação controlada, confirme um arquivo inicial e seu hash, uma edição, uma renomeação no Drive, uma remoção ou perda de acesso e uma repetição sem duplicatas. Confira que a versão anterior continua recuperável. A indicação de remoção do Drive pode significar perda de acesso; não a descreva automaticamente como exclusão pela empresa.

```text
PYTHON -m company_pilot.watchdog --config WATCHDOG_CONFIG inspect --source ORIGEM --record ID_ORIGINAL --revision REVISAO_DO_CATALOGO
PYTHON -m company_pilot.watchdog --config WATCHDOG_CONFIG compare --source ORIGEM --record ID_ORIGINAL --before REVISAO_ANTERIOR --after REVISAO_ATUAL
```

`inspect --output-file NOVO_ARQUIVO_PRIVADO` recupera os bytes arquivados sem sobrescrever um arquivo existente. `compare` mostra diferença do extrato disponível e mudança de hash; ausência de diferença textual não prova igualdade de um binário não interpretado. Texto, Office e SQLite têm extratores limitados; PDF, imagens, vídeo e outros binários podem estar arquivados sem conteúdo interpretado. Trechos para o Git têm limite adicional de 16 KiB. Confira as marcas de extração parcial; não conclua que o restante foi lido.

Depois confira os trabalhos de interpretação, as fontes `company_record` e as conexões no mapa. Arquivo sincronizado, fonte recebida, interpretação pendente e conhecimento confirmado são estados diferentes. Procure a evidência existente antes de repetir ao fundador uma pergunta já respondida nos registros. Sem evidência suficiente ou diante de conflito, conserve a dúvida e sua fonte.

`watch` repete o ciclo no intervalo configurado; `Ctrl+C` para. Não registra serviço nem altera o início automático. O computador central precisa permanecer disponível. Dimensionamento real, recuperação após reinício e instalação de operação contínua exigem seus próprios recibos. Não troque o processo central ou instale cron/serviço durante este roteiro por inferência.

**Concluído quando:** conjunto de fontes e lacunas está explícito; cópias, versões e acessos foram verificados; mudança/repetição/recuperação funcionam; o mapa mostra fonte e estado da interpretação. Sem uma conexão real, entregue somente o preparo e o que foi demonstrado localmente.
