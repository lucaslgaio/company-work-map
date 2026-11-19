# Operador externo

Este computador lê os trabalhos e grava a análise. Não há programa `claude` e não há chave de API. Você é o analista: lê o JSON, escreve outro JSON e envia. A conta autenticada no `gh` tem de ser a conta configurada com `setup external-analyst`. O Mac do fundador não faz este laço.

`CONFIG` é a configuração privada deste operador, apontando para o repositório de registros e o mirror. Rode na raiz do código, com `python3 -m company_pilot`. Não instale pacote.

## 1. Trazer a fila

```text
python3 -m company_pilot --config CONFIG operator pull
python3 -m company_pilot --config CONFIG operator export --output JOBS
```

`pull` sincroniza o mirror. `export` grava um arquivo por trabalho pendente e um `index.json`. Cada arquivo traz:

- `job.id` e `job.kind`
- `sources`: o texto; numa entrevista, `question_topic` é o assunto da pergunta
- quando houver imagem, `attachment_path` é um arquivo real relativo a `JOBS` (`JOBS/attachments/<sha>.jpg`). O mirror do GitHub não tem esse arquivo solto: a imagem estava embutida em base64 e o export gravou os bytes aqui. Abra esse caminho. Não procure o mesmo nome dentro do mirror
- `map_scope` e `context`: o recorte que a gravação vai conferir
- `contract.schema` e `contract.instructions`
- se a tentativa anterior foi recusada, `job.rejection_reason`, `job.reason` e `job.quote_failures`

## 2. Escrever o resultado

Um objeto JSON só, no formato de `contract.schema`. Cite um trecho que já esteja no texto da fonte, contínuo, em `quote`, com o `source_id` dessa fonte. Não invente fato, pessoa, finalidade ou repasse.

Para captura (`observation`):

- há trabalho: de 1 a 3 tarefas; a tarefa nova cita a captura atual
- não há trabalho (aba nova, Finder, Ajustes do Sistema, título vazio ou só o sistema): `tasks` vazio e `"outcome": "no_work"`; deixe `nodes` e `context_updates` vazios
- `next_question` é `null`

Para resposta de entrevista (`interview`): `tasks` vazio e `nodes` vazio. Um `context_updates` com `topic` igual a `question_topic` da fonte, `mode` `add`, `basis` `reported`, e `value` e `quote` iguais ao texto inteiro da resposta. `next_question` é `null`, ou `{topic, text}` se ainda faltar um assunto (`activities`, `people`, `stages`, `tracking` ou `documents`).

```json
{"tasks": [], "nodes": [], "context_updates": [{"topic": "TOPIC_DA_FONTE", "value": "TEXTO_INTEIRO_DA_FONTE", "basis": "reported", "mode": "add", "evidence": [{"source_id": "ID_DA_FONTE", "quote": "TEXTO_INTEIRO_DA_FONTE"}]}], "next_question": null}
```

Campo desconhecido fica `value` null, `basis` `inferred`, `evidence` vazio.

## 3. Enviar

```text
python3 -m company_pilot --config CONFIG operator submit --job JOB_ID --file result.json
```

`JOB_ID` é o `job.id` do arquivo. Saída `ok: true` gravou no repositório. Saída `ok: false` não gravou a análise. Leia `mensagem` e `rejection_reason`. Se vier `quote_failures`, a citação não estava na fonte: copie um trecho contínuo ou tire o fato. Corrija o mesmo arquivo e envie de novo o mesmo `--job`.

## 4. Próximo

Repita o export quando a fila mudar. Não rode `operator watch` e não chame modelo por fora. Não apague trabalho, mirror nem ledger para destravar uma recusa.
