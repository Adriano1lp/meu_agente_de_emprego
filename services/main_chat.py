from __future__ import annotations

from pathlib import Path

from langchain_chroma import Chroma
from langchain_core.output_parsers import JsonOutputParser, StrOutputParser
from langchain_core.prompts import PromptTemplate
from langchain_openai import ChatOpenAI, OpenAIEmbeddings
from pydantic import BaseModel, Field

from config import (
    CHROMA_DB_DIR,
    CV_FILE,
    OPENAI_CHAT_MODEL,
    OPENAI_EMBEDDING_MODEL,
    ensure_openai_api_key,
)

OPENAI_API_KEY = ensure_openai_api_key()


class Vaga(BaseModel):
    empresa: str = Field(description="Nome da empresa que oferece a vaga")
    nivel: str = Field(description="Nivel de experiencia requerido")
    hard_skills: list[str] = Field(
        description="Lista de hard skills requeridas",
        default_factory=list,
    )
    soft_skills: list[str] = Field(
        description="Lista de soft skills requeridas",
        default_factory=list,
    )
    ferramentas: list[str] = Field(
        description="Lista de ferramentas requeridas",
        default_factory=list,
    )
    responsabilidades: list[str] = Field(
        description="Lista de responsabilidades do cargo",
        default_factory=list,
    )
    requisitos_obrigatorios: list[str] = Field(
        description="Lista de requisitos obrigatorios",
        default_factory=list,
    )
    requisitos_desejaveis: list[str] = Field(
        description="Lista de requisitos desejaveis",
        default_factory=list,
    )
    palavras_chave_ats: list[str] = Field(
        description="Lista de palavras-chave importantes para ATS",
        default_factory=list,
    )


class Matching(BaseModel):
    match_score: int = Field(description="Pontuacao de 0 a 100")
    matching_skills: list[str] = Field(
        description="Lista de habilidades que correspondem a vaga",
        default_factory=list,
    )
    missing_skills: list[str] = Field(
        description="Lista de habilidades que faltam para a vaga",
        default_factory=list,
    )
    skills_similares: list[str] = Field(
        description="Lista de habilidades similares que podem ser destacadas",
        default_factory=list,
    )
    experiencias_relevantes: list[str] = Field(
        description="Lista de experiencias profissionais relevantes para a vaga",
        default_factory=list,
    )
    gaps_criticos: list[str] = Field(
        description="Lista de lacunas criticas",
        default_factory=list,
    )
    pontos_fortes: list[str] = Field(
        description="Lista de pontos fortes",
        default_factory=list,
    )


class Otimizacao(BaseModel):
    priorizar_experiencias: list[str] = Field(
        description="Lista de experiencias a serem priorizadas",
        default_factory=list,
    )
    destacar_habilidades: list[str] = Field(
        description="Lista de habilidades a serem destacadas",
        default_factory=list,
    )
    adaptacoes_necessarias: list[str] = Field(
        description="Lista de adaptacoes necessarias",
        default_factory=list,
    )
    palavras_chave_prioritarias: list[str] = Field(
        description="Lista de palavras-chave prioritarias",
        default_factory=list,
    )
    tom_do_curriculo: str = Field(
        description="Tom do curriculo: tecnico|estrategico|lideranca",
    )


parseador_vaga = JsonOutputParser(pydantic_object=Vaga)
parseador_matching = JsonOutputParser(pydantic_object=Matching)
parseador_otimizacao = JsonOutputParser(pydantic_object=Otimizacao)

prompt_analizar_vaga = PromptTemplate(
    template="""
        Voce e um especialista em recrutamento e analise de vagas.

        Entrada:
        - {vaga}

        Objetivo:
        Extrair e estruturar as informacoes mais relevantes da vaga.

        {formato_de_saida}

        Regras:
        - Normalize termos.
        - Priorize tecnologias e competencias repetidas.
        - Identifique palavras-chave usadas em ATS.
        - Nao invente nada fora da vaga.
        - Responda sempre em JSON valido.
        - Se algum campo nao estiver explicito, use "Nao informado" para texto e [] para listas.
    """,
    input_variables=["vaga"],
    partial_variables={
        "formato_de_saida": parseador_vaga.get_format_instructions(),
    },
)

