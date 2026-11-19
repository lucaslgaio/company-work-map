# Configurar o piloto com o fundador

**Versão liberada para o piloto (`pilot_ready`).** Mesmo assim, captura, contas e mensagens só ligam com a autorização de cada pessoa, passo a passo.

Este roteiro é para o assistente local com acesso a comandos. A entrevista não depende de um programa chamado `claude`: siga `docs/assistente.md`. O ciclo captura → pergunta → resposta → cartão, só com dados sintéticos, está em `docs/ciclo-local.md`. Conduza a conversa com uma pergunta por vez. Consulte primeiro ambiente, recibos, identidade, política e pendências; pergunte ao fundador apenas sobre negócio, escolhas e confirmações que faltam. Mostre a consequência de cada escolha antes de gravá-la. Empresa sem equipe participante pula a etapa de convites.

## 1. Abrir o único link entregue

O operador entrega um link privado da **empresa**, com `START-HERE.md` numa revisão preparada. Siga esse arquivo. A pessoa entra na própria conta GitHub e aceita o acesso; o assistente confere `gh auth status` e `gh api user --jq .login`. Convite pendente continua pendente.

O bootstrap usa Python 3.12+ e GitHub CLI já disponíveis. Ele baixa do mesmo repositório da empresa o pacote preparado, confere seu hash e gera a configuração privada. A origem do software aparece em `software_repository` e `code_commit`; os registros vão para `company_repository`, na branch `company-state`. O fundador não precisa obter acesso a outro repositório nem escolher branches. A revisão do pacote e a revisão de runtime no manifesto podem ter papéis diferentes; confira os recibos de correspondência entregues pelo operador, sem exigir que commits de papéis diferentes sejam iguais. Um ZIP de revisão separado não substitui esse pacote verificado.

Guarde os caminhos `source_directory`, `interpreter` e `config` do recibo. Execute os próximos comandos nessa pasta, com o interpretador detectado e `--config` apontando para a configuração gerada. Nos exemplos, `PYTHON`, `COMPANY_CONFIG` e `COMPANY_URL` são valores que o assistente obtém dos recibos, não perguntas ao fundador. No PowerShell, use `&` para executar um caminho em variável. Passe relatos como argumentos literais, preservando aspas e caracteres da pessoa.

```text
PYTHON -m company_pilot verify-release --company-url COMPANY_URL
PYTHON -m company_pilot --config COMPANY_CONFIG setup status
```

O comando não cria outra empresa nem concede acesso. Falha de revisão, hash, destino ou cadastro volta ao operador com o diagnóstico. Preserve os arquivos existentes. O operador prepara a empresa e cadastra o fundador. Nesse repositório privado, o assistente do fundador não executa `init` nem recebe a conta de proprietário. O `init` local de `START-HERE.md` vale só para o exercício neste repositório de software, com o nome que o fundador disser, e não cria a empresa online.

**Concluído quando:** o recibo identifica a empresa, a conta do fundador, a revisão imutável e os caminhos locais, com acesso e impedimentos explícitos. Com pacote em revisão, aguarde a liberação antes de instalar ou operar.

## 2. Decidir quem pode ler e modificar os registros

Confirme quem deve ler e quem deve modificar os registros, inclusive fontes, respostas e histórico. Aproveite as pessoas identificadas na conversa, mas confirme separadamente quem participará da coleta. Uma pessoa citada no trabalho pode não participar.

Explique o limite concreto: neste armazenamento, acesso ao repositório alcança o conjunto dos registros e do histórico ali guardados. Em repositório pessoal, colaboradores têm leitura e escrita; não há leitura isolada. O proprietário mantém acesso administrativo. Em organização, leitura isolada pode ser compatível, sujeita à verificação completa. Restrições a registros próprios ou categorias ainda não são aplicáveis por este adaptador.

Registre o pedido real mesmo se incompatível. Leitura isolada, restrição por categoria ou recusa de acesso amplo não viram autorização para todos escreverem. Mostre pedido, acesso efetivo e impedimentos. A coleta e a entrevista compartilhada ficam bloqueadas enquanto houver divergência. Considere operador, proprietário e todos os colaboradores existentes; tirar alguém da lista não remove seu acesso GitHub.

O assistente traduz cada decisão confirmada para o comando entregue, preservando as demais pessoas já registradas:

```text
PYTHON -m company_pilot --config COMPANY_CONFIG setup access --person LOGIN --read all --modify none --request-reference REFERENCIA_DO_PEDIDO
PYTHON -m company_pilot --config COMPANY_CONFIG policy show
```

Esse exemplo representa **pedido de leitura sem modificação**, não uma recomendação. Use `all`/`none` conforme a escolha; para categorias use `selected --category CATEGORIA`, ou `--modify own` para modificação dos próprios registros. Esses pedidos ficam preservados e bloqueados quando não suportados. O comando não envia convite nem altera permissões. operador verifica o acesso nativo com `owner verify-access` em sua própria configuração após conferir o pedido; não amplie direitos para fazer a instalação passar.

**Concluído quando:** cada pessoa tem pedido registrado, resultado efetivo e pendência com responsável. Pedido incompatível permanece visível e bloqueado.

## 3. Consultar os registros existentes da empresa

Identifique com os responsáveis os arquivos, Drives e sistemas de dados que realmente pertencem à empresa. Uma conta conectada não representa automaticamente todo o acervo. Com acesso humano verificado e confirmado pelo fundador, operador segue [fontes-empresa.md](fontes-empresa.md) para preparar inventário, destino privado, cópia inicial e mudanças. O aceite de captura de tela não autoriza essa conexão empresarial.

Antes de perguntar ao fundador o que já está documentado, consulte as fontes permitidas disponíveis. Mostre fontes cobertas, falhas, última sincronização e fila de interpretação. Preserve links, versões e distinção entre fato sustentado, relato e inferência. Arquivo arquivado, extrato parcial ou análise pendente não significam conteúdo entendido; não invente importação ou cobertura completa.

Se faltar apenas conexão a uma fonte, registre a cobertura pendente com o operador e prossiga à entrevista somente com acesso humano já verificado e confirmado. Se o acesso humano estiver pendente ou incompatível, conserve a conversa e eventuais notas apenas no espaço privado escolhido pelo fundador, sem publicar conteúdo no Git compartilhado ou encaminhá-lo pelo operador.

**Concluído quando:** fontes disponíveis e lacunas têm estado e responsável, e a conversa seguinte aproveita os fatos já sustentados. Conexão real continua pendente onde identidade, escopo, acesso ou destino ainda não foram comprovados.

## 4. Aprender como o negócio funciona

**Antes de publicar qualquer relato:** confira `access_policy.state: verified`, o conjunto efetivo de leitores/modificadores e a confirmação do fundador. Com acesso pendente ou incompatível, mantenha respostas e dúvidas somente na conversa privada ou nas notas privadas escolhidas por ele. Adie `interview answer`, `interview correct`, `interview defer` e `message send`; não use o operador para contornar o bloqueio. Quando houver solução compatível, verificada e confirmada, confira novamente o que a pessoa quer compartilhar. Um comando aceitar texto não comprova essa confirmação nem cria isolamento de acesso.

A descrição inicial do operador é uma hipótese. Aprenda com o fundador o trabalho real, as pessoas e responsabilidades, como as etapas avançam, onde acompanham andamento/pagamentos e quais documentos ajudam. Aproveite o que ele já contou; uma resposta pode cobrir vários assuntos.

```text
PYTHON -m company_pilot --config COMPANY_CONFIG interview next
PYTHON -m company_pilot --config COMPANY_CONFIG interview answer --text "RELATO_LITERAL"
PYTHON -m company_pilot --config COMPANY_CONFIG setup status
```

Os comandos geram os identificadores e guardam recibos privados antes do envio. Se o resultado for desconhecido, repita **o mesmo comando**: a operação usa a mesma identidade e não cria uma segunda resposta. Consulte `interview next` para apresentar a próxima pergunta; respostas iguais a perguntas diferentes ficam distintas pelo recibo da pergunta. Preserve `operation-receipts.json` e `interview-prompt.json`. A trava pertence ao processo e é liberada quando ele termina, inclusive por interrupção. Repita o comando original após a interrupção; uma execução concorrente ainda ativa continua protegida.

