# Contrato Python e registros v1

Contrato implementado por `company_pilot.brain`; cópia de produto em português. O armazenamento remoto usa a branch `company-state`. Branches não separam permissões. `LocalStore` é somente E1; `GitHubStore` identifica o usuário por `/user` e verifica permissões do repositório privado. A configuração de cliente precisa declarar o arranjo de acesso nativo aceito; não há permissões inventadas por pasta.

```python
from company_pilot.brain import Brain, LocalStore, GitHubStore
brain = Brain(store)
brain.initialize(company_name, synthetic=True,
                 screenshots={"founder": False, "team": False})
brain.device_policy()
brain.upload_attachment(event_id, image_bytes, sha256, media_type="image/png")
brain.send_observation(payload)  # também: ingest_observation(payload)
brain.pending_jobs()            # id, kind, source_ids, task_ids, created_at, age_seconds
brain.snapshot()                # company, context, tasks, questions, sources, pending_jobs
brain.record_answer(question_ids, text, event_id, base_versions=None, source=None,
                    candidate_question_ids=None)
brain.interview_next()
brain.interview_answer(text, event_id)
brain.interview_correct(topic, text, event_id)  # inclusive após complete
brain.interview_defer(topic, text, event_id)    # explicitamente desconhecido; sem inventar fatos
brain.apply_analysis(job_id, result)
brain.change_map(operations, event_id, reason)
brain.transact_nora(callback, event_id=None)  # callback recebe dict mutável; retorna JSON
brain.send_observations(payloads)             # lote atômico; também ingest_batch
brain.ingest_context_gap(safe_metadata)       # somente app/tempo/IDs; sem conteúdo desconhecido
```

`transact_nora` grava estado e resultado com CAS; callback pode ser reexecutado em conflito e **não pode enviar mensagens nem executar efeitos externos**. O envio vem depois do marcador `sending` confirmado. Nora faz sua codificação/decodificação de dataclasses na borda. `record_answer` aceita lista vazia para resposta ambígua. Fonte Nora traz `kind=nora`, `route_id`, `message_id`, `account`, `conversation`, `sender`; a identidade relatada vem do vínculo de rota configurado, e a atestação registra o ator GitHub autenticado. Nunca aceitar `actor`/`performer` de payload de captura como identidade autenticada.

Resposta ambígua: passar os candidatos em `candidate_question_ids`, não em `question_ids`. O texto e os candidatos ficam imutáveis. Uma pergunta `target_kind=answer_assignment` rastreia a ligação ausente. `awaiting_disambiguation` não é uma fila manual: Nora leva a pergunta à rodada programada, e a resposta cria um job limitado aos candidatos. O resultado opcional `answer_assignments` traz `{answer_id,question_ids,evidence}`; não pode escolher atividade externa ao conjunto nem várias atividades por padrão. Um vínculo apoiado cria nova interpretação da resposta original e resolve a pergunta de ligação, sem alterar a fonte anterior. `SemanticWorker` informa `awaiting_clarification` enquanto aguarda essa resposta.

`revision` é a revisão canônica de projeção da pergunta; avança sempre que conteúdo, evidência, versão-base ou situação mudar. Conflitos de contexto usam `target_kind=context_conflict`, chave estável por tópico e resposta pelo mesmo caminho de fundador/Nora.

Correção/adiamento do contexto: `topic` é `activities`, `people`, `stages`, `tracking` ou `documents`; só fundador autorizado pode chamar. `interview_correct` retorna `source_id`, `job_id`, `topic`, `state=pending_analysis`; a fonte literal e a descrição anterior ficam imutáveis. O job de entrevista traz `interview_action={mode:correct,topic}`. O interpretador retorna apenas esse tópico, sem tarefas/nós; uma afirmação única apoiada aplica a correção, enquanto ambiguidade ou mudança concorrente abre pergunta. `interview_defer` retorna `source_id`, `question_id`, `topic`, `state=deferred_unknown`, sem chamar modelo. A pergunta estável tem `target_kind=context_deferred`; respostas por `record_answer` usam o caminho já existente e resolvem o tópico sem apagar o relato desconhecido. A fonte antiga pode continuar aguardando análise para outros tópicos; `excluded_context_topics` impede que ela sobrescreva o adiamento explícito.

