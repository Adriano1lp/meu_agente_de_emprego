from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.pagesizes import A4
from reportlab.lib.enums import TA_CENTER
from reportlab.lib import colors
import re
import html

def formatar_negrito(texto: str) -> str:
    """
    Converte **texto** em <b>texto</b> corretamente
    """
    return re.sub(r"\*\*(.*?)\*\*", r"<b>\1</b>", texto)

def gerar_pdf_profissional(texto: str, nome_arquivo: str = "output.pdf"):
    """
    Gera um PDF com formatação profissional a partir de um texto estruturado.
    """

    doc = SimpleDocTemplate(nome_arquivo, pagesize=A4)

    estilos = getSampleStyleSheet()

    # ==========================
    # ESTILOS CUSTOMIZADOS
    # ==========================

    estilo_titulo = ParagraphStyle(
        name="Titulo",
        parent=estilos["Heading1"],
        alignment=TA_CENTER,
        fontSize=16,
        spaceAfter=12
    )

    estilo_subtitulo = ParagraphStyle(
        name="Subtitulo",
        parent=estilos["Heading2"],
        fontSize=13,
        textColor=colors.darkblue,
        spaceAfter=8
    )

    estilo_texto = ParagraphStyle(
        name="Texto",
        parent=estilos["Normal"],
        fontSize=10,
        spaceAfter=6
    )

    estilo_bullet = ParagraphStyle(
        name="Bullet",
        parent=estilos["Normal"],
        leftIndent=10,
        bulletIndent=5,
        spaceAfter=4
    )

    conteudo = []

    linhas = texto.split("\n")

    for linha in linhas:
        linha = linha.strip()

        if not linha:
            conteudo.append(Spacer(1, 8))
            continue

        # ==========================
        # REGRAS DE FORMATAÇÃO
        # ==========================

        # Título principal
        if linha.startswith("# "):
            conteudo.append(Paragraph(linha[2:], estilo_titulo))

        # Subtítulo
        elif linha.startswith("## "):
            conteudo.append(Paragraph(linha[3:], estilo_subtitulo))

        # Bullet point
        elif linha.startswith("- "):
            conteudo.append(Paragraph(linha[2:], estilo_bullet, bulletText="•"))

        # Texto em negrito (markdown simples)
        else:
            linha_segura = html.escape(linha)
            linha_formatada = formatar_negrito(linha_segura)
            conteudo.append(Paragraph(linha_formatada, estilo_texto))

    doc.build(conteudo)

def extrair_texto(resposta: str) -> str:
    marcador = "### 2. CURRÍCULO PERSONALIZADO"

    if marcador not in resposta:
        raise ValueError("Seção de currículo não encontrada na resposta.")

    # pega tudo depois do marcador
    curriculo = resposta.split(marcador, 1)[1].strip()

    return curriculo

# ==========================
# EXEMPLO DE USO
# ==========================

# if __name__ == "__main__":
#     texto_exemplo = """
# # Adriano Lima Pereira
# Senior QA Automation Engineer

# ## Resumo
# Especialista em automação de testes full-stack com foco em qualidade, performance e integração CI/CD.

# ## Experiência
# - Implementação de 800 testes automatizados
# - Redução de 70% no tempo de feedback
# - Deploys diários com segurança

# ## Tecnologias
# - Cypress, Robot Framework, Appium
# - Java, Python, JavaScript
# - AWS, Azure, GCP

# ## Resultado
# Automação escalável que aumentou a qualidade e reduziu falhas em produção.
# """

#     gerar_pdf_profissional(texto_exemplo, "curriculo_profissional.pdf")