prompt_matching = PromptTemplate(
    template="""
        Voce e um especialista em analise de perfil profissional.

        Entradas:
        1. Dados do candidato {contexto}
        2. Dados estruturados da vaga {vaga}

        Objetivo:
        Mapear o quanto o candidato atende a vaga.

        {formato_de_saida}

        Regras:
        - Compare semanticamente.
        - Nao penalize diferencas de nomenclatura.
        - Destaque lacunas criticas.
        - Use somente o contexto fornecido do candidato.
        - Nunca peca mais dados e nunca diga que faltou contexto.
        - Se algo nao estiver claro, assuma lista vazia ou descricoes conservadoras.
        - Responda sempre em JSON valido.
    """,
    input_variables=["contexto", "vaga"],
    partial_variables={
        "formato_de_saida": parseador_matching.get_format_instructions(),
    },
)

prompt_otimizacao = PromptTemplate(
    template="""
        Voce e um especialista em otimizacao de curriculos para ATS.

        Entradas:
        - Dados da vaga {vaga}
        - Match do candidato {matching}

        Objetivo:
        Definir estrategia de adaptacao do curriculo.

        {formato_de_saida}

        Regras:
        - Priorize o que aumenta o match ATS.
        - Foque no que o recrutador busca.
        - Nunca peca mais dados adicionais.
        - Se houver pouca informacao, responda com a melhor estrategia possivel.
        - Responda sempre em JSON valido.
    """,
    input_variables=["vaga", "matching"],
    partial_variables={
        "formato_de_saida": parseador_otimizacao.get_format_instructions(),
    },
)

prompt_curriculo_otimizado = PromptTemplate(
    template="""
        Voce e um especialista em criacao de curriculos otimizados para ATS e LinkedIn.

        Entradas:
        - Dados do candidato {contexto}
        - Dados da vaga {vaga}
        - Estrategia de otimizacao {otimizacao}

        Objetivo:
        Gerar um curriculo altamente aderente a vaga.

        REGRAS:
        - Use palavras-chave da vaga naturalmente.
        - Reescreva experiencias com foco em impacto sem inventar dados.
        - Destaque tecnologias e resultados.
        - Inclua metricas quando possivel.
        - Priorize relevancia sobre quantidade.
        - Use linguagem objetiva e profissional.
        - Evite redundancia.
        - Nao invente experiencias.

        FORMATO: (MANTENHA OS ATERISCOS PARA DEIXAR O TESTO EM NEGRITO)
        **Adriano Lima Pereira**
        **Cargo**

        adrianolpereira@gmail.com | (11) 966394923 | linkedin.com/in/adriano-lima-76764085/

        ---

        **Resumo Profissional**
        Texto direto com experiencia, foco tecnico e diferencial competitivo.

        ---

        **Experiencia Profissional**

        **Cargo**
        **[EXPERIENCIA_VIA_VAREJO]** | 2019 - 2021
        - Entrega de valor com impacto mensuravel
        - Tecnologias utilizadas
        - Resultado alcancado

        **Cargo**
        **[EXPERIENCIA_SUPERDIGITAL]** | 2021 - 2023
        - Entrega de valor com impacto mensuravel
        - Tecnologias utilizadas
        - Resultado alcancado

        **Cargo**
        **[EXPERIENCIA_DS_PLUS]** | 2015 - 2018
        - Entrega de valor com impacto mensuravel
        - Tecnologias utilizadas
        - Resultado alcancado

        ---

        **Habilidades Tecnicas**
        - **Categoria:** tecnologias
        - **Categoria:** tecnologias
        - **Categoria:** tecnologias

        ---

        **Formacao Academica**
        **MBA AI Engineering e Multi-Agents**
        FIAP | previsao 2026

        **Analise e Desenvolvimento de Sistemas**
        UNIABC | 2012

        ---

        **Idiomas**
        Idioma: Ingles - A1

        ---

        **Diferenciais**
        - Pontos fortes relevantes para a vaga
        - Conhecimentos em destaque

        ---

        **Objetivos**
        - Objetivo alinhado com a vaga

        IMPORTANTE:
        - Sem markdown
        - Sem explicacoes
        - Sem invencao de dados
    """,
    input_variables=["contexto", "vaga", "otimizacao"],
)