`interview_next` acrescenta `deferred_topics`, `pending_context_question_ids` e `business_context_status`. `complete` significa que os cinco tópicos foram respondidos ou explicitamente adiados, não que os adiados viraram fatos. Os estados de contexto são `collecting`, `pending_analysis`, `pending_clarification`, `known_topics_recorded`. Declaração explícita de não aplicação é resposta conhecida, com fonte; não é desconhecido. Event-id vincula operação, ator, tópico e texto; replays idênticos preservam recibo/estado, e mudanças exigem novo ID.

Mudança estrutural por resposta: `SemanticWorker` aceita `map_changes` opcional no resultado estruturado, com até 20 operações `add`, `rename`, `split`, `merge`, `reassign`, `connect`, `disconnect`, `correct`. O fundador responde texto comum a Nora; não chama `change_map` nem escreve operações. O interpretador produz somente dados tipados, sem executar código ou ferramentas. Uma intenção estrutural exige citação literal da resposta verificada do fundador em `evidence` de cada operação. A afirmação de negócio de cada campo continua usando `{value,basis,evidence}`; campos omitidos ficam desconhecidos. O JSON Schema define as variantes exatas. `map_changes` não vazio exige `tasks`, `nodes`, `context_updates`, `answer_assignments` vazios e `next_question=null`; só jobs de resposta ligados a tarefas podem aplicar essa via.

O worker entrega tarefas-alvo e até 20 outras tarefas ativas, até 20 nós relevantes e as arestas incidentes, inclusive inativas. Ele calcula `map_scope={tasks:[{id,version}],nodes:[{id,version}],edges:[{id,version}],source_ids:[id]}` a partir desse snapshot e o passa separadamente a `brain.apply_analysis(job_id,result,metadata=...,map_scope=...)`. O escopo de metadados/modelo não confere autoridade. Cada operação precisa de `expected_versions:[{id,version}]` para exatamente os objetos existentes tocados; `split`/`merge` incluem todas as arestas incidentes dos predecessores. Mudanças nos campos que derivam relações (`performer`, `placement`, `handoff`, `tracking_record`) também incluem versões das arestas antigas afetadas e dos nós/arestas existentes de destino. Campos e suporte idênticos não reescrevem relações. O Brain verifica escopo, versões do snapshot e estado atual dentro da mesma transação, além da versão da tarefa no recebimento da resposta. IDs novos nulos são derivados do job/operação, sem colisão entre sucessores.

Em `split`/`merge`, a união dos `evidence_ids` dos sucessores deve conservar todas as fontes dos predecessores; fontes podem apoiar mais de um sucessor. Citações dos campos precisam estar alocadas ao respectivo sucessor ou vir da resposta atual. As fontes de entrada do job também permanecem ligadas aos sucessores, incluindo eventual esclarecimento/atribuição. Tarefas e relações anteriores permanecem versionadas, com predecessores/sucessores explícitos; relações antigas são desativadas e marcadas para reconciliação. O mesmo applier transacional atende a API direta e a resposta interpretada, sem segunda transação ou simulação de autoridade do operador como fundador. O recibo separa `founder_speaker` de `attested_by`. Propostas malformadas, não autorizadas, fora do escopo, sem alocação ou obsoletas não alteram o mapa: a fonte continua salva e o job fica pendente com diagnóstico. Respostas comuns e correção/adiamento de contexto mantêm os contratos anteriores.

Política retornada: `policy_version`, `context_version`, `issued_at`, `expires_at`, `role` (`founder`/`team`), `screenshots` com `founder`/`team`, `screenshots_allowed`, `work_signals` (lista de textos do contexto apoiado), `personal_exclusions`, `record_refs`, `tool_hints`. Conteúdo de contexto não prova sozinho que a janela atual pertence à empresa. Política vencida/ausente bloqueia screenshots no coletor.

