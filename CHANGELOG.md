# Changelog

## 0.1.2

Fatos complementares da entrevista deixam de virar «Relatos conflitantes» só porque o texto é outro. Se `mode` não vem no `context_update`, o significado é `add`: cada fato fica ativo com a fonte dele e o resumo junta os dois. `mode conflict` continua conflito. `mode replace` e a correção do fundador continuam como estavam. Uma correção gravada quando o assunto ainda estava vazio entra ao lado do que foi preenchido depois; correção gravada contra um resumo que mudou continua em conflito.

Instalações que já têm esses conflitos falsos podem resolvê-los sem reescrever o histórico:

```text
python3 -m company_pilot --config CONFIG repair context-conflicts --dry-run
python3 -m company_pilot --config CONFIG repair context-conflicts
```

O dry-run só mostra. A aplicação grava um evento novo no ledger. Conflito que o modelo marcou como `conflict` não entra. Rodar de novo não muda nada.

Nomes de projeto, registro ou empresa que a entrevista já citou não passam sozinhos a reconhecer janelas. Enquanto nenhum nome estiver confirmado, `setup status`, `interview next` e `operator health` avisam, e as capturas sem esse nome continuam incertas. O fundador confirma só os nomes que aceitar:

```text
python3 -m company_pilot --config CONFIG setup confirm-names --name "NOME" --kind record_name
```

O tipo é `record_name`, `record_ref`, `company_alias` ou `business_account`. O nome tem de aparecer inteiro numa fonte já guardada.

Para atualizar uma instalação existente, na raiz do repositório: `git pull` e `python3 -m pip install -e .`. Quem usa o módulo sem instalar, só precisa do `git pull`. Depois rode o dry-run do reparo, o reparo, e `setup confirm-names` para cada nome que o fundador aceitar. Não apague a configuração nem o cérebro.

## 0.1.1

A conferência de citações da análise aceita diferenças pequenas de Unicode (NFC/NFKC, inclusive acento NFD do macOS), maiúsculas, espaços, aspas, travessões, reticências e pontuação miúda, e um erro curto de digitação quando há um único trecho correspondente. O que fica gravado é o texto original da fonte. Se uma citação ainda não existir, a nova tentativa informa ao modelo qual trecho falhou (`source_quote_not_found`); numa entrevista, só o fato sem trecho fica de fora. Depois de três recusas `analysis_rejected` numa entrevista, `interview next`, `status` e a saúde do operador recomendam `operator prompt` com `operator apply`, ou `operator commit-literal`, em vez de insistir no mesmo pedido.

Para atualizar uma instalação existente, na raiz do repositório: `git pull` e `python3 -m pip install -e .`. Quem usa o módulo sem instalar, só precisa do `git pull`.
