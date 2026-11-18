# Preparação pelo operador

Operador: operador, com sua própria conta e configuração privada. Este documento prepara a entrada de uma empresa; não autoriza operar uma versão `review_candidate`, enviar convite/mensagem ou mudar permissões. A publicação do código testado e do pacote preparado é responsabilidade do líder/operador após revisão. Exemplos usam dados fictícios e nomes de argumentos, nunca credenciais.

## Preparar o destino vazio

Confira `gh auth status`, `gh api user --jq .login` e a propriedade/administração do repositório privado da empresa, já existente. A origem do software deve ser outro repositório. O comando recusa a assinatura de código na raiz; um pacote de software aninhado na entrada da empresa é legítimo. Não use a origem do software como armazenamento de registros.

Com o Python da revisão testada e uma configuração privada nova:

```text
PYTHON -m company_pilot --config OPERATOR_CONFIG init --name NOME_CONFIRMADO --mode company --store github --repo DONO/EMPRESA
PYTHON -m company_pilot --config OPERATOR_CONFIG owner enroll-founder --github-login LOGIN_FUNDADOR --request-reference PEDIDO_CONFIRMADO
```

`init` prepara `company-state`, inclusive no repositório genuinamente vazio. Nenhum convite é enviado. Primeiro confira o acesso que o fundador já recebeu/aceitou e o pedido explícito; o cadastro não substitui permissão. Repetir o cadastro informa “Fundador já cadastrado”. Falha de autenticação/409/estado/concorrência permanece pendente, sem reset, troca de conta ou conversão para modo sintético.

**Concluído quando:** snapshot identifica a empresa não sintética e o fundador correto. Não grave credencial nem exclusões pessoais em qualquer branch.

## Preparar a entrada de link único

A árvore de software precisa estar limpa e no commit exato testado. Use a revisão final fornecida pelo líder; `setup prepare` não escolhe a branch mais recente nem promove a versão a `pilot_ready`.

```text
PYTHON -m company_pilot --config OPERATOR_CONFIG setup prepare --company-url https://github.com/DONO/EMPRESA --founder-login LOGIN_FUNDADOR --software-repository DONO/SOFTWARE --code-commit SHA_TESTADO --output-dir PASTA_NOVA
```

O comando gera **somente arquivos locais**: `START-HERE.md`, `bootstrap.py`, `.company-pilot/handoff.json` e `.company-pilot/software.zip`. O ZIP contém runtime, instalador, contratos, roteiros de fontes/equipe/fundador/operador, proposta Markdown/PDF, relatório atual e a opção inativa da Nora, de uma árvore Git comprometida; não inclui corpus de testes, revisões históricas ou configuração privada. Proveniência e SHA256 constam do handoff. A empresa/branch/ID/fundador são conferidos contra o cérebro preparado, e a preparação exige conta proprietária administrativa. O pacote de revisão completo é separado.

Após revisão, publique os quatro arquivos numa branch de entrada. Não grave o ZIP em `company-state`. Não mude a visibilidade do repositório. A partir da pasta gerada, com `git` já configurado:

```text
git checkout --orphan entrada
git rm -rf --ignore-unmatch .
git add START-HERE.md bootstrap.py .company-pilot
git commit -m "Entrada do fundador"
git push -u origin entrada
git rev-parse HEAD
```

O link entregue ao fundador é `https://github.com/DONO/EMPRESA/tree/SHA`, com o SHA impresso por `git rev-parse HEAD`. Se a pasta de saída já existir e estiver vazia, `setup prepare` pode usá-la. Se ela tiver arquivos, apague só essa pasta local e rode de novo. Não apague `company-state`.

Entregue somente `https://github.com/DONO/EMPRESA/tree/SHA_DA_ENTRADA`, abrindo o START-HERE daquela revisão. O link simples do repositório pode abrir `company-state`; use o link imutável da entrada. Confira do lado do fundador, com credenciais que leem apenas a empresa:

```text
PYTHON -m company_pilot verify-release --company-url URL_IMUTAVEL_DA_EMPRESA
```

O verificador baixa bytes pela API da empresa, confere hash, comentário de commit do ZIP, manifesto e caminhos seguros. A origem separada é proveniência; não há leitura oculta do outro repositório. Falha nomeia operador como responsável pela preparação. Se precisar substituir um pacote, publique outra revisão e entregue outro link imutável; preserve o anterior para inspeção.