Observação v1:

```json
{"schema_version":1,"event_id":"UUID","device_id":"UUID","occurred_at":"ISO-8601 com fuso","policy_version":1,"source":{"kind":"active_window","app":"aplicativo","record_ref":null,"origin":"native"},"classification":{"label":"company","reason":"explicação","context_version":1},"content":{"text":"evidência literal","sha256":"SHA-256 UTF-8"},"synthetic":true,"screenshot":{"attachment_id":"SHA-256","sha256":"SHA-256","media_type":"image/png"}}
```

`screenshot` é opcional; upload anterior recebe bytes, valida tipo/hash/limite e retorna ID/hash. O envio revalida política e dono do anexo. Arquivos de imagem ficam privados no mesmo repositório e têm link inspecionável; caminho local não é evidência online. O recibo contém `observation_id`, `job_id`, `state`, `inspect_url`; repetição idêntica mantém os IDs, payload diferente com a mesma chave dá conflito. Conteúdo pessoal/incerto não é aceito como observação da empresa.

Lacuna segura: `ingest_context_gap` recebe `schema_version=1`, `event_id`, `device_id`, `occurred_at`, `app`, `surface_kind="desktop_window"`, `synthetic`. Não recebe título, referência de documento, texto ou screenshot desconhecido. Retorna `source_id`, `question_id`, `state`, `inspect_url`. A pergunta tem `target_kind=context`, não corresponde a uma tarefa observada da empresa e pode receber resposta pelo mesmo caminho Nora. O operador no Mac do Lucas interpreta a fila; não é necessário instalar LLM para cada participante.

Campos obrigatórios da tarefa: `trigger`, `performer`, `input`, `action`, `result`, `purpose`, `placement`, `handoff`, `tracking_record`. As chaves de incerteza são `(task_id, field)`, sem prosa do modelo. Cada campo contém `value` (texto ou null), `basis` (`observed`, `reported`, `inferred`) e `evidence` com pares `{source_id, quote}`. O que fica gravado em `quote` é um trecho literal da fonte. A comparação aceita diferença de Unicode (NFC/NFKC), maiúsculas, espaços, aspas, travessões, reticências, pontuação miúda e um erro curto de digitação quando o trecho é único; uma citação inventada continua recusada. Numa entrevista, o fato sem trecho pode ser omitido e o restante segue. Campos inferidos/ausentes e contradições mantêm a tarefa provisória. Cada falta tem uma pergunta estável; a versão atual não apaga a anterior.

Interpretação retorna `tasks`, `nodes`, `context_updates`, `next_question`; detalhes constam do schema do interpretador. `tasks` traz `task_id` existente ou null, `name`, `fields` e `links`. `nodes` traz tipo/nome/evidência. Contexto cobre `activities`, `people`, `stages`, `tracking`, `documents`, inclusive declaração explícita de não aplicação. A conversa pergunta uma coisa por vez e guarda a resposta antes de interpretar.

Mudanças: `add`, `rename`, `split`, `merge`, `reassign`, `connect`, `disconnect`, `correct`. Todas preservam fontes/versões e registram motivo. Split/merge exigem alocação explícita de todas as fontes dos predecessores; fontes comuns podem apoiar vários sucessores. A atualização remota cria árvore/commit e move a referência com `force:false`; conflito reabre o estado. Mirror retido + cadeia de hashes + alerta detectam reescritas depois da primeira confiança; não são armazenamento inviolável.

Correções explícitas de nomes em nós compartilhados são preservadas: derivar uma relação não renomeia um nó existente. `disconnect` conserva a evidência da relação vigente e a fonte da remoção. Atualizações sem nova evidência da relação não a reativam, mesmo se mudarem outros campos da tarefa. Uma fonte da relação recebida antes da remoção também não a desfaz ao ser interpretada depois. Evidência realmente posterior da relação ou uma ordem explícita `connect` pode restabelecê-la; as versões anteriores permanecem no histórico.
