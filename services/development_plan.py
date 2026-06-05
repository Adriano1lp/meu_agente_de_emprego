from __future__ import annotations

import json
import uuid
from collections import Counter
from datetime import UTC, datetime
from typing import Any

from fastapi import HTTPException
from langchain_core.output_parsers import JsonOutputParser
from langchain_core.prompts import PromptTemplate
from langchain_openai import ChatOpenAI

from config import OPENAI_CHAT_MODEL, ensure_openai_api_key
from database.repository import (
    create_development_plan,
    get_active_development_plan,
    get_development_plan,
    list_development_plans,
    list_job_analysis_insights,
    update_development_plan,
)

MINIMUM_INSIGHTS_TO_GENERATE = 2
DEFAULT_ANALYSIS_LIMIT = 10
MAX_ANALYSIS_LIMIT = 20
ALLOWED_ITEM_STATUSES = {"pending", "in_progress", "completed"}
SECTION_CATEGORIES = ("70", "20", "10")


def generate_development_plan(
    *,
    user_id: str,
    limit: int = DEFAULT_ANALYSIS_LIMIT,
    replace_active: bool = False,
) -> dict[str, Any]:
    normalized_limit = max(1, min(limit, MAX_ANALYSIS_LIMIT))
    active_plan = get_active_development_plan(user_id)
    if active_plan and not replace_active:
        raise HTTPException(
            status_code=409,
            detail="Ja existe um PDI ativo. Informe replace_active=true para substituir.",
        )

    insights = list_job_analysis_insights(
        user_id,
        limit=normalized_limit,
        offset=0,
    )
    if len(insights) < MINIMUM_INSIGHTS_TO_GENERATE:
        raise HTTPException(
            status_code=400,
            detail=(
                "Historico insuficiente para gerar PDI. "
                f"Analise pelo menos {MINIMUM_INSIGHTS_TO_GENERATE} vagas."
            ),
        )

    summary = _summarize_insights(insights)
    ai_plan = _synthesize_with_ai(summary)
    plan_payload = _build_plan_payload(
        user_id=user_id,
        limit=normalized_limit,
        insights=insights,
        summary=summary,
        ai_plan=ai_plan,
    )

    if active_plan and replace_active:
        closed_active = {
            **active_plan,
            "status": "replaced",
            "updated_at": _utc_now_iso(),
        }
        update_development_plan(closed_active)

    create_development_plan(plan_payload)
    return _format_plan_response(plan_payload)


def read_active_development_plan(user_id: str) -> dict[str, Any] | None:
    plan = get_active_development_plan(user_id)
    return _format_plan_response(plan) if plan else None


def read_development_plan_history(
    *,
    user_id: str,
    limit: int = 20,
    offset: int = 0,
) -> list[dict[str, Any]]:
    return [
        _format_plan_summary(plan)
        for plan in list_development_plans(user_id, limit=limit, offset=offset)
    ]


def update_development_plan_item_status(
    *,
    user_id: str,
    pdi_id: str,
    item_id: str,
    status: str,
) -> dict[str, Any]:
    normalized_status = status.strip().lower()
    if normalized_status not in ALLOWED_ITEM_STATUSES:
        raise HTTPException(
            status_code=400,
            detail="Status invalido. Use pending, in_progress ou completed.",
        )

    plan = get_development_plan(user_id=user_id, pdi_id=pdi_id)
    if not plan:
        raise HTTPException(status_code=404, detail="PDI nao encontrado")

    items = _as_list(plan.get("checklist_items"))
    updated = False
    now = _utc_now_iso()
    for item in items:
        if str(item.get("id")) == item_id:
            item["status"] = normalized_status
            item["updated_at"] = now
            updated = True
            break

    if not updated:
        raise HTTPException(status_code=404, detail="Item do PDI nao encontrado")

    progress_percent = _calculate_progress_percent(items)
    plan["checklist_items"] = items
    plan["plan_70"] = _filter_items_by_category(items, "70")
    plan["plan_20"] = _filter_items_by_category(items, "20")
    plan["plan_10"] = _filter_items_by_category(items, "10")
    plan["progress_percent"] = progress_percent
    plan["status"] = "completed" if progress_percent == 100 else "active"
    plan["completed_at"] = now if progress_percent == 100 else None
    plan["updated_at"] = now
    update_development_plan(plan)
    return _format_plan_response(plan)