`pending_analysis` significa que a resposta está guardada e ainda precisa de análise. Mostre idade real da pendência, tentativas e motivo disponível em `setup status`. Na atualização para a `0.1.4`, pare antes qualquer `operator watch` da `0.1.3` (Ctrl+C, ou encerre o processo). A análise não roda neste Mac e não usa assinatura de modelo daqui. O operador externo, na conta configurada, segue `OPERATOR.md` (`operator pull`, `operator export`, `operator submit`) no computador dele. Um recibo antigo ou o armazenamento online não comprovam que ele já leu. Registre o tempo realmente observado entre envio e processamento; não prometa prazo nem repita a resposta para acelerar a fila. Erro de análise volta ao operador, com o motivo, sem apagar a resposta.

Correção e desconhecimento têm caminhos próprios, inclusive depois da conversa inicial:

```text
PYTHON -m company_pilot --config COMPANY_CONFIG interview correct --topic people --text "CORRECAO_LITERAL"
PYTHON -m company_pilot --config COMPANY_CONFIG interview defer --topic tracking --text "NAO_SEI_LITERAL" --delegate-to "NOME_DE_QUEM_RECEBE"
```

Escolha o assunto correspondente (`activities`, `people`, `stages`, `tracking` ou `documents`). Use `--delegate-to` somente quando o fundador indicar essa pessoa. O assunto fica aguardando a pessoa, com atribuição; isso não envia mensagem, cria contato ou resolve a lacuna. Sem pessoa indicada, preserve o desconhecimento. Não invente nome, etapa, documento ou ligação para terminar.

**Concluído quando:** contexto confirmado, correções, desconhecimentos e assuntos aguardando outra pessoa estão distinguíveis; nenhuma resposta guardada foi confundida com análise concluída.

## 5. Instalar e confirmar escolhas individuais

Após a liberação da versão, siga apenas a etapa de instalação do [roteiro da equipe](configuracao-equipe.md), usando o pacote já verificado. O instalador prepara os arquivos com captura desativada. Guarde `installation.json`; se a pasta instalada mudar, use seus caminhos reais para os comandos seguintes.

O padrão desta versão é gravar a imagem da janela de trabalho. Grave `setup screenshots --founder yes --team yes`, salvo se a pessoa recusar um dos dois. Cada participante ainda confirma a própria imagem no consentimento. A imagem só sai se a empresa e a pessoa aceitarem.

No computador do fundador, execute `setup device --installation-receipt INSTALLATION_JSON`, usando o recibo do instalador. Depois confira com a própria pessoa os padrões locais `cartão pessoal`, `conta pessoal`, `e-mail pessoal`, `conteúdo pessoal`, `uso pessoal` e `perfil pessoal`, além de exclusões específicas que ela escolher. Uma conta pessoal pode usar o mesmo banco/navegador da empresa; não exclua o aplicativo inteiro por suposição. Reconhecimento imperfeito continua uma limitação.

Se `setup status`, `interview next` ou `operator health` disser que nenhum nome de reconhecimento foi confirmado, a captura do computador de trabalho continua entrando como trabalho. O nome confirmado só melhora o vínculo com o projeto. Os nomes que a entrevista já citou aparecem como candidatos. Confirme só os que o fundador aceitar, com o tipo correspondente (`record_name` para projeto ou registro, `company_alias` para o nome da empresa, `record_ref` ou `business_account` quando for o caso):

```text
PYTHON -m company_pilot --config COMPANY_CONFIG setup confirm-names --name "NOME" --kind record_name
```

O comando não escolhe nomes sozinho. Uma janela sem nome confirmado entra como trabalho. Continua de fora só o que for claramente pessoal: as exclusões deste computador e a lista embutida (banco e corretora pessoais, saúde pessoal, namoro, streaming, redes sociais pessoais, gerenciador de senha, janela anônima, tela bloqueada). WhatsApp, LinkedIn e YouTube não estão nessa lista.

A imagem da janela de trabalho é o padrão. Grave `setup screenshots --founder yes --team yes` e, no consentimento desta pessoa, `--screenshots yes`. Se o macOS ainda não autorizou a Gravação de Tela, o texto segue. `screenshots` imprime `binary_path`: é o executável `mac-capture` desta instalação, não o Python, o Claude nem o Chrome. Abra Ajustes do Sistema > Privacidade e Segurança > Gravação de Tela, clique no botão +, pressione Cmd+Shift+G, cole esse caminho e confirme. Depois reinicie o serviço:

