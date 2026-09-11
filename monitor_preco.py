"""
Monitor de preço - Lava-louças Brastemp BLF62AP

O que esse script faz:
1. Acessa a página do produto em cada loja monitorada (veja LOJAS abaixo)
2. Extrai o preço TOTAL parcelado no cartão de cada uma (ex: "10x de R$ 539,01" -> R$ 5.390,10)
3. Entre as lojas que tiverem o produto disponível, pega a de menor preço
4. Se esse valor for igual ou menor que o PRECO_ALVO, envia um e-mail de alerta

Esse script sozinho não roda em loop nem fica "ligado" o tempo todo.
Ele é feito para ser chamado periodicamente pelo GitHub Actions
(veja o arquivo .github/workflows/verificar_preco.yml).
"""

import json
import os
import re
import smtplib
import subprocess
import time
from datetime import datetime, timedelta, timezone
from email.mime.text import MIMEText

import requests
from playwright.sync_api import sync_playwright

# Lojas monitoradas. Só incluímos aqui lojas que permitem acesso automatizado
# (robots.txt não bloqueia) - Mercado Livre, Shopee e Amazon foram testadas e
# bloqueiam esse tipo de acesso, então não entraram na lista.
LOJAS = [
    {
        "nome": "WebContinental",
        "url": (
            "https://www.webcontinental.com.br/"
            "lava-loucas-15-servicos-brastemp-eclipse-collection---blf62ap-110v-000387001443/p"
        ),
    },
    {
        "nome": "Magazine Luiza",
        "url": (
            "https://www.magazineluiza.com.br/"
            "lava-loucas-15-servicos-brastemp-eclipse-collection-blf62ap/p/dk536fhbk6/ed/l15s/"
        ),
    },
    {
        "nome": "Brastemp (oficial)",
        "url": "https://www.brastemp.com.br/lava-loucas-15-servicos-brastemp-eclipse-collection---blf62ap/p",
    },
]

# Preço-alvo em reais. Pode ser sobrescrito pela variável de ambiente PRECO_ALVO
# (definida no arquivo do workflow do GitHub Actions).
PRECO_ALVO = float(os.environ.get("PRECO_ALVO", "5390"))

# Arquivo que guarda "até que preço já avisamos", pra não mandar o mesmo
# alerta de novo a cada execução enquanto o preço continuar baixo.
ESTADO_PATH = "estado.json"

# O GitHub Actions roda em UTC por padrão; isso converte pra horário de
# Brasília só na hora de mostrar no e-mail, pra não confundir.
HORARIO_BRASILIA = timezone(timedelta(hours=-3))


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


class ProdutoIndisponivelError(Exception):
    """Levantado quando o produto está claramente fora de estoque na loja."""


def _extrair_preco_parcelado(texto: str) -> float:
    """Recebe o texto da página já carregada e extrai o preço TOTAL parcelado."""
    # A loja mostra frases assim quando o produto está fora de estoque, em
    # vez de mostrar preço. Nesse caso não faz sentido insistir tentando de
    # novo - é um estado real da loja, não uma instabilidade passageira.
    texto_lower = texto.lower()
    marcadores_fora_de_estoque = [
        "não está disponível no momento",
        "sem este produto",
        "produto indisponível",
    ]
    if any(marcador in texto_lower for marcador in marcadores_fora_de_estoque):
        raise ProdutoIndisponivelError("Produto marcado como fora de estoque.")

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


def _tentar_buscar_preco_de_uma_loja(url: str) -> float:
    """Faz UMA tentativa de abrir a página de uma loja e extrair o preço parcelado."""
    with sync_playwright() as p:
        navegador = p.chromium.launch()
        pagina = navegador.new_page(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
            )
        )
        pagina.goto(url, wait_until="networkidle", timeout=30000)
        # Espera um pouco a mais, caso o preço demore pra aparecer na tela
        pagina.wait_for_timeout(3000)
        texto = pagina.inner_text("body")
        navegador.close()
    return _extrair_preco_parcelado(texto)


def buscar_preco_de_uma_loja(url: str, tentativas: int = 3, espera_segundos: int = 20) -> float:
    """
    Abre a página do produto numa loja específica, num navegador headless
    (Playwright) - porque o preço nessas lojas costuma ser carregado via
    JavaScript, então uma requisição HTTP simples não é suficiente pra
    "ver" o valor.

    Se a página estiver instável ou lenta no momento (site fora do ar por
    um instante, timeout de rede etc.), tenta de novo algumas vezes antes
    de desistir. Já se o produto estiver genuinamente fora de estoque, não
    insiste tentando de novo.
    """
    ultimo_erro: Exception | None = None

    for tentativa in range(1, tentativas + 1):
        try:
            return _tentar_buscar_preco_de_uma_loja(url)
        except ProdutoIndisponivelError:
            raise
        except Exception as erro:
            ultimo_erro = erro
            print(f"  Tentativa {tentativa}/{tentativas} falhou: {erro}")
            if tentativa < tentativas:
                time.sleep(espera_segundos)

    raise ultimo_erro


