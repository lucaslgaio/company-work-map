# Assistente do fundador

A entrevista e as capturas podem ser lidas por você, o assistente que o fundador já usa. Não é preciso instalar ou autenticar um programa chamado `claude`.

## Quando houver uma resposta esperando

1. Rode `operator prompt --output CAMINHO_PRIVADO/pedido.json` com a configuração do operador.
2. Leia o pedido e as fontes que ele cita. Escreva um JSON no formato que o cérebro já aceita: tarefas, campos e trechos que existem literalmente na fonte. Não invente nome, valor ou repasse.
3. Rode `operator apply --file CAMINHO_PRIVADO/analise.json`.

## Se você não puder interpretar

Rode `operator commit-literal`. A frase do fundador entra no assunto da entrevista como ele escreveu. O mapa não fica parado à espera de outro programa.

`interview next` diz isso em português enquanto a resposta espera. Se a leitura automática da mesma entrevista for recusada várias vezes, `interview next`, `status` e `operator health` pedem este caminho (`operator prompt` e `operator apply`, ou `operator commit-literal`) em vez de repetir o mesmo pedido.

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