**Concluído quando:** o link abre START-HERE e o pacote exato é recuperável/verificado com acesso exclusivo à empresa. Teste fake não comprova disponibilidade remota; guarde o recibo da verificação real autorizada da publicação de código, sem dados de empresa.

## Acesso, análise e canal centrais

Durante a conversa, o fundador registra quem pode ler/modificar com `setup access`; o comando preserva pedidos incompatíveis. Confira `policy show`, colaboradores completos e a decisão explícita antes de qualquer alteração humana de acesso. Não faça concessões automáticas. Com pedido atual e acesso efetivo conferido:

```text
PYTHON -m company_pilot --config OPERATOR_CONFIG owner verify-access
PYTHON -m company_pilot --config OPERATOR_CONFIG policy show
```

A verificação é de leitura e grava a atestação canônica; não envia convite. O comando gera e preserva sua identidade no recibo privado. Repita o comando original após uma interrupção; cada tentativa confere novamente o acesso nativo. Mudança no pedido ou no resultado inspecionado cria outra verificação. A conta de operador/operador e colaboradores existentes devem estar incluídos no acesso pedido compatível; ausência na lista de participantes não remove direitos nativos. Pedido de leitura isolada em repositório pessoal permanece bloqueado, sem forçar escrita.

Se houver registros empresariais existentes, siga [fontes-empresa.md](../founder/fontes-empresa.md) após o fundador confirmar o acesso humano efetivo. Identifique as fontes reais com seus responsáveis e confira inventário, identidade, destino privado, cópia/mudanças e acesso permitido/negado. Mostre cobertura, falhas, última sincronização e interpretação pendente antes da entrevista. Uma conta conectada ou arquivo apenas arquivado não significa acervo inteiro entendido. Conexão pendente pode ficar explícita enquanto a entrevista autorizada avança; acesso humano pendente ou incompatível mantém a conversa somente no espaço privado do fundador, sem publicação pelo operador. O roteiro de fontes mantém seus próprios gates de release, arquivo privado e autorização; esta referência não conecta conta ou serviço.

## Grupo de perguntas da Nora

A Nora fala num grupo de WhatsApp. O nome do grupo e quem pode responder entram na rota, durante a configuração, e não ficam gravados no código. O fundador escolhe o nome do grupo e preenche quem responde. Este repositório não traz nome de pessoa nem número. O operador, que é o `founder_sender` da rota, não responde: com as pessoas da rota configuradas, uma mensagem vinda dele é recusada. Se duas pessoas derem respostas diferentes, a mensagem seguinte cita as duas e faz uma pergunta só.

A entrevista não depende de um programa chamado `claude`. O assistente do fundador segue [assistente.md](assistente.md): `operator prompt`, depois `operator apply --file`. Se não houver assistente, `operator commit-literal` guarda a frase como foi escrita. A chave `operator.claude_executable` é opcional e só serve para quem já tem esse programa configurado.

Durante a entrevista, se quiser que a fila ande sozinha no computador autorizado:

```text
PYTHON -m company_pilot --config OPERATOR_CONFIG operator watch
PYTHON -m company_pilot --config OPERATOR_CONFIG operator health
```

Confira o processo real e a atualização do recibo local; saúde antiga não é prova de liveness. `setup status` mostra trabalhos, idade real, tentativas e falhas disponíveis. Registre tempos reais observados, sem prometer ETA. Não reenvie respostas nem apague fila para contornar análise pendente. Se um comando de entrada for interrompido, repita o comando original, preservando `operation-receipts.json`. A trava do sistema é liberada com o processo e a mesma identidade é reutilizada. O arquivo de trava pode permanecer sem bloquear a retomada; não o remova nem use sua idade para disputar uma operação ativa. Respostas futuras a novas perguntas usam o recibo de `interview next`.

Nora exige a rota WhatsApp, conta/conversa/fundador verificados e processo central próprio conforme [instruções da Nora](../deploy/nora-vps/README.md). O fundador não inicia outra rota. Sem prova de envio/recebimento real, a etapa de canal e o ciclo completo continuam pendentes. Pessoas delegadas ficam aguardando, sem contato inventado. Participantes sem login recebem notas privadas de convite pendente; o comando não envia nada.

**Concluído quando:** acesso efetivo corresponde ao pedido; o processo de análise está realmente ativo durante setup; canal, consentimento e captura mantêm estados separados. Somente a primeira tarefa real com pergunta válida, resposta e cartão revisado comprova o ciclo; nenhum teste sintético ou `release_verified` substitui essa prova ou a aceitação do operador.
