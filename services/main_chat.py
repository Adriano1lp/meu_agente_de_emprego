from __future__ import annotations

from fastapi import HTTPException
from langchain_chroma import Chroma
from langchain_core.output_parsers import JsonOutputParser, StrOutputParser
from langchain_core.prompts import PromptTemplate
from langchain_openai import ChatOpenAI, OpenAIEmbeddings
from pydantic import BaseModel, Field

from config import (
    OPENAI_CHAT_MODEL,
    OPENAI_EMBEDDING_MODEL,
    PERSISTENCE_BACKEND,
    get_user_chroma_dir,
    get_user_cv_file,
    ensure_openai_api_key,
)
from database.repository import find_similar_embedding_chunks, get_latest_user_cv_text

OPENAI_API_KEY = ensure_openai_api_key()
MINIMUM_MATCH_SCORE_TO_GENERATE_CURRICULUM = 60


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
        - Preencha os [...] com informacoes reais do candidato.

        FORMATO: (MANTENHA OS ATERISCOS PARA DEIXAR O TESTO EM NEGRITO)
        **[NOME COMPLETO]**
        **[CARGO]**

        [EMAIL] | [TELEFONE] | [LINKEDIN]

        ---

        **Resumo Profissional**
        Texto direto com experiencia, foco tecnico e diferencial competitivo.

        ---

        **Experiencia Profissional**

        **Cargo**
        **[EXPERIENCIA 1]** | [INICIO] - [FIM]
        - Entrega de valor com impacto mensuravel
        - Tecnologias utilizadas
        - Resultado alcancado

        **Cargo**
        **[EXPERIENCIA 2]** | [INICIO] - [FIM]
        - Entrega de valor com impacto mensuravel
        - Tecnologias utilizadas
        - Resultado alcancado

        **Cargo**
        **[EXPERIENCIA 3]** | [INICIO] - [FIM]
        - Entrega de valor com impacto mensuravel
        - Tecnologias utilizadas
        - Resultado alcancado

        **Cargo**
        **[EXPERIENCIA 4]** | [INICIO] - [FIM]
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
        **[FORMACAO ACADEMICA 1]**
        [INSTITUICAO] | [FINALIZACAO]

        **[FORMACAO ACADEMICA 2]**
        [INSTITUICAO] | [FINALIZACAO]

        ---

        **Idiomas**
        Idioma: [IDIOMA E NIVEL]

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
        Voce e um analista de aderencia profissional para o candidato.

        Entradas:
        - Dados da vaga: {vaga}
        - Analise de matching: {matching}

        Objetivo:
        Gerar uma resposta clara e objetiva sobre a compatibilidade do perfil com a vaga.

        Formato da resposta:
        - Percentual de match com a vaga.
        - Pontos fortes do candidato em relacao a vaga.
        - Pontos fracos ou lacunas do candidato em relacao a vaga.
        - Recomendacoes praticas para melhorar a aderencia.

        Regras:
        - Linguagem simples e direta.
        - Sem JSON.
        - Sem termos tecnicos desnecessarios.
        - Seja honesto, mas construtivo.
        - Nao aja como recrutador ou representante da empresa.
        - Nao agradeca candidatura.
        - Nao convide para entrevista.
        - Nao aprove nem reprove o candidato.
        - Nao use frases como "Obrigado por se candidatar".

        Resposta:
    """,
    input_variables=["vaga", "matching"],
)

prompt_carta_apresentacao = PromptTemplate(
    template="""
        Voce e um especialista em carreira e cartas de apresentacao.

        Entradas:
        - Dados do candidato {contexto}
        - Empresa alvo {empresa}

        Objetivo:
        Criar uma carta de apresentacao profissional, objetiva e aderente ao perfil do candidato.

        Regras:
        - Escreva em primeira pessoa.
        - Use apenas informacoes presentes no contexto do candidato.
        - Nao invente cargos, empresas, metricas, formacoes ou tecnologias.
        - Mencione a empresa alvo de forma natural.
        - Use tom profissional, direto e confiante.
        - Nao use markdown.
        - Nao inclua explicacoes fora da carta.

        Estrutura:
        [Cidade], [data atual]

        Prezados recrutadores da {empresa},

        [Abertura com interesse pela empresa]

        [Resumo profissional baseado no contexto]

        [Pontos fortes e contribuicoes possiveis]

        [Fechamento com disponibilidade]

        Atenciosamente,
        [Nome do candidato quando disponivel]
    """,
    input_variables=["contexto", "empresa"],
)

embeddings = OpenAIEmbeddings(
    model=OPENAI_EMBEDDING_MODEL,
    api_key=OPENAI_API_KEY,
)

llm_openai = ChatOpenAI(
    model_name=OPENAI_CHAT_MODEL,
    temperature=0.5,
    openai_api_key=OPENAI_API_KEY,
)

cadeia_1 = prompt_analizar_vaga | llm_openai | parseador_vaga
cadeia_2 = prompt_matching | llm_openai | parseador_matching
cadeia_3 = prompt_otimizacao | llm_openai | parseador_otimizacao
cadeia_4 = prompt_curriculo_otimizado | llm_openai | StrOutputParser()
cadeia_resposta = prompt_resposta_usuario | llm_openai | StrOutputParser()
cadeia_carta_apresentacao = prompt_carta_apresentacao | llm_openai | StrOutputParser()


def pipeline(vaga_texto: str, user_id: str) -> tuple[str, str]:
    result = pipeline_with_details(vaga_texto, user_id)
    return str(result.get("curriculo") or ""), str(result["resposta_usuario"])


def pipeline_with_details(vaga_texto: str, user_id: str) -> dict[str, object]:
    contexto = _load_candidate_context(vaga_texto, user_id)

    vaga_struct = cadeia_1.invoke({"vaga": vaga_texto})
    matching = cadeia_2.invoke({"contexto": contexto, "vaga": vaga_struct})
    match_score = _extract_match_score(matching)

    if match_score < MINIMUM_MATCH_SCORE_TO_GENERATE_CURRICULUM:
        return {
            "curriculo": None,
            "resposta_usuario": _build_job_analysis_response(match_score, matching),
            "vaga": vaga_struct,
            "matching": matching,
            "otimizacao": {
                "blocked_reason": "low_match_score",
                "minimum_match_score": MINIMUM_MATCH_SCORE_TO_GENERATE_CURRICULUM,
            },
            "match_score": match_score,
            "should_generate_curriculum": False,
        }

    otimizacao = cadeia_3.invoke({"vaga": vaga_struct, "matching": matching})
    resposta_usuario = _build_job_analysis_response(match_score, matching)
    curriculo = cadeia_4.invoke(
        {
            "contexto": contexto,
            "vaga": vaga_struct,
            "otimizacao": otimizacao,
        },
    )

    return {
        "curriculo": curriculo,
        "resposta_usuario": resposta_usuario,
        "vaga": vaga_struct,
        "matching": matching,
        "otimizacao": otimizacao,
        "match_score": match_score,
        "should_generate_curriculum": True,
    }


def generate_cover_letter(company_name: str, user_id: str) -> str:
    empresa = company_name.strip()
    if not empresa:
        raise HTTPException(status_code=400, detail="Nome da empresa nao pode ser vazio")

    contexto = _load_candidate_context(empresa, user_id)
    return cadeia_carta_apresentacao.invoke(
        {
            "contexto": contexto,
            "empresa": empresa,
        },
    )


def _load_candidate_context(vaga_texto: str, user_id: str) -> str:
    context_parts: list[str] = []
    if _use_mongodb_embeddings():
        context_parts.extend(_load_mongodb_candidate_context(vaga_texto, user_id))
    else:
        retriever = _build_user_retriever(user_id)

        if retriever is None:
            raise HTTPException(
                status_code=400,
                detail=(
                    "Embeddings do usuario nao encontrados. "
                    "Envie o curriculo e execute POST /users/me/rebuild-embeddings antes de processar a vaga."
                ),
            )

        try:
            contexto_docs = retriever.invoke(vaga_texto)
        except Exception:
            contexto_docs = []

        for doc in contexto_docs:
            content = getattr(doc, "page_content", "").strip()
            if content:
                context_parts.append(content)

    cv_text = _read_cv_file(user_id)
    if cv_text:
        context_parts.append(cv_text)

    contexto = "\n\n".join(dict.fromkeys(context_parts)).strip()
    if not contexto:
        raise HTTPException(
            status_code=400,
            detail=(
                "Nao foi possivel carregar o contexto do candidato para este usuario. "
                "Verifique o upload do curriculo e regenere os embeddings."
            ),
        )

    return contexto


def _extract_match_score(matching: object) -> int:
    value: object = None
    if isinstance(matching, dict):
        value = matching.get("match_score")
    else:
        value = getattr(matching, "match_score", None)

    try:
        score = int(value)
    except (TypeError, ValueError):
        score = 0

    return max(0, min(100, score))


def _build_job_analysis_response(match_score: int, matching: object) -> str:
    gaps = _extract_list_field(matching, "gaps_criticos")
    missing_skills = _extract_list_field(matching, "missing_skills")
    strengths = _extract_list_field(matching, "pontos_fortes")
    combined_gaps = _dedupe_items([*gaps, *missing_skills])
    lines = [
        f"Percentual de match com a vaga: {match_score}%.",
        _build_match_summary(match_score),
    ]

    if strengths:
        lines.append("Pontos fortes: " + "; ".join(strengths[:5]) + ".")
    else:
        lines.append("Pontos fortes: nenhum ponto forte especifico foi identificado nos dados analisados.")

    if combined_gaps:
        lines.append("Pontos fracos ou lacunas: " + "; ".join(combined_gaps[:6]) + ".")
    else:
        lines.append("Pontos fracos ou lacunas: nenhuma lacuna critica foi identificada nos dados analisados.")

    lines.append(
        "Recomendacoes praticas: ajuste o curriculo com experiencias reais relacionadas aos requisitos, "
        "reforce palavras-chave aderentes e priorize o desenvolvimento das lacunas listadas."
    )
    return "\n\n".join(lines)


def _build_low_match_response(match_score: int, matching: object) -> str:
    return _build_job_analysis_response(match_score, matching)


def _build_match_summary(match_score: int) -> str:
    if match_score < MINIMUM_MATCH_SCORE_TO_GENERATE_CURRICULUM:
        return (
            "Resumo: a aderencia esta abaixo do minimo usado pelo sistema para gerar "
            "curriculo otimizado e PDF."
        )
    return "Resumo: a aderencia atende ao minimo usado pelo sistema para gerar curriculo otimizado e PDF."


def _dedupe_items(items: list[str]) -> list[str]:
    unique_items: list[str] = []
    seen: set[str] = set()
    for item in items:
        normalized = item.strip().lower()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        unique_items.append(item.strip())
    return unique_items


def _extract_list_field(source: object, field_name: str) -> list[str]:
    value: object
    if isinstance(source, dict):
        value = source.get(field_name)
    else:
        value = getattr(source, field_name, None)

    if not isinstance(value, list):
        return []

    return [str(item).strip() for item in value if str(item).strip()]


def _load_mongodb_candidate_context(vaga_texto: str, user_id: str) -> list[str]:
    query_embedding = embeddings.embed_query(vaga_texto)
    chunks = find_similar_embedding_chunks(
        user_id=user_id,
        query_embedding=query_embedding,
        limit=6,
    )
    context_parts = [
        str(chunk.get("page_content", "")).strip()
        for chunk in chunks
        if str(chunk.get("page_content", "")).strip()
    ]
    if not context_parts:
        raise HTTPException(
            status_code=400,
            detail=(
                "Embeddings do usuario nao encontrados. "
                "Envie o curriculo e execute POST /users/me/rebuild-embeddings antes de processar a vaga."
            ),
        )
    return context_parts


def _build_user_retriever(user_id: str):
    chroma_dir = get_user_chroma_dir(user_id)
    if not chroma_dir.exists() or not any(chroma_dir.iterdir()):
        return None

    vectorstore = Chroma(
        persist_directory=str(chroma_dir),
        embedding_function=embeddings,
    )
    return vectorstore.as_retriever(
        search_type="similarity",
        search_kwargs={"k": 6},
    )


def _read_cv_file(user_id: str) -> str:
    cv_path = get_user_cv_file(user_id)
    if cv_path.exists():
        return cv_path.read_text(encoding="utf-8").strip()

    from services.object_storage import get_bytes, user_object_key

    remote_cv = get_bytes(user_object_key(user_id, "documents", cv_path.name))
    if remote_cv:
        return remote_cv.decode("utf-8").strip()

    return get_latest_user_cv_text(user_id) or ""


def _use_mongodb_embeddings() -> bool:
    return PERSISTENCE_BACKEND == "mongodb"
