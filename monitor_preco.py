"""
Monitor de preço - Lava-louças Brastemp BLF62AP (WebContinental)

O que esse script faz:
1. Acessa a página do produto na WebContinental
2. Extrai o preço TOTAL parcelado no cartão (ex: "10x de R$ 539,01" -> R$ 5.390,10)
3. Se esse valor for igual ou menor que o PRECO_ALVO, envia um e-mail de alerta

Esse script sozinho não roda em loop nem fica "ligado" o tempo todo.
Ele é feito para ser chamado periodicamente pelo GitHub Actions
(veja o arquivo .github/workflows/verificar_preco.yml).
"""

import json
import os
import re
import smtplib
import subprocess
from datetime import datetime
from email.mime.text import MIMEText

import requests
from playwright.sync_api import sync_playwright

URL = (
    "https://www.webcontinental.com.br/"
    "lava-loucas-15-servicos-brastemp-eclipse-collection---blf62ap-110v-000387001443/p"
)

# Preço-alvo em reais. Pode ser sobrescrito pela variável de ambiente PRECO_ALVO
# (definida no arquivo do workflow do GitHub Actions).
PRECO_ALVO = float(os.environ.get("PRECO_ALVO", "5390"))

# Arquivo que guarda "até que preço já avisamos", pra não mandar o mesmo
# alerta de novo a cada execução enquanto o preço continuar baixo.
ESTADO_PATH = "estado.json"


def carregar_estado() -> dict:
    if not os.path.exists(ESTADO_PATH):
        return {"ultimo_preco_alertado": None}
    with open(ESTADO_PATH, "r", encoding="utf-8") as arquivo:
        return json.load(arquivo)


def salvar_estado_e_commitar(estado: dict) -> None:
    with open(ESTADO_PATH, "w", encoding="utf-8") as arquivo:
        json.dump(estado, arquivo, ensure_ascii=False, indent=2)

    # Salva esse arquivo de volta no repositório, pra próxima execução
    # "lembrar" que já avisamos sobre esse preço. Se isso falhar por
    # qualquer motivo, só registra no log - não deve derrubar o alerta
    # que já foi enviado.
    try:
        subprocess.run(["git", "config", "user.name", "monitor-preco-bot"], check=True)
        subprocess.run(["git", "config", "user.email", "actions@github.com"], check=True)
        subprocess.run(["git", "add", ESTADO_PATH], check=True)
        resultado = subprocess.run(["git", "commit", "-m", "Atualiza estado do preço monitorado"])
        if resultado.returncode == 0:
            subprocess.run(["git", "push"], check=True)
    except Exception as erro:
        print(f"Aviso: não consegui salvar o estado no repositório ({erro}).")


def buscar_preco_parcelado() -> float:
    """
    Abre a página do produto num navegador headless (Playwright) - porque o
    preço nessa loja é carregado via JavaScript, então uma requisição HTTP
    simples não é suficiente pra "ver" o valor - e retorna o preço TOTAL
    pago no cartão parcelado (não o preço à vista no Pix, que costuma ser
    mais baixo e não é a forma de pagamento usada).

    A maioria das lojas brasileiras anuncia o parcelamento no formato
    "10x de R$ 539,01", por exemplo. O script procura esse padrão e calcula
    o total (parcelas x valor da parcela).
    """
    with sync_playwright() as p:
        navegador = p.chromium.launch()
        pagina = navegador.new_page(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
            )
        )
        pagina.goto(URL, wait_until="networkidle", timeout=30000)
        # Espera um pouco a mais, caso o preço demore pra aparecer na tela
        pagina.wait_for_timeout(3000)
        texto = pagina.inner_text("body")
        navegador.close()
    # Procura padrões como "10x de R$ 539,01" ou "até 10x de R$ 539,01 sem juros"
    parcelamentos = re.findall(r"(\d{1,2})\s*x\s*de\s*R\$\s?([\d.]+,\d{2})", texto, re.IGNORECASE)

    # Ignora "1x" (isso normalmente é só o preço à vista no cartão, não parcelamento de verdade)
    parcelamentos = [(int(qtd), valor) for qtd, valor in parcelamentos if int(qtd) >= 2]

    if not parcelamentos:
        raise ValueError(
            "Não encontrei nenhuma opção de parcelamento na página. "
            "O site pode ter mudado o layout, ou o produto saiu do ar."
        )

    # Se aparecer mais de uma opção de parcelamento na página, pega a de mais
    # parcelas (geralmente é a condição "cheia" tipo 10x sem juros).
    qtd_parcelas, valor_parcela_str = max(parcelamentos, key=lambda p: p[0])
    valor_parcela = float(valor_parcela_str.replace(".", "").replace(",", "."))

    return qtd_parcelas * valor_parcela


def enviar_email(preco_atual: float) -> None:
    remetente = os.environ["SMTP_USER"]
    senha_app = os.environ["SMTP_PASS"]
    destinatario = os.environ["EMAIL_DESTINO"]

    corpo = (
        f"O preço TOTAL parcelado no cartão da lava-louças BLF62AP caiu para "
        f"R$ {preco_atual:.2f}!\n\n"
        f"Alvo configurado: R$ {PRECO_ALVO:.2f}\n"
        f"Link: {URL}\n\n"
        f"Verificado em {datetime.now().strftime('%d/%m/%Y %H:%M')}"
    )

    msg = MIMEText(corpo)
    msg["Subject"] = f"Preço parcelado caiu: BLF62AP por R$ {preco_atual:.2f}"
    msg["From"] = remetente
    msg["To"] = destinatario

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as servidor:
        servidor.login(remetente, senha_app)
        servidor.sendmail(remetente, destinatario, msg.as_string())


def notificar_celular(preco_atual: float) -> None:
    """
    Notificação push simples via ntfy.sh - opcional, sem precisar de conta.
    Só é usada se a variável de ambiente NTFY_TOPICO estiver definida.
    """
    topico = os.environ.get("NTFY_TOPICO")
    if not topico:
        return
    requests.post(
        f"https://ntfy.sh/{topico}",
        data=f"Preço parcelado da BLF62AP caiu para R$ {preco_atual:.2f}! {URL}".encode("utf-8"),
        timeout=10,
    )


def main() -> None:
    preco_atual = buscar_preco_parcelado()
    print(f"Preço parcelado encontrado: R$ {preco_atual:.2f} (alvo: R$ {PRECO_ALVO:.2f})")

    estado = carregar_estado()
    ultimo_preco_alertado = estado.get("ultimo_preco_alertado")

    if preco_atual <= PRECO_ALVO:
        # Só alerta se for a primeira vez, ou se o preço caiu ainda mais
        # desde o último alerta - assim não repete o mesmo aviso.
        if ultimo_preco_alertado is None or preco_atual < ultimo_preco_alertado:
            enviar_email(preco_atual)
            notificar_celular(preco_atual)
            print("Alerta enviado!")
            salvar_estado_e_commitar({"ultimo_preco_alertado": preco_atual})
        else:
            print("Preço continua baixo, mas você já foi avisado sobre esse valor. Nada a fazer.")
    else:
        print("Preço acima do alvo, nada a fazer por enquanto.")
        # Se o preço voltou a subir, "reseta" o estado - assim, se ele cair
        # de novo depois, você recebe um novo aviso mesmo que seja o mesmo valor.
        if ultimo_preco_alertado is not None:
            salvar_estado_e_commitar({"ultimo_preco_alertado": None})


if __name__ == "__main__":
    main()
