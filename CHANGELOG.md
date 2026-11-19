# Changelog

## 0.1.3

No computador de trabalho, a captura entra como trabalho da empresa. Um nome confirmado (`setup confirm-names`) continua útil para ligar a janela ao projeto, mas não decide mais se a captura sai do Mac. O que é claramente pessoal continua só no computador: as exclusões que a pessoa já gravou, e uma lista embutida de superfícies pessoais.

Ficam de fora, e não vão para o repositório:

- banco e corretora de uso pessoal (Nubank, Itaú, Bradesco, Banco do Brasil, Inter, C6, PicPay, XP, Rico, Clear, Binance, Mercado Bitcoin, Avenue, internet banking, home broker)
- saúde pessoal (Doctoralia, Dr. Consulta, Conexa, prontuário, consulta médica, exame de sangue, Apple Health)
- namoro (Tinder, Bumble, Happn, Badoo, OkCupid, Grindr)
- streaming e entretenimento (Netflix, Spotify, Disney+, Globoplay, Prime Video, HBO Max, Deezer, Twitch, Apple Music)
- redes sociais pessoais (Instagram, Facebook, TikTok, Snapchat, Threads, X/Twitter)
- gerenciadores de senha (1Password, Bitwarden, LastPass, Dashlane, KeePass, Acesso às Chaves)
- janela anônima ou privada (incógnito, InPrivate, navegação privada)
- tela bloqueada e tela de início de sessão

WhatsApp, LinkedIn e YouTube não entram nessa lista: no trabalho eles aparecem como ferramenta. A palavra «saúde» sozinha também não bloqueia um texto de segurança do trabalho. Campo de senha continua de fora do texto; se o valor aparecer com rótulo (`senha:`, `password:`, `token:`), o valor vai como `[redigido]`. No Mac, a imagem não é tirada quando há um campo de senha visível.

A quarentena antiga de capturas «incertas» é relida no próximo ciclo, antes de expirar. O que não for pessoal passa a ser compartilhado. Se a quarentena já estiver vazia, o ciclo não muda nada. Rodar de novo não duplica.

A imagem da janela de trabalho passa a ser o padrão. Ela é reduzida (lado maior até 1280 pixels, JPEG no Mac), no máximo uma imagem nova por minuto se o texto da janela não mudou, e fica em `attachments/` ligada à fonte. A análise usa o texto; o anexo é a mesma janela. Sem a permissão de Gravação de Tela, o texto continua. O comando abaixo diz se a imagem está de fato sendo gravada.

Para atualizar uma instalação que já está em uso, na pasta que o serviço usa (checkout detached; não use `git pull` e não instale pacote):

```text
git fetch origin
git checkout COMMIT_PUBLICADO
```

`COMMIT_PUBLICADO` é o commit desta versão no Git. Não rode `python3 -m pip install -e .` e não use `--break-system-packages`. Os comandos são `python3 -m company_pilot`, com a configuração que o fundador já tem no lugar de `CONFIG` e a configuração do coletor no lugar de `DEVICE_CONFIG`:

```text
python3 -m company_pilot --config CONFIG setup screenshots --founder yes --team yes
python3 -m company_pilot --config CONFIG setup consent --participate yes --screenshots yes --reviewed-defaults
python3 -m company_pilot.desktop enable-capture --config DEVICE_CONFIG
launchctl kickstart -k "gui/$(id -u)/com.company-pilot.desktop"
python3 -m company_pilot.desktop screenshots --config DEVICE_CONFIG
python3 -m company_pilot.desktop status --config DEVICE_CONFIG
```

O `consent` grava a escolha de imagem e deixa a captura desligada até o `enable-capture`. Se a Gravação de Tela ainda não estiver autorizada, `screenshots` explica o caminho: Ajustes do Sistema > Privacidade e Segurança > Gravação de Tela, adicionar o coletor e reiniciar o serviço. A captura de texto segue. `actually_taking` só fica verdadeiro quando a política permite, o macOS autorizou e já existe imagem nesta fila. Não apague a configuração nem o cérebro.

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