def _summarize_insights(insights: list[dict[str, Any]]) -> dict[str, Any]:
    gap_counter: Counter[str] = Counter()
    strength_counter: Counter[str] = Counter()
    source_insight_ids: list[str] = []
    source_processing_run_ids: list[str] = []

    for insight in insights:
        source_insight_ids.append(str(insight.get("id")))
        processing_run_id = insight.get("processing_run_id")
        if processing_run_id is not None:
            source_processing_run_ids.append(str(processing_run_id))

        for gap in [
            *_as_list(insight.get("critical_gaps")),
            *_as_list(insight.get("missing_skills")),
        ]:
            normalized = str(gap).strip()
            if normalized:
                gap_counter[normalized] += 1

        for strength in [
            *_as_list(insight.get("strengths")),
            *_as_list(insight.get("matching_skills")),
        ]:
            normalized = str(strength).strip()
            if normalized:
                strength_counter[normalized] += 1

    priority_gaps = [item for item, _ in gap_counter.most_common(6)]
    strengths = [item for item, _ in strength_counter.most_common(6)]
    return {
        "source_insight_ids": source_insight_ids,
        "source_processing_run_ids": source_processing_run_ids,
        "priority_gaps": priority_gaps,
        "strengths_to_leverage": strengths,
        "analysis_count": len(insights),
    }


def _synthesize_with_ai(summary: dict[str, Any]) -> dict[str, Any] | None:
    try:
        parser = JsonOutputParser()
        prompt = PromptTemplate(
            template="""
                Voce e um mentor de carreira.

                Crie um PDI pratico em JSON valido com as chaves:
                title, main_objective, summary, secondary_objectives,
                priority_areas, checklist_items.

                Cada checklist item deve ter:
                id, title, description, category, gap, priority, status, weight.

                Regras:
                - category deve ser "70", "20" ou "10".
                - status inicial deve ser "pending".
                - distribua acoes entre pratica 70, social 20 e estudo 10.
                - use objetivo especifico, pratico e mensuravel.
                - nao invente gaps fora dos dados.

                Dados consolidados:
                {summary}
            """,
            input_variables=["summary"],
        )
        llm = ChatOpenAI(
            model_name=OPENAI_CHAT_MODEL,
            temperature=0.2,
            openai_api_key=ensure_openai_api_key(),
        )
        result = (prompt | llm | parser).invoke(
            {"summary": json.dumps(summary, ensure_ascii=False)},
        )
        return result if isinstance(result, dict) else None
    except Exception:
        return None


