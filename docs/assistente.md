# Assistente do fundador

A entrevista e as capturas podem ser lidas por você, o assistente que o fundador já usa. Não é preciso instalar ou autenticar um programa chamado `claude`.

## Quando houver uma resposta esperando

1. Rode `operator prompt --output CAMINHO_PRIVADO/pedido.json` com a configuração do operador.
2. Leia o pedido e as fontes que ele cita. Escreva um JSON no formato que o cérebro já aceita: tarefas, campos e trechos que existem literalmente na fonte. Não invente nome, valor ou repasse.
3. Rode `operator apply --file CAMINHO_PRIVADO/analise.json`.

## Se você não puder interpretar

Rode `operator commit-literal`. A frase do fundador entra no assunto da entrevista como ele escreveu. O mapa não fica parado à espera de outro programa.

`interview next` diz isso em português enquanto a resposta espera. Se a leitura automática da mesma entrevista for recusada várias vezes, `interview next`, `status` e `operator health` pedem este caminho (`operator prompt` e `operator apply`, ou `operator commit-literal`) em vez de repetir o mesmo pedido.
