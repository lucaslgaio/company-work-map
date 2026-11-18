# Coletor desktop: integração e limites verificados

Registro técnico interno de Q2/Q3, com a revisão a11 de p11. Os arquivos finais do fundador/equipe dependem da integração do produto. Esta camada não interpreta tarefas por LLM no computador dos participantes; a interpretação fica no operador no computador do operador quando estiver disponível.

A revisão a19 corrige os quatro contraexemplos independentes de p22: recusa explícita invalida o cache imediatamente; permissões são reavaliadas na chamada final de envio; cada nova ocorrência em quarentena tem identidade própria; a conversão do buffer é atômica e conta o destino uma vez. Os 46 testes locais e os quatro casos do revisor passaram, somente com dados sintéticos. O recibo atual é `desktop-a19-repair-receipt.json`; os recibos de a11 abaixo continuam preservados como evidência histórica.

## Comandos entregues

O código requer Python 3.12+, macOS 14+ ou Windows 11+. Na raiz do pacote entregue, instale numa pasta vazia própria do piloto:

```sh
python3 install/bootstrap.py --prefix "/caminho/para/piloto"
```

No PowerShell:

```powershell
py -3 install/bootstrap.py --prefix "$env:LOCALAPPDATA\CompanyPilot"
```

O instalador copia o pacote, compila o helper Swift no Mac e gera os arquivos de início automático, sem registrar serviço nem iniciar observação. Ele preserva um caminho estável do interpretador quando disponível; neste Mac o recibo usa `/opt/homebrew/bin/python3`, sem resolver o destino removível em Cellar. O diagnóstico do agente perde validade se o runtime, helper ou configuração mudar.

Ainda não há runtime Python empacotado nem instalador automático de Python/GitHub CLI. Um Mac limpo precisa de Python funcional e das Command Line Tools da Apple para compilar; o stub de `python3` não basta. O Windows usa PowerShell, UI Automation e System.Drawing. A distribuição assinada/notarizada pronta e a instalação numa máquina limpa permanecem pendentes.

Depois de obter acesso ao repositório pela própria conta GitHub da pessoa e autenticar o `gh` nessa máquina, a configuração de produção existe:

```sh
python3 -m company_pilot.desktop configure --config CONFIG --repo lucaslgaio/company-work-map --branch company-state
python3 -m company_pilot.desktop doctor --config CONFIG
python3 -m company_pilot.desktop status --config CONFIG
```

Execute o módulo a partir da instalação ou use a entrada `company-pilot-desktop` do pacote. `CONFIG` é um caminho absoluto próprio desse computador. A configuração verifica a identidade autenticada, repositório privado e direito de contribuição; gera UUID do dispositivo e cache da política. Não concede acesso, convida pessoas nem copia credenciais do proprietário. Cada máquina usa o login local da própria pessoa. Os papéis desejados e as permissões nativas do GitHub precisam ser reconciliados pelo operador; branches não separam acesso.

O adaptador usa `GitHubStore/Brain`, o mirror verificado e os envelopes de `contracts/v1`. Converte explicitamente o envelope local, usa anexos autenticados com ID/hash e envia observações em lote. O código dessa rota está entregue; execução remota por este membro ainda não foi verificada. Não se deve chamar o receptor local de teste de brain online.

## Diagnóstico no contexto correto e início automático

Os scripts gerados `startup/register-macos.sh` e `startup/register-windows.ps1` preparam `capture_enabled=false` e registram o agente para diagnóstico. Não dependem de uma concessão interativa para concluir que a tarefa terá acesso. No Mac, launchd inicia o processo na sessão do usuário; no Windows a tarefa usa `InteractiveToken` e privilégio mínimo.

O agente publica sua saúde recente: PID/pai, contexto de execução, identidade da configuração/runtime/helper e resultado da consulta nativa de permissões. O diagnóstico interativo aparece separadamente e é apenas informativo. Agente sem relato, parado, antigo, com configuração diferente ou sem permissão mantém `doctor` no código 2. No Mac a identidade de execução exige filho de launchd; no Windows existe verificação do processo pai de agendamento, ainda sem prova nativa.

A pessoa concede as permissões que o sistema operacional solicitar/identificar para a instalação real. O helper não abre pedidos de permissão automaticamente. Após verificar o diagnóstico do próprio agente, habilite uma vez durante o setup:

```sh
python3 -m company_pilot.desktop enable-capture --config CONFIG
```

Esse comando só habilita se política, acesso online e saúde nativa do agente estiverem prontos. O agente passa a observar sem start/pause diário, lista fixa de janelas ou resumo manual. Os scripts `disable-startup-*` interrompem somente a tarefa do piloto e preservam evidências/documentos. Nenhum observador recorrente foi ativado neste trabalho.