def buscar_menor_preco_entre_lojas() -> tuple[float, str, str]:
    """
    Verifica o preço parcelado em cada loja de LOJAS e retorna
    (preco, nome_da_loja, url_da_loja) da que tiver o menor preço entre as
    que tiverem o produto disponível agora.

    Se o produto estiver fora de estoque (ou inacessível) em TODAS as
    lojas monitoradas, levanta ProdutoIndisponivelError.
    """
    encontrados = []
    fora_de_estoque = []

    for loja in LOJAS:
        print(f"Verificando {loja['nome']}...")
        try:
            preco = buscar_preco_de_uma_loja(loja["url"])
            print(f"  {loja['nome']}: R$ {preco:.2f}")
            encontrados.append((preco, loja["nome"], loja["url"]))
        except ProdutoIndisponivelError:
            print(f"  {loja['nome']}: fora de estoque no momento.")
            fora_de_estoque.append(loja["nome"])
        except Exception as erro:
            print(f"  {loja['nome']}: não consegui verificar ({erro}).")

    if not encontrados:
        raise ProdutoIndisponivelError(
            "Produto fora de estoque (ou não verificável) em todas as lojas monitoradas: "
            + ", ".join(loja["nome"] for loja in LOJAS)
        )

    return min(encontrados, key=lambda item: item[0])


def enviar_email(preco_atual: float, loja_nome: str, loja_url: str) -> None:
    remetente = os.environ["SMTP_USER"]
    senha_app = os.environ["SMTP_PASS"]
    destinatario = os.environ["EMAIL_DESTINO"]

    corpo = (
        f"O preço TOTAL parcelado no cartão da lava-louças BLF62AP caiu para "
        f"R$ {preco_atual:.2f}, na loja {loja_nome}!\n\n"
        f"Alvo configurado: R$ {PRECO_ALVO:.2f}\n"
        f"Link: {loja_url}\n\n"
        f"Verificado em {datetime.now(HORARIO_BRASILIA).strftime('%d/%m/%Y %H:%M')} (horário de Brasília)"
    )
    msg = MIMEText(corpo)
    msg["Subject"] = f"Preço parcelado caiu: BLF62AP por R$ {preco_atual:.2f} ({loja_nome})"
    msg["From"] = remetente
    msg["To"] = destinatario

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as servidor:
        servidor.login(remetente, senha_app)
        servidor.sendmail(remetente, destinatario, msg.as_string())


def notificar_celular(preco_atual: float, loja_nome: str, loja_url: str) -> None:
    """
    Notificação push simples via ntfy.sh - opcional, sem precisar de conta.
    Só é usada se a variável de ambiente NTFY_TOPICO estiver definida.
    """
    topico = os.environ.get("NTFY_TOPICO")
    if not topico:
        return
    requests.post(
        f"https://ntfy.sh/{topico}",
        data=(
            f"Preço parcelado da BLF62AP caiu para R$ {preco_atual:.2f} na {loja_nome}! {loja_url}"
        ).encode("utf-8"),
        timeout=10,
    )


def main() -> None:
    try:
        preco_atual, loja_nome, loja_url = buscar_menor_preco_entre_lojas()
    except ProdutoIndisponivelError as erro:
        # Fora de estoque não é um bug no script - só não tem preço pra
        # comparar agora. Registra no log e encerra sem marcar como falha.
        print(f"Produto fora de estoque no momento: {erro}")
        return

    print(
        f"Melhor preço parcelado encontrado: R$ {preco_atual:.2f} na {loja_nome} "
        f"(alvo: R$ {PRECO_ALVO:.2f})"
    )

    estado = carregar_estado()
    ultimo_preco_alertado = estado.get("ultimo_preco_alertado")

    if preco_atual <= PRECO_ALVO:
        # Só alerta se for a primeira vez, ou se o preço caiu ainda mais
        # desde o último alerta - assim não repete o mesmo aviso.
        if ultimo_preco_alertado is None or preco_atual < ultimo_preco_alertado:
            enviar_email(preco_atual, loja_nome, loja_url)
            notificar_celular(preco_atual, loja_nome, loja_url)
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
