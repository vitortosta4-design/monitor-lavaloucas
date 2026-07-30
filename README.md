# Monitor de preço - Lava-louças Brastemp BLF62AP

Esse projeto verifica o preço da BLF62AP na WebContinental algumas vezes por
dia e te avisa por e-mail (e, se quiser, no celular também) quando o preço
cair pra um valor que você define.

Ele roda de graça na nuvem através do **GitHub Actions**, então seu
computador não precisa ficar ligado.

## Passo 1 - Criar o repositório no GitHub

1. Crie uma conta gratuita em https://github.com (se ainda não tiver).
2. Clique em "New repository". Pode ser público ou privado, tanto faz.
3. Envie esses arquivos pro repositório (pelo site mesmo, arrastando os
   arquivos em "Add file > Upload files" - não precisa saber usar linha de
   comando de git).

Estrutura que precisa ficar assim dentro do repositório:

```
.github/workflows/verificar_preco.yml
monitor_preco.py
requirements.txt
```

## Passo 2 - Criar uma senha de app do Gmail

O Gmail não deixa mais scripts fazerem login com sua senha normal, então
você precisa gerar uma "senha de app":

1. Ative a verificação em duas etapas na sua conta Google, se ainda não
   tiver: https://myaccount.google.com/security
2. Acesse https://myaccount.google.com/apppasswords
3. Crie uma senha de app (pode chamar de "monitor-preco"). O Google vai te
   dar uma senha de 16 letras - copie ela, você vai usar no próximo passo.

Se preferir não usar Gmail, dá pra adaptar o script pra outro provedor de
e-mail (Outlook, Yahoo etc.) - é só trocar o servidor SMTP no código.

## Passo 3 - Guardar os dados sensíveis como "Secrets" no GitHub

No seu repositório: **Settings > Secrets and variables > Actions > New
repository secret**. Crie estes três:

| Nome do secret | Valor |
|---|---|
| `SMTP_USER` | seu e-mail do Gmail (ex: `voce@gmail.com`) |
| `SMTP_PASS` | a senha de app de 16 letras que você gerou no passo 2 |
| `EMAIL_DESTINO` | o e-mail que vai receber o alerta (pode ser o mesmo) |

Isso evita que sua senha fique visível no código.

## Passo 4 - Ajustar o preço-alvo

O script acompanha o **preço TOTAL parcelado no cartão** (ex: se a loja
anuncia "10x de R$ 539,01", ele calcula R$ 5.390,10), já que é essa a forma
de pagamento que você vai usar - não o preço à vista no Pix, que costuma
aparecer mais baixo.

No arquivo `.github/workflows/verificar_preco.yml`, tem essa linha:

```yaml
PRECO_ALVO: "5390"
```

Troque `5390` pelo valor total parcelado que você quer usar como
referência. O script dispara o alerta quando encontra um total parcelado
igual ou menor que esse.

## Passo 5 - Testar

No GitHub, vá em **Actions > Verificar preço da lava-louças > Run
workflow**. Isso roda o script na hora, sem precisar esperar o horário
programado. Depois clique na execução pra ver o que ele encontrou.

Por padrão ele roda sozinho 3x por dia (6h, 12h e 18h, horário de
Brasília). Dá pra mudar a frequência editando a linha `cron:` no arquivo do
workflow.

## Opcional - Notificação no celular sem precisar de conta

Se além do e-mail você quiser também um aviso no celular, dá pra usar o
**ntfy.sh** - um serviço gratuito de notificação push que não exige
cadastro:

1. Instale o app "ntfy" (Android/iOS) ou acesse https://ntfy.sh pelo
   navegador do celular.
2. Escolha um nome de "tópico" único, tipo uma senha
   (ex: `vitor-lavaloucas-8k2j`). Qualquer pessoa que souber esse nome
   consegue ver as notificações, então evite algo óbvio.
3. Dentro do app, toque em "Subscribe to topic" e digite esse nome.
4. No GitHub, crie mais um secret chamado `NTFY_TOPICO` com esse mesmo
   nome.

Pronto - o script já está preparado pra mandar a notificação assim que
esse secret existir, sem precisar mudar mais nada no código.

## Sobre o funcionamento

O script lê o preço direto do texto da página do produto. Se a
WebContinental mudar o layout do site, a busca pode parar de encontrar o
preço - nesse caso o workflow vai falhar (você recebe um aviso do próprio
GitHub por e-mail) e o código do `monitor_preco.py` precisa ser ajustado.

Ele também não sobrecarrega o site: roda só 3 vezes por dia, como um
acesso normal de navegador.
