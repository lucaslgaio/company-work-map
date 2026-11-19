# Changelog

## 0.1.1

A conferência de citações da análise aceita diferenças pequenas de Unicode (NFC/NFKC, inclusive acento NFD do macOS), maiúsculas, espaços, aspas, travessões, reticências e pontuação miúda, e um erro curto de digitação quando há um único trecho correspondente. O que fica gravado é o texto original da fonte. Se uma citação ainda não existir, a nova tentativa informa ao modelo qual trecho falhou (`source_quote_not_found`); numa entrevista, só o fato sem trecho fica de fora. Depois de três recusas `analysis_rejected` numa entrevista, `interview next`, `status` e a saúde do operador recomendam `operator prompt` com `operator apply`, ou `operator commit-literal`, em vez de insistir no mesmo pedido.

Para atualizar uma instalação existente, na raiz do repositório: `git pull` e `python3 -m pip install -e .`. Quem usa o módulo sem instalar, só precisa do `git pull`.
