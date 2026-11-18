# Ciclo local

Este comando lê só as capturas que o fundador informar. Não envia WhatsApp, não liga captura, não publica nada e não traz uma semana pronta. Rode na raiz do repositório, com `python3`, como em `START-HERE.md`.

Sem arquivo de capturas, o comando imprime que ainda não há captura para ler, diz como montar o JSON e não cria pasta de mapa.

Com o arquivo, cada item tem `when`, `app`, `record_ref` e `text`. O texto é o da janela, sem completar.

```text
python3 -m company_pilot.local_cycle --root "$PWD/.company-pilot-local/cycle" --captures "$PWD/.company-pilot-local/capturas.json" --company "NOME_DA_EMPRESA" --founder "NOME_DO_FUNDADOR"
```

`--company` e `--founder` são obrigatórios junto com `--captures`. O comando não escolhe esses nomes. Use os mesmos que o fundador disse no `init`.

O que acontece, nesta ordem:

1. Cada texto vira uma observação local.
2. Um leitor sem modelo pago cria tarefa só quando o texto diz quem faz o quê. Ele só cria um repasse quando a frase manda um resultado para o nome exato de uma atividade que já existe. Se houver dúvida, não liga.
3. Se uma tarefa ficar sem responsável, o comando imprime a mensagem que a Nora escreveria no WhatsApp, com o nome do fundador, o horário da captura e uma pergunta. Não envia.
4. No fim, imprime os caminhos para abrir.

A pasta fica com arquivos comuns, não só o snapshot interno:

- `views/mapa.md`
- `views/insights.md`
- `views/leitura-segunda.md`
- `notes/indice.md`

A semana real, quando houver modelo do fundador, é lida pelo prompt de análise, não só por este leitor.