def _build_plan_payload(
    *,
    user_id: str,
    limit: int,
    insights: list[dict[str, Any]],
    summary: dict[str, Any],
    ai_plan: dict[str, Any] | None,
) -> dict[str, Any]:
    priority_gaps = _strings_or_default(
        (ai_plan or {}).get("priority_gaps"),
        summary["priority_gaps"],
    )
    if not priority_gaps:
        priority_gaps = ["Aprofundar competencias recorrentes nas vagas analisadas"]

    strengths = _strings_or_default(
        (ai_plan or {}).get("strengths_to_leverage"),
        summary["strengths_to_leverage"],
    )
    checklist_items = _normalize_checklist_items(
        (ai_plan or {}).get("checklist_items"),
        priority_gaps=priority_gaps,
    )
    now = _utc_now_iso()
    plan = {
        "pdi_id": f"pdi_{uuid.uuid4().hex}",
        "user_id": user_id,
        "source_insight_ids": summary["source_insight_ids"],
        "source_processing_run_ids": summary["source_processing_run_ids"],
        "generated_from_limit": limit,
        "title": _text_or_default(
            (ai_plan or {}).get("title"),
            "PDI para evoluir nos gaps das vagas analisadas",
        ),
        "main_objective": _text_or_default(
            (ai_plan or {}).get("main_objective"),
            _default_main_objective(priority_gaps),
        ),
        "summary": _text_or_default(
            (ai_plan or {}).get("summary"),
            f"Plano criado a partir das ultimas {len(insights)} vagas analisadas.",
        ),
        "secondary_objectives": _strings_or_default(
            (ai_plan or {}).get("secondary_objectives"),
            [f"Evoluir em {gap}" for gap in priority_gaps[:3]],
        ),
        "priority_areas": _strings_or_default(
            (ai_plan or {}).get("priority_areas"),
            priority_gaps[:3],
        ),
        "priority_gaps": priority_gaps,
        "strengths_to_leverage": strengths,
        "plan_70": _filter_items_by_category(checklist_items, "70"),
        "plan_20": _filter_items_by_category(checklist_items, "20"),
        "plan_10": _filter_items_by_category(checklist_items, "10"),
        "checklist_items": checklist_items,
        "progress_percent": 0,
        "status": "active",
        "created_at": now,
        "updated_at": now,
        "completed_at": None,
    }
    return plan


def _normalize_checklist_items(
    value: Any,
    *,
    priority_gaps: list[str],
) -> list[dict[str, Any]]:
    raw_items = value if isinstance(value, list) else []
    normalized: list[dict[str, Any]] = []
    for index, item in enumerate(raw_items, start=1):
        if not isinstance(item, dict):
            continue
        category = str(item.get("category") or item.get("categoria") or "").strip()
        if category not in SECTION_CATEGORIES:
            continue
        normalized.append(
            _checklist_item(
                item_id=str(item.get("id") or f"item_{index}"),
                title=_text_or_default(item.get("title"), f"Acao {index}"),
                description=_text_or_default(
                    item.get("description"),
                    "Executar acao pratica de desenvolvimento.",
                ),
                category=category,
                gap=_text_or_default(item.get("gap"), priority_gaps[0]),
                priority=_normalize_priority(item.get("priority")),
                weight=_safe_positive_int(item.get("weight"), default=1),
            ),
        )

    if normalized:
        return _ensure_all_sections(normalized, priority_gaps)

    return _default_checklist(priority_gaps)


def _default_checklist(priority_gaps: list[str]) -> list[dict[str, Any]]:
    main_gap = priority_gaps[0]
    secondary_gap = priority_gaps[1] if len(priority_gaps) > 1 else main_gap
    third_gap = priority_gaps[2] if len(priority_gaps) > 2 else secondary_gap
    return [
        _checklist_item(
            item_id="item_70_1",
            title=f"Aplicar {main_gap} em um projeto pratico",
            description="Criar uma entrega de portfolio usando um problema real e registrar evidencias do aprendizado.",
            category="70",
            gap=main_gap,
            priority="high",
            weight=3,
        ),
        _checklist_item(
            item_id="item_70_2",
            title=f"Resolver um caso completo envolvendo {secondary_gap}",
            description="Simular uma demanda de trabalho, documentar decisoes e publicar resultado revisavel.",
            category="70",
            gap=secondary_gap,
            priority="high",
            weight=3,
        ),
        _checklist_item(
            item_id="item_20_1",
            title=f"Pedir feedback sobre {main_gap}",
            description="Compartilhar a entrega com uma pessoa experiente e registrar os principais ajustes recomendados.",
            category="20",
            gap=main_gap,
            priority="medium",
            weight=2,
        ),
        _checklist_item(
            item_id="item_20_2",
            title="Conversar com profissionais das vagas alvo",
            description="Mapear expectativas reais do mercado e comparar com os gaps recorrentes do historico.",
            category="20",
            gap=secondary_gap,
            priority="medium",
            weight=1,
        ),
        _checklist_item(
            item_id="item_10_1",
            title=f"Estudar fundamentos de {third_gap}",
            description="Concluir conteudo estruturado e produzir um resumo aplicavel ao portfolio.",
            category="10",
            gap=third_gap,
            priority="medium",
            weight=1,
        ),
    ]