```text
launchctl kickstart -k "gui/$(id -u)/com.company-pilot.desktop"
PYTHON -m company_pilot.desktop screenshots --config DEVICE_CONFIG
```

`actually_taking` diz se a imagem está sendo gravada. `DEVICE_CONFIG` é a configuração do coletor neste computador. Com a permissão, a imagem fica em `attachments/` e a fonte aponta para ela.

Após a decisão pessoal, o assistente usa `setup consent --participate yes|no --screenshots yes|no --reviewed-defaults`, acrescentando `--personal-exclusion TERMO` apenas para termos escolhidos neste computador. O comando gera os arquivos e o recibo; os termos pessoais ficam locais. Recusa mantém captura bloqueada. Política nova exige nova conferência da pessoa, sem reaplicar um aceite antigo para contornar o bloqueio.

Siga então o diagnóstico e o registro do agente na etapa 3 do roteiro da equipe. Uma sessão interativa não comprova permissões em segundo plano.

**Concluído quando:** escolhas da empresa e da pessoa foram registradas, arquivo privado/versão conferidos e diagnóstico nativo concluído. Falha de acesso, aceite ou diagnóstico mantém captura bloqueada.

## 6. Preparar as cópias da equipe

Para cada **participante confirmado pelo fundador**, execute `setup participant --name NOME --github-login LOGIN --confirmed`. Sem login confirmado, omita `--github-login`: será gerada uma nota pronta para encaminhar com **convite pendente**, próximos passos e aceite pessoal pendente. O comando não envia convite nem mensagem.

A nota preserva o link imutável de START-HERE conferido em `setup-receipt.json` e orienta o assistente da equipe a usar `--role team`. Sem recibo válido para esta empresa/revisão, mostra **entrada pendente** com o operador; aguarde esse link antes de iniciar a configuração da pessoa.

Mostre os arquivos realmente gerados ao fundador. Ele decide o encaminhamento; operador confere o acesso solicitado. Pessoa sem computador compatível ou que recusar participação fica fora da coleta, com essa cobertura explícita. Sem equipe participante, não gere cópias fictícias. As notas não incluem termos de exclusão pessoal nem aceitam consentimento por outra pessoa.

**Concluído quando:** cada participante confirmado tem uma nota utilizável com acesso/convite/aceite reais ou pendentes; ninguém apenas citado foi inscrito automaticamente.

## 7. Fazer uma tarefa e conferir a revisão

Esta é a primeira utilização após a configuração e liberação. Faça uma tarefa real e confira `company_pilot.desktop status` e `flush` com a configuração do dispositivo. Abra `views/tarefas.md` e `views/perguntas.md` na branch `company-state`. Confira a tarefa, sua fonte e o que permanece provisório. Uma lacuna de metadados sem corpo original não comprova a tarefa inteira.

Se houver uma ligação real pendente, aguarde a próxima rodada da Nora no WhatsApp **verificado com o operador**: uma pergunta por mensagem, até três por dia, às 10h, 14h e 18h de Brasília. Responda por texto normal, sem códigos. Áudio/imagem aguardam texto nesta versão. Depois confira a resposta, a versão revisada da tarefa, sua finalidade e conexões. Uma tarefa já completa pode receber correção verdadeira pelo comando `message send --text RELATO_LITERAL`, sem inventar pergunta aberta.

Antes desse teste, o operador deve mostrar no aplicativo a conta da Nora, a conversa exata, o fundador vinculado e os recibos reais de envio/recebimento. Sem rota verificada, canal e ciclo ficam pendentes; não invente contato, antecipe mensagens ou inicie outra Nora no computador do fundador.

**Concluído quando:** tarefa → fonte online → pergunta válida → resposta normal → cartão revisado estão conferidos. Se não surgir caso real ou a rota não estiver pronta, registre separadamente captura/consulta verificadas e esclarecimento **não comprovado**, com próxima verificação combinada com o operador. Uso normal depois disso não exige logs, resumos recorrentes, início/pausa manual nem listas fixas de janelas.

Para suporte ou encerramento, siga a etapa 5 do roteiro da equipe. Parar o agente, remover software, revogar acesso e excluir registros são ações separadas; confirme com o operador o estado de cada pedido, preservando os registros existentes.