prompt_resposta_usuario = PromptTemplate(
    template="""
        Voce e um especialista em carreira e recrutamento.

        Entradas:
        - Dados da vaga: {vaga}
        - Analise de matching: {matching}

        Objetivo:
        Gerar uma resposta clara e objetiva para o candidato explicando o quao aderente ele esta a vaga.

        Formato da resposta:
        - Um resumo do nivel de aderencia.
        - Principais pontos fortes.
        - Principais lacunas.
        - Recomendacoes praticas para melhorar o fit.

        Regras:
        - Linguagem simples e direta.
        - Sem JSON.
        - Sem termos tecnicos desnecessarios.
        - Seja honesto, mas construtivo.

        Resposta:
    """,
    input_variables=["vaga", "matching"],
)

embeddings = OpenAIEmbeddings(
    model=OPENAI_EMBEDDING_MODEL,
    api_key=OPENAI_API_KEY,
)

vectorstore = Chroma(
    persist_directory=str(CHROMA_DB_DIR),
    embedding_function=embeddings,
)

llm_openai = ChatOpenAI(
    model_name=OPENAI_CHAT_MODEL,
    temperature=0.5,
    openai_api_key=OPENAI_API_KEY,
)

retriever = vectorstore.as_retriever(
    search_type="similarity",
    search_kwargs={"k": 6},
)

cadeia_1 = prompt_analizar_vaga | llm_openai | parseador_vaga
cadeia_2 = prompt_matching | llm_openai | parseador_matching
cadeia_3 = prompt_otimizacao | llm_openai | parseador_otimizacao
cadeia_4 = prompt_curriculo_otimizado | llm_openai | StrOutputParser()
cadeia_resposta = prompt_resposta_usuario | llm_openai | StrOutputParser()


def pipeline(vaga_texto: str) -> tuple[str, str]:
    contexto = _load_candidate_context(vaga_texto)

    vaga_struct = cadeia_1.invoke({"vaga": vaga_texto})
    matching = cadeia_2.invoke({"contexto": contexto, "vaga": vaga_struct})
    otimizacao = cadeia_3.invoke({"vaga": vaga_struct, "matching": matching})
    resposta_usuario = cadeia_resposta.invoke(
        {"vaga": vaga_struct, "matching": matching},
    )
    curriculo = cadeia_4.invoke(
        {
            "contexto": contexto,
            "vaga": vaga_struct,
            "otimizacao": otimizacao,
        },
    )

    return curriculo, resposta_usuario


def _load_candidate_context(vaga_texto: str) -> str:
    context_parts: list[str] = []

    try:
        contexto_docs = retriever.invoke(vaga_texto)
    except Exception:
        contexto_docs = []

    for doc in contexto_docs:
        content = getattr(doc, "page_content", "").strip()
        if content:
            context_parts.append(content)

    cv_text = _read_cv_file()
    if cv_text:
        context_parts.append(cv_text)

    contexto = "\n\n".join(dict.fromkeys(context_parts)).strip()
    if not contexto:
        raise RuntimeError(
            "Nao foi possivel carregar o contexto do candidato. "
            "Verifique api/documents/cv.txt e rode python rebuild_vectorstore.py."
        )

    return contexto


def _read_cv_file() -> str:
    cv_path = Path(CV_FILE)
    if not cv_path.exists():
        return ""

    return cv_path.read_text(encoding="utf-8").strip()