def _ensure_all_sections(
    items: list[dict[str, Any]],
    priority_gaps: list[str],
) -> list[dict[str, Any]]:
    existing = {str(item.get("category")) for item in items}
    defaults = _default_checklist(priority_gaps)
    for category in SECTION_CATEGORIES:
        if category not in existing:
            items.append(next(item for item in defaults if item["category"] == category))
    return items


def _checklist_item(
    *,
    item_id: str,
    title: str,
    description: str,
    category: str,
    gap: str,
    priority: str,
    weight: int,
) -> dict[str, Any]:
    return {
        "id": item_id,
        "title": title,
        "description": description,
        "category": category,
        "gap": gap,
        "priority": priority,
        "status": "pending",
        "weight": weight,
    }


def _format_plan_response(plan: dict[str, Any]) -> dict[str, Any]:
    items = _as_list(plan.get("checklist_items"))
    return {
        "pdi_id": plan["pdi_id"],
        "title": plan["title"],
        "main_objective": plan["main_objective"],
        "summary": plan["summary"],
        "secondary_objectives": _as_list(plan.get("secondary_objectives")),
        "priority_areas": _as_list(plan.get("priority_areas")),
        "priority_gaps": _as_list(plan.get("priority_gaps")),
        "strengths_to_leverage": _as_list(plan.get("strengths_to_leverage")),
        "progress_percent": int(plan.get("progress_percent") or 0),
        "status": plan.get("status"),
        "sections": {
            "70": _filter_items_by_category(items, "70"),
            "20": _filter_items_by_category(items, "20"),
            "10": _filter_items_by_category(items, "10"),
        },
        "checklist_items": items,
        "created_at": plan.get("created_at"),
        "updated_at": plan.get("updated_at"),
        "completed_at": plan.get("completed_at"),
    }


def _format_plan_summary(plan: dict[str, Any]) -> dict[str, Any]:
    return {
        "pdi_id": plan["pdi_id"],
        "title": plan["title"],
        "main_objective": plan["main_objective"],
        "progress_percent": int(plan.get("progress_percent") or 0),
        "status": plan.get("status"),
        "created_at": plan.get("created_at"),
        "updated_at": plan.get("updated_at"),
        "completed_at": plan.get("completed_at"),
    }


def _calculate_progress_percent(items: list[dict[str, Any]]) -> int:
    total_weight = sum(_safe_positive_int(item.get("weight"), default=1) for item in items)
    if total_weight <= 0:
        return 0
    completed_weight = sum(
        _safe_positive_int(item.get("weight"), default=1)
        for item in items
        if item.get("status") == "completed"
    )
    return round((completed_weight / total_weight) * 100)


def _filter_items_by_category(items: list[dict[str, Any]], category: str) -> list[dict[str, Any]]:
    return [item for item in items if str(item.get("category")) == category]


def _default_main_objective(priority_gaps: list[str]) -> str:
    gaps = ", ".join(priority_gaps[:3])
    return (
        "Desenvolver dominio pratico e evidencias de portfolio em "
        f"{gaps} para aumentar aderencia as proximas vagas analisadas."
    )


def _text_or_default(value: Any, default: str) -> str:
    text = str(value or "").strip()
    return text or default


def _strings_or_default(value: Any, default: list[str]) -> list[str]:
    values = [str(item).strip() for item in value] if isinstance(value, list) else []
    values = [item for item in values if item]
    return values or default


def _normalize_priority(value: Any) -> str:
    priority = str(value or "").strip().lower()
    return priority if priority in {"low", "medium", "high"} else "medium"


def _safe_positive_int(value: Any, *, default: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _utc_now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()