O helper Swift faz as chamadas AX/ScreenCaptureKit; Python orquestra os subprocessos. O requisito designado observado no binário local é um cdhash ad hoc. Não foi provada a persistência de concessões após atualização; reaprovação pode ser necessária. A distribuição Developer ID/notarização é uma rota oficial, mas não há prova de que uma conta paga seja obrigatória para todo primeiro teste local controlado. Fontes: [identidade de código](https://developer.apple.com/documentation/technotes/tn3127-inside-code-signing-requirements/), [assinatura local](https://developer.apple.com/library/archive/documentation/Security/Conceptual/CodeSigningGuide/Procedures/Procedures.html), [notarização](https://developer.apple.com/documentation/security/notarizing-macos-software-before-distribution).

## Captura, contexto incerto e revogação

O nome de um aplicativo é pista fraca. Referência de registro ou marcador apoiado no contexto aprendido pode classificar uma janela como trabalho; contexto pessoal explícito e campos protegidos são excluídos. Conteúdo pessoal dentro de uma janela identificada como trabalho ainda pode ser classificado incorretamente. O classificador não estabelece que uma tarefa foi concluída.

Quando a conexão com a empresa é incerta, apenas aplicativo/tipo de superfície e uma lacuna sanitizada entram no brain, que mantém uma pergunta de contexto sem inventar tarefa. Título, referência, processo/janela e horário ficam em quarentena local limitada: sem corpo de documento, screenshot ou upload desses metadados enquanto incertos. O padrão retém por até 24 horas e limita a quantidade de registros; são configurações reais do código, não medição da empresa. A expiração remove apenas essa memória temporária do coletor. Estado e quantidade pendente aparecem no diagnóstico.

Uma política aprendida posterior reclassifica os metadados e promove apenas os que passam a ter conexão com trabalho. A promoção mantém o horário original e deixa explícito que não existe corpo/imagem original. Se o contexto chegar após a retenção, a evidência desconhecida não estará disponível; a lacuna continua visível, sem reconstrução inventada.

Observações conhecidas são coalescidas por janela em episódios com primeira e última leituras; mudança contínua não vira um novo evento por poll. O padrão finaliza por idade/inatividade e mantém episódios recuperáveis após reinício. Lacunas incertas são limitadas por aplicativo/período. A fila envia lotes, não um commit por amostra. Leituras intermediárias não são preservadas integralmente e o sistema não se apresenta como gravação contínua de toda a execução.

Screenshots exigem decisão separada do fundador/equipe e contexto de trabalho conhecido. Negativa impede a chamada nativa. Captura é da janela esperada, com foco conferido antes/depois; não há fallback para o monitor inteiro. PrintWindow no Windows depende da aplicação e pode bloquear/falhar; o coletor limita o tempo e informa a lacuna. O tamanho aceito segue o limite do brain.

Política ausente, expirada ou revogada impede nova captura. A política online é atualizada e o cache tem validade curta; revogação remota pode levar até o próximo refresh para ser conhecida localmente. Na fila, revogação bloqueia a imagem e cria evento só de texto com outro ID; troca de política também preserva o evento antigo. Imagem enviada antes da revogação não pode ser retirada pela fila local. O banco local não tem criptografia própria e usa os controles do diretório do usuário.

## Recuperação e primeiro uso

Sem conexão, a fila mantém o corpo/ID e tenta novamente; recibo ausente não vira sucesso. Falha de credencial, papel ou integridade fica bloqueada para reparo, sem imprimir segredos do backend. Limite de armazenamento interrompe novos registros sem apagar os anteriores. Permissão deve ser verificada novamente no agente após atualização. Nenhum reparo remove documentos do usuário para liberar espaço.

A primeira utilização precisa terminar o ciclo completo no produto integrado: tarefa real, fonte no brain online, pergunta na próxima rodada autorizada da Nora, resposta e mapa revisto com evidência. Um título ainda desconhecido não é um fracasso silencioso: fica como pergunta de contexto e metadados locais que podem ser promovidos. A prova local abaixo não estabelece o ciclo inteiro nem autoriza antecipar uma mensagem da Nora.

## Recibos reproduzíveis e limites

```sh
python3 -m unittest discover -s tests/desktop -q
python3 docs/review/checks/desktop_volume_check.py .
python3 scripts/feasibility/local-desktop-proof.py
python3 scripts/feasibility/desktop-brain-proof.py
```

Antes da incorporação do módulo brain, o último comando admite `--brain-source CAMINHO/company_pilot` para testar a árvore explícita do outro membro; o recibo identifica essa origem.

Passaram 36 testes locais (real; execução registrada na revisão a11). O check independente passou de 240 para 11 eventos finalizados numa hora sintética de edição, com um episódio final ainda em buffer; alternar dois aplicativos incertos produziu dois sinais. Forçar a finalização do último episódio produz 12, preservando primeira/última leituras. São dados de desenvolvimento, não frequência ou capacidade medida na empresa.

O fluxo de instalação isolada em pasta com espaços executou novamente no Mac 15.7.4 arm64: compilação, configuração, fila e receptor local, sem duplicação num segundo processo. `doctor` retornou 2, com agente ausente, permissões ausentes e nada online. O fluxo com módulos reais `Brain/LocalStore` demonstrou título não pré-semeado → pergunta sanitizada → resposta guardada → correção explícita de referência pelo fundador → promoção dos metadados originais. Não executou interpretação/modelo nem Nora real.

A prova opt-in `scripts/feasibility/mac-agent-health-proof.py --run` executou um job launchd único, com captura desativada, somente para consulta de permissões. Recebeu saúde de um filho de launchd, constatou Accessibility/Screen Recording ausentes e removeu o job ao final. Isso prova diagnóstico no contexto de segundo plano; não prova captura autorizada, logon/reboot ou operação recorrente. O probe anterior com OCR falhou dentro do sandbox; o líder obteve render/OCR e captura da própria janela sintética fora dele. OCR não é dependência desta camada.

Sem comprovação por este membro: transporte remoto GitHub, papéis reais de participantes, Windows interativo, captura autorizada pelo observador, concessão preservada após upgrade, reinício/logon e ciclo Nora real. Hooks de histórico de IA não estão entregues. Os recibos JSON desta pasta distinguem cada uma dessas fronteiras.
