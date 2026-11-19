# Assistente do fundador

A partir da `0.1.4` este computador não analisa. Ele guarda a resposta da entrevista e a captura de trabalho. A leitura que vira atividade, cadeia, nota ou pergunta é do operador externo, no computador dele, com a conta configurada em `setup external-analyst`. Não chame um programa chamado `claude` e não rode `operator watch`, `operator apply` nem `operator commit-literal` aqui: esta conta não grava análise.

O laço do operador está em `OPERATOR.md`: `operator pull`, `operator export`, ler o JSON, escrever o resultado e `operator submit`.

`interview next` continua mostrando a próxima pergunta. A resposta fica guardada com `interview answer`. Se a mesma entrevista for recusada várias vezes, o aviso pede `operator export` e `operator submit` no computador do operador, não outra tentativa igual neste Mac.

## Fatos que se somam

Se a análise não trouxer `mode` num fato de contexto, grave como complemento (`add`). Dois relatos do mesmo assunto ficam os dois, cada um com a fonte, e o resumo junta os textos. Use `mode` `conflict` só quando as frases se contradizem. Use `replace` só quando o fundador estiver trocando uma afirmação por outra e você citar o id dela em `replaces`.

## Conflitos antigos

Se o cérebro já marcou complementos como «Relatos conflitantes», rode primeiro o dry-run e depois o reparo, com a configuração da empresa:

```text
python3 -m company_pilot --config CONFIG repair context-conflicts --dry-run
python3 -m company_pilot --config CONFIG repair context-conflicts
```

O reparo não mexe num conflito que a análise marcou como `conflict`. Pode rodar outra vez: se não houver o que resolver, ele não grava de novo.

## Nomes para reconhecer janelas

Um projeto, um registro ou o nome da empresa citado na entrevista ajuda a ligar a janela ao projeto. Não é mais a trava da captura: no computador de trabalho, a janela entra como trabalho, salvo o que for claramente pessoal. `setup status`, `interview next` e `operator health` avisam quando nenhum nome foi confirmado. Confirme só o que o fundador aceitar, com o nome exatamente como está na fonte:

```text
python3 -m company_pilot --config CONFIG setup confirm-names --name "NOME" --kind record_name
```

Para o nome da empresa use `--kind company_alias`. Para um código de registro use `record_ref`. Para uma conta empresarial use `business_account`. Repita `--name` e `--kind` em pares. Não invente nome e não confirme a lista inteira de uma vez sem a pessoa aceitar cada um.
