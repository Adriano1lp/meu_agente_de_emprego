from __future__ import annotations

import html
from pathlib import Path

from reportlab.lib.enums import TA_JUSTIFY, TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import cm
from reportlab.platypus import Paragraph, SimpleDocTemplate
from reportlab.lib.styles import ParagraphStyle


def gerar_pdf_carta_apresentacao(texto: str, nome_arquivo: str = "carta.pdf") -> None:
    """
    Gera um PDF A4 para carta de apresentacao seguindo convencoes ABNT:
    margens 3 cm superior/esquerda, 2 cm inferior/direita, fonte tamanho 12
    e paragrafo justificado.
    """
    carta = texto.strip()
    if not carta:
        raise ValueError("Texto da carta nao pode ser vazio")

    caminho_pdf = Path(nome_arquivo)
    caminho_pdf.parent.mkdir(parents=True, exist_ok=True)

    doc = SimpleDocTemplate(
        str(caminho_pdf),
        pagesize=A4,
        leftMargin=3 * cm,
        rightMargin=2 * cm,
        topMargin=3 * cm,
        bottomMargin=2 * cm,
        title="Carta de apresentacao",
        author="Analista de Vagas",
    )

    estilo_texto = ParagraphStyle(
        name="CartaABNTTexto",
        fontName="Times-Roman",
        fontSize=12,
        leading=18,
        alignment=TA_JUSTIFY,
        firstLineIndent=1.25 * cm,
        spaceAfter=12,
    )
    estilo_local_data = ParagraphStyle(
        name="CartaABNTLocalData",
        parent=estilo_texto,
        alignment=TA_RIGHT,
        firstLineIndent=0,
        spaceAfter=24,
    )
    estilo_assinatura = ParagraphStyle(
        name="CartaABNTAssinatura",
        parent=estilo_texto,
        firstLineIndent=0,
        spaceBefore=18,
    )

    elementos = []
    paragrafos = _normalizar_paragrafos(carta)

    for indice, paragrafo in enumerate(paragrafos):
        texto_seguro = html.escape(paragrafo).replace("\n", "<br/>")
        estilo = estilo_texto

        if indice == 0 and _parece_local_data(paragrafo):
            estilo = estilo_local_data
        elif _parece_assinatura(paragrafo):
            estilo = estilo_assinatura

        elementos.append(Paragraph(texto_seguro, estilo))

    doc.build(elementos)


def _normalizar_paragrafos(texto: str) -> list[str]:
    paragrafos: list[str] = []
    bloco_atual: list[str] = []

    for linha in texto.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        linha_limpa = linha.strip()
        if not linha_limpa:
            if bloco_atual:
                paragrafos.append(" ".join(bloco_atual))
                bloco_atual = []
            continue

        if _linha_curta_independente(linha_limpa):
            if bloco_atual:
                paragrafos.append(" ".join(bloco_atual))
                bloco_atual = []
            paragrafos.append(linha_limpa)
            continue

        bloco_atual.append(linha_limpa)

    if bloco_atual:
        paragrafos.append(" ".join(bloco_atual))

    return paragrafos


def _linha_curta_independente(linha: str) -> bool:
    return _parece_local_data(linha) or _parece_saudacao(linha) or _parece_assinatura(linha)


def _parece_local_data(linha: str) -> bool:
    texto = linha.lower()
    return "," in linha and any(marcador in texto for marcador in (" de 20", "data atual"))


def _parece_saudacao(linha: str) -> bool:
    texto = linha.lower()
    return texto.startswith(("prezado", "prezada", "prezados", "prezadas"))


def _parece_assinatura(linha: str) -> bool:
    texto = linha.lower().strip(" .")
    return texto.startswith(("atenciosamente", "cordialmente", "respeitosamente"))
