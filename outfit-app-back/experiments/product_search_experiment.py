"""Dry-run y smoke controlado de búsqueda de prendas persistidas.

Desde ``outfit-app-back/``:

    python -m experiments.product_search_experiment \
        --database outfit.db \
        --selection 5:0,1,2,3,4 \
        --batch \
        --dry-run

El modo real requiere ``--execute`` y los límites de una aprobación activa. Esos
flags son una defensa adicional, no sustituyen la aprobación del usuario.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlsplit, urlunsplit

from openai import OpenAI
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.config import get_settings
from app.schemas import OutfitExtraction, OutfitItem

OUTPUT_ROOT = Path(__file__).resolve().parent / "output"

MODEL = "gpt-5.4-nano"
AUTOMATIC_RETRIES = 0
MAX_ITEMS = 8
MAX_RESULTS = 5
MAX_OUTPUT_TOKENS = 1_200
MAX_TOOL_CALLS_PER_ITEM = 2
BATCH_MAX_RESULTS = 3
BATCH_MAX_OUTPUT_TOKENS = 2_200
BATCH_MAX_TOOL_CALLS = 1
MAX_EXTRA_LENGTH = 200

# Tarifas estándar verificadas el 2026-07-27.
WEB_SEARCH_CALL_COST = 0.01
INPUT_COST_PER_MILLION = 0.20
OUTPUT_COST_PER_MILLION = 1.25
SEARCH_CONTENT_TOKEN_BUDGET = 12_000
PROMPT_TOKEN_BUDGET = 1_500
DISPLAY_CEILING_PER_ITEM = 0.03
APPROVAL_BUDGET_PER_ITEM = 0.035
BATCH_SEARCH_CONTEXT_LIMIT = 128_000
BATCH_DISPLAY_CEILING = 0.04
BATCH_APPROVAL_BUDGET = 0.04

STORES = {
    "zara.com": "Zara",
    "mango.com": "Mango",
    "hm.com": "H&M",
    "uniqlo.com": "Uniqlo",
    "massimodutti.com": "Massimo Dutti",
    "pullandbear.com": "Pull&Bear",
    "bershka.com": "Bershka",
    "stradivarius.com": "Stradivarius",
    "zalando.es": "Zalando",
    "elcorteingles.es": "El Corte Inglés",
}
GENERIC_TYPES = {
    "accesorio",
    "accesorios",
    "complemento",
    "complementos",
    "prenda",
    "prendas",
    "ropa",
}


class ExperimentError(ValueError):
    pass


class Candidate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str
    product_url: str
    price_text: str | None


class SearchOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    candidates: list[Candidate] = Field(max_length=MAX_RESULTS)


class BatchItemOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    item_id: str
    candidates: list[Candidate] = Field(max_length=BATCH_MAX_RESULTS)


class BatchSearchOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[BatchItemOutput] = Field(max_length=MAX_ITEMS)


@dataclass(frozen=True)
class SearchCase:
    outfit_id: int
    item_index: int
    item: OutfitItem
    extra_details: str | None
    query: str

    @property
    def stem(self) -> str:
        return f"outfit{self.outfit_id}_item{self.item_index}"


def _clean(value: str | None) -> str | None:
    normalized = " ".join(value.split()) if value else ""
    return normalized or None


def build_query(item: OutfitItem, extra_details: str | None = None) -> str:
    """Usa solo atributos persistidos y detalles escritos por el usuario."""
    item_type = _clean(item.item_type)
    if not item_type or item_type.casefold() in GENERIC_TYPES:
        raise ExperimentError(
            f"«{item.item_type}» no identifica una prenda concreta; pide más información."
        )

    extra = _clean(extra_details)
    if extra and len(extra) > MAX_EXTRA_LENGTH:
        raise ExperimentError(f"Los detalles adicionales superan {MAX_EXTRA_LENGTH} caracteres.")

    terms = [item_type]
    for value in (item.color, item.material, item.fit, *item.details, extra):
        value = _clean(value)
        if value and not any(value.casefold() in term.casefold() for term in terms):
            terms.append(value)
    if len(terms) == 1:
        raise ExperimentError(
            f"«{item_type}» es demasiado genérico; añade color, material, corte o detalle."
        )
    return " ".join([*terms, "comprar online España"])


def _parse_selection(value: str) -> tuple[int, tuple[int, ...]]:
    outfit, separator, raw_indexes = value.partition(":")
    try:
        indexes = tuple(int(index) for index in raw_indexes.split(","))
        outfit_id = int(outfit)
    except ValueError as exc:
        raise ExperimentError(f"Selección inválida «{value}». Usa OUTFIT:INDICE,INDICE.") from exc
    if not separator or outfit_id < 1 or not indexes or any(index < 0 for index in indexes):
        raise ExperimentError(f"Selección inválida «{value}». Usa OUTFIT:INDICE,INDICE.")
    return outfit_id, indexes


def _parse_extra(value: str) -> tuple[tuple[int, int], str]:
    target, separator, detail = value.partition("=")
    outfit_id, indexes = _parse_selection(target)
    detail = _clean(detail)
    if not separator or len(indexes) != 1 or not detail:
        raise ExperimentError(f"Detalle inválido «{value}». Usa OUTFIT:INDICE=TEXTO.")
    return (outfit_id, indexes[0]), detail


def _load_extraction(connection: sqlite3.Connection, outfit_id: int) -> OutfitExtraction:
    row = connection.execute(
        "SELECT outfit_json FROM outfits WHERE id = ?",
        (outfit_id,),
    ).fetchone()
    if row is None:
        raise ExperimentError(f"No existe el outfit {outfit_id}.")
    try:
        return OutfitExtraction.model_validate_json(row[0])
    except ValidationError as exc:
        raise ExperimentError(f"El outfit {outfit_id} no contiene una extracción válida.") from exc


def load_cases(
    database: Path,
    selections: Iterable[str],
    extras: Iterable[str] = (),
) -> list[SearchCase]:
    specs = [_parse_selection(value) for value in selections]
    if not specs:
        raise ExperimentError("Selecciona al menos una prenda.")

    extra_values = dict(_parse_extra(value) for value in extras)
    selected = [(outfit_id, index) for outfit_id, indexes in specs for index in indexes]
    if len(selected) != len(set(selected)):
        raise ExperimentError("Una misma prenda aparece seleccionada más de una vez.")
    if set(extra_values) - set(selected):
        raise ExperimentError("Hay detalles adicionales para prendas no seleccionadas.")
    if len(selected) > MAX_ITEMS:
        raise ExperimentError(f"El experimento admite como máximo {MAX_ITEMS} prendas.")

    path = database.expanduser().resolve()
    if not path.is_file():
        raise ExperimentError(f"No existe la base de datos: {path}.")

    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    cases: list[SearchCase] = []
    try:
        for outfit_id, indexes in specs:
            extraction = _load_extraction(connection, outfit_id)
            for index in indexes:
                if index >= len(extraction.items):
                    raise ExperimentError(
                        f"El outfit {outfit_id} no tiene una prenda con índice {index}."
                    )
                item = extraction.items[index]
                extra = extra_values.get((outfit_id, index))
                cases.append(SearchCase(outfit_id, index, item, extra, build_query(item, extra)))
    finally:
        connection.close()
    return cases


def build_prompt(case: SearchCase) -> str:
    return "\n".join(
        [
            "Busca páginas de producto concretas para esta prenda.",
            f"Consulta exacta: {case.query}",
            "Mercado: España. Usa exclusivamente los dominios permitidos.",
            "Mantén el tipo de prenda como requisito y usa todos los términos "
            "descriptivos de la consulta.",
            "Ordena los candidatos por coincidencia con color, material, corte y detalles. "
            "Si no existe una coincidencia exacta, conserva el tipo de prenda y prioriza "
            "las alternativas que mantengan más atributos explícitos.",
            "Usa una sola acción web cuando sea suficiente y nunca más de dos.",
            f"Devuelve como máximo {MAX_RESULTS} candidatos.",
            "Excluye categorías, editoriales, inspiración, segunda mano y agregadores.",
            "No inventes URL, precio, disponibilidad, material ni detalles.",
            "Devuelve solo lo encontrado y no calcules porcentajes de similitud.",
        ]
    )


def build_batch_prompt(cases: Iterable[SearchCase]) -> str:
    requested_items = [{"item_id": case.stem, "query": case.query} for case in cases]
    return "\n".join(
        [
            "Busca páginas de producto concretas para todas las prendas de esta lista.",
            "Haz las consultas necesarias dentro de una única acción de búsqueda web.",
            "Mercado: España. Usa exclusivamente los dominios permitidos.",
            f"Devuelve exactamente una entrada por item_id y como máximo {BATCH_MAX_RESULTS} "
            "candidatos por entrada; usa candidates=[] cuando no encuentres nada.",
            "No mezcles candidatos entre prendas ni cambies los item_id.",
            "Excluye categorías, editoriales, inspiración, segunda mano y agregadores.",
            "No inventes URL, precio, disponibilidad, material ni detalles.",
            "Devuelve solo lo encontrado y no calcules porcentajes de similitud.",
            "Prendas:",
            json.dumps(requested_items, ensure_ascii=False),
        ]
    )


def _web_search_tool() -> dict[str, Any]:
    return {
        "type": "web_search",
        "search_context_size": "low",
        "external_web_access": True,
        "filters": {"allowed_domains": list(STORES)},
        "user_location": {"type": "approximate", "country": "ES"},
    }


def request_payload(case: SearchCase) -> dict[str, Any]:
    return {
        "model": MODEL,
        "reasoning": {"effort": "low"},
        "input": build_prompt(case),
        "tools": [_web_search_tool()],
        "tool_choice": "required",
        "max_tool_calls": MAX_TOOL_CALLS_PER_ITEM,
        "max_output_tokens": MAX_OUTPUT_TOKENS,
        "parallel_tool_calls": False,
        "include": ["web_search_call.action.sources"],
        "text": {
            "format": {
                "type": "json_schema",
                "name": "product_search_results",
                "strict": True,
                "schema": SearchOutput.model_json_schema(),
            }
        },
        "store": False,
    }


def batch_request_payload(cases: Iterable[SearchCase]) -> dict[str, Any]:
    cases = list(cases)
    if not cases:
        raise ExperimentError("El lote requiere al menos una prenda.")
    return {
        "model": MODEL,
        "reasoning": {"effort": "low"},
        "input": build_batch_prompt(cases),
        "tools": [_web_search_tool()],
        "tool_choice": "required",
        "max_tool_calls": BATCH_MAX_TOOL_CALLS,
        "max_output_tokens": BATCH_MAX_OUTPUT_TOKENS,
        "parallel_tool_calls": False,
        "include": ["web_search_call.action.sources"],
        "text": {
            "format": {
                "type": "json_schema",
                "name": "batch_product_search_results",
                "strict": True,
                "schema": BatchSearchOutput.model_json_schema(),
            }
        },
        "store": False,
    }


def cost_budget(item_count: int, *, batch: bool = False) -> dict[str, float | int | str]:
    if item_count < 1:
        raise ExperimentError("El presupuesto requiere al menos una prenda.")
    if batch:
        absolute_ceiling = (
            BATCH_MAX_TOOL_CALLS * WEB_SEARCH_CALL_COST
            + BATCH_SEARCH_CONTEXT_LIMIT * INPUT_COST_PER_MILLION / 1_000_000
            + BATCH_MAX_OUTPUT_TOKENS * OUTPUT_COST_PER_MILLION / 1_000_000
        )
        return {
            "strategy": "outfit_batch",
            "item_count": item_count,
            "planned_web_search_calls": BATCH_MAX_TOOL_CALLS,
            "calculated_absolute_ceiling": round(absolute_ceiling, 6),
            "display_ceiling_total": BATCH_DISPLAY_CEILING,
            "recommended_approval_budget": BATCH_APPROVAL_BUDGET,
        }
    per_item = (
        MAX_TOOL_CALLS_PER_ITEM * WEB_SEARCH_CALL_COST
        + (SEARCH_CONTENT_TOKEN_BUDGET + PROMPT_TOKEN_BUDGET) * INPUT_COST_PER_MILLION / 1_000_000
        + MAX_OUTPUT_TOKENS * OUTPUT_COST_PER_MILLION / 1_000_000
    )
    return {
        "strategy": "per_item",
        "item_count": item_count,
        "calculated_ceiling_per_item": round(per_item, 6),
        "display_ceiling_per_item": DISPLAY_CEILING_PER_ITEM,
        "calculated_total": round(per_item * item_count, 6),
        "display_ceiling_total": round(DISPLAY_CEILING_PER_ITEM * item_count, 2),
        "recommended_approval_budget": round(APPROVAL_BUDGET_PER_ITEM * item_count, 2),
    }


def _write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def _new_output_dir(dry_run: bool, *, batch: bool = False) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    mode = "dry_run" if dry_run else "real"
    strategy = "product_search_batch" if batch else "product_search"
    path = OUTPUT_ROOT / f"{stamp}_{strategy}_{mode}"
    path.mkdir(parents=True, exist_ok=False)
    return path


def _response_data(response: object) -> dict[str, Any]:
    if isinstance(response, dict):
        return response
    data = response.model_dump(mode="json")
    if not isinstance(data, dict):
        raise ExperimentError("OpenAI devolvió un tipo de respuesta inesperado.")
    return data


def _response_text(response: object, data: dict[str, Any]) -> str:
    output_text = getattr(response, "output_text", None)
    if isinstance(output_text, str) and output_text:
        return output_text
    for item in data.get("output", []):
        if item.get("type") == "message":
            for content in item.get("content", []):
                if content.get("type") == "output_text":
                    return content["text"]
    raise ExperimentError("La búsqueda no devolvió el JSON de resultados.")


def _web_url(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    parsed = urlsplit(value.strip())
    return value.strip() if parsed.scheme in {"http", "https"} and parsed.hostname else None


def _url_key(url: str) -> str:
    parsed = urlsplit(url)
    host = (parsed.hostname or "").casefold()
    path = parsed.path.rstrip("/") or "/"
    return urlunsplit((parsed.scheme.casefold(), host, path, "", ""))


def _store(url: str) -> str | None:
    host = (urlsplit(url).hostname or "").casefold()
    return next(
        (name for domain, name in STORES.items() if host == domain or host.endswith(f".{domain}")),
        None,
    )


def _search_sources(
    data: dict[str, Any],
) -> tuple[int, dict[str, str]]:
    calls = 0
    sources: dict[str, str] = {}

    def add_source(value: object) -> None:
        url = _web_url(value)
        if url:
            sources.setdefault(_url_key(url), url)

    for item in data.get("output") or []:
        if item.get("type") == "web_search_call":
            calls += 1
            for source in (item.get("action") or {}).get("sources") or []:
                add_source(source.get("url"))
        elif item.get("type") == "message":
            for content in item.get("content") or []:
                for annotation in content.get("annotations") or []:
                    add_source(annotation.get("url"))
    return calls, sources


def _validated_candidates(
    candidates: Iterable[Candidate],
    sources: dict[str, str],
) -> tuple[list[dict[str, str | None]], int]:
    matches: list[dict[str, str | None]] = []
    rejected = 0
    seen: set[str] = set()
    for candidate in candidates:
        candidate_url = _web_url(candidate.product_url)
        key = _url_key(candidate_url) if candidate_url else ""
        source_url = sources.get(key)
        store = _store(source_url) if source_url else None
        if not source_url or not store or key in seen:
            rejected += 1
            continue
        seen.add(key)
        matches.append(
            {
                "title": candidate.title.strip(),
                "store": store,
                "product_url": source_url,
                "price_text": _clean(candidate.price_text),
            }
        )
    return matches, rejected


def validate_results(response: object) -> tuple[list[dict[str, str | None]], dict[str, int]]:
    """Acepta únicamente URLs devueltas como fuentes y pertenecientes a la allowlist."""
    data = _response_data(response)
    calls, sources = _search_sources(data)
    if not 1 <= calls <= MAX_TOOL_CALLS_PER_ITEM:
        raise ExperimentError(
            f"Se esperaban entre 1 y {MAX_TOOL_CALLS_PER_ITEM} acciones web por prenda; "
            f"se observaron {calls}."
        )

    try:
        parsed = SearchOutput.model_validate_json(_response_text(response, data))
    except ValidationError as exc:
        raise ExperimentError("La búsqueda no devolvió productos válidos.") from exc

    matches, rejected = _validated_candidates(parsed.candidates, sources)
    return matches, {"calls": calls, "sources": len(sources), "rejected": rejected}


def validate_batch_results(
    response: object,
    cases: Iterable[SearchCase],
) -> tuple[dict[str, list[dict[str, str | None]]], dict[str, Any]]:
    """Valida una única acción web y mantiene cada candidato en su prenda."""
    cases = list(cases)
    expected_ids = {case.stem for case in cases}
    data = _response_data(response)
    calls, sources = _search_sources(data)
    if calls != 1:
        raise ExperimentError(
            f"Se esperaba una única acción web para el outfit; se observaron {calls}."
        )

    try:
        parsed = BatchSearchOutput.model_validate_json(_response_text(response, data))
    except ValidationError as exc:
        raise ExperimentError("La búsqueda agrupada no devolvió productos válidos.") from exc

    matches_by_item = {item_id: [] for item_id in expected_ids}
    rejected_by_item = {item_id: 0 for item_id in expected_ids}
    returned_ids: set[str] = set()
    for item in parsed.items:
        item_id = item.item_id.strip()
        if item_id not in expected_ids:
            raise ExperimentError(
                f"La búsqueda agrupada devolvió un item_id desconocido: {item_id}."
            )
        if item_id in returned_ids:
            raise ExperimentError(f"La búsqueda agrupada duplicó el item_id {item_id}.")
        returned_ids.add(item_id)
        matches, rejected = _validated_candidates(item.candidates, sources)
        matches_by_item[item_id] = matches
        rejected_by_item[item_id] = rejected

    return matches_by_item, {
        "calls": calls,
        "sources": len(sources),
        "rejected_by_item": rejected_by_item,
        "missing_item_ids": sorted(expected_ids - returned_ids),
    }


def _usage(data: dict[str, Any]) -> tuple[int, int]:
    usage = data.get("usage") or {}
    return int(usage.get("input_tokens") or 0), int(usage.get("output_tokens") or 0)


def _observed_cost(calls: int, input_tokens: int, output_tokens: int) -> float:
    # Conservador: reserva contenido de búsqueda además de usage hasta verificar
    # cómo se refleja en la primera respuesta válida del smoke.
    return round(
        calls * WEB_SEARCH_CALL_COST
        + (calls * SEARCH_CONTENT_TOKEN_BUDGET + input_tokens) * INPUT_COST_PER_MILLION / 1_000_000
        + output_tokens * OUTPUT_COST_PER_MILLION / 1_000_000,
        6,
    )


def _usage_cost(calls: int, input_tokens: int, output_tokens: int) -> float:
    return round(
        calls * WEB_SEARCH_CALL_COST
        + input_tokens * INPUT_COST_PER_MILLION / 1_000_000
        + output_tokens * OUTPUT_COST_PER_MILLION / 1_000_000,
        6,
    )


def _get_client() -> OpenAI:
    return OpenAI(
        api_key=get_settings().require_openai_api_key(),
        timeout=60,
        max_retries=AUTOMATIC_RETRIES,
    )


def run(
    database: Path,
    selections: Iterable[str],
    *,
    extras: Iterable[str] = (),
    dry_run: bool,
    out_dir: Path | None = None,
    approved_max_calls: int | None = None,
    approval_budget: float | None = None,
    batch: bool = False,
) -> Path:
    cases = load_cases(database, selections, extras)
    planned_calls = BATCH_MAX_TOOL_CALLS if batch else len(cases) * MAX_TOOL_CALLS_PER_ITEM
    budget = cost_budget(len(cases), batch=batch)
    if not dry_run and (
        approved_max_calls != planned_calls
        or approval_budget is None
        or approval_budget < budget["display_ceiling_total"]
    ):
        raise ExperimentError("Los límites no coinciden con el lote y su techo aprobado.")

    out_dir = out_dir or _new_output_dir(dry_run, batch=batch)
    out_dir.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, Any]] = []
    planned: list[dict[str, Any]] = []
    batch_request_file = "001_outfit_batch_request.json" if batch else None
    if batch_request_file:
        _write_json(out_dir / batch_request_file, batch_request_payload(cases))
    for position, case in enumerate(cases, 1):
        request_file = batch_request_file or f"{position:03d}_{case.stem}_request.json"
        if not batch:
            _write_json(out_dir / request_file, request_payload(case))
        planned.append(
            {
                "outfit_id": case.outfit_id,
                "item_index": case.item_index,
                "item_type": case.item.item_type,
                "certainty": case.item.certainty,
                "attributes": case.item.model_dump(exclude={"visual_phrase_en"}),
                "extra_details": case.extra_details,
                "query": case.query,
                "request_file": request_file,
            }
        )
        rows.append(
            {
                "outfit_id": case.outfit_id,
                "item_index": case.item_index,
                "item_type": case.item.item_type,
                "query": case.query,
                "status": "dry_run" if dry_run else "pending",
                "results": [],
                "error": None,
            }
        )

    attempts = observed_calls = 0
    execution: dict[str, Any] = {}
    client = None if dry_run else _get_client()
    if not dry_run and batch:
        started = time.monotonic()
        attempts = 1
        try:
            response = client.responses.create(**batch_request_payload(cases))
            data = _response_data(response)
            _write_json(out_dir / "001_outfit_batch_raw_response.json", data)
            matches_by_item, evidence = validate_batch_results(response, cases)
            input_tokens, output_tokens = _usage(data)
            observed_calls = evidence["calls"]
            missing_ids = set(evidence["missing_item_ids"])
            for case, row in zip(cases, rows):
                matches = matches_by_item[case.stem]
                row.update(
                    {
                        "status": (
                            "completed_missing"
                            if case.stem in missing_ids
                            else "completed"
                            if matches
                            else "completed_empty"
                        ),
                        "results": matches,
                        "rejected_candidate_count": evidence["rejected_by_item"][case.stem],
                    }
                )
            execution.update(
                {
                    "search_call_count": evidence["calls"],
                    "source_url_count": evidence["sources"],
                    "missing_item_ids": evidence["missing_item_ids"],
                    "input_tokens": input_tokens,
                    "output_tokens": output_tokens,
                    "usage_cost_estimate": _usage_cost(
                        evidence["calls"], input_tokens, output_tokens
                    ),
                    "conservative_cost_estimate": _observed_cost(
                        evidence["calls"], input_tokens, output_tokens
                    ),
                }
            )
        except Exception as exc:
            for row in rows:
                row.update(status="provider_or_protocol_error", error=str(exc))
            execution["error"] = str(exc)
        finally:
            execution["seconds"] = round(time.monotonic() - started, 3)
    elif not dry_run:
        for position, (case, row) in enumerate(zip(cases, rows), 1):
            started = time.monotonic()
            attempts += 1
            try:
                response = client.responses.create(**request_payload(case))
                data = _response_data(response)
                _write_json(out_dir / f"{position:03d}_{case.stem}_raw_response.json", data)
                matches, evidence = validate_results(response)
                input_tokens, output_tokens = _usage(data)
                observed_calls += evidence["calls"]
                row.update(
                    {
                        "status": "completed" if matches else "completed_empty",
                        "results": matches,
                        "search_call_count": evidence["calls"],
                        "source_url_count": evidence["sources"],
                        "rejected_candidate_count": evidence["rejected"],
                        "input_tokens": input_tokens,
                        "output_tokens": output_tokens,
                        "usage_cost_estimate": _usage_cost(
                            evidence["calls"], input_tokens, output_tokens
                        ),
                        "conservative_cost_estimate": _observed_cost(
                            evidence["calls"], input_tokens, output_tokens
                        ),
                    }
                )
            except Exception as exc:
                row.update(status="provider_or_protocol_error", error=str(exc))
                break
            finally:
                row["seconds"] = round(time.monotonic() - started, 3)

    _write_json(out_dir / "results.json", rows)
    _write_json(
        out_dir / "manifest.json",
        {
            "mode": "product_search",
            "strategy": "outfit_batch" if batch else "per_item",
            "dry_run": dry_run,
            "created_at_utc": datetime.now(timezone.utc).isoformat(),
            "model": MODEL,
            "allowed_store_domains": STORES,
            "planned_cases": planned,
            "planned_text_extraction_calls": 0,
            "planned_image_calls": 0,
            "planned_web_search_calls": planned_calls,
            "max_tool_calls_per_response": (
                BATCH_MAX_TOOL_CALLS if batch else MAX_TOOL_CALLS_PER_ITEM
            ),
            "automatic_retries": AUTOMATIC_RETRIES,
            "provider_call_attempts": attempts,
            "observed_web_search_calls": observed_calls,
            "execution": execution,
            "cost_budget": budget,
            "approval_budget": approval_budget,
            "stop_condition": "Detenerse en el primer error; no reintentar ni ampliar.",
        },
    )

    print(
        f"{'DRY-RUN GRATUITO' if dry_run else 'EJECUCIÓN REAL'}: "
        f"{len(cases)} prendas, 0 extracciones, 0 imágenes, "
        f"máximo {planned_calls} búsquedas, 0 reintentos.\n"
        f"Techo mostrado: ${budget['display_ceiling_total']:.2f}; "
        f"presupuesto recomendado: ${budget['recommended_approval_budget']:.2f}.\n"
        f"Resultados: {out_dir}"
    )
    if dry_run:
        print("Coste real: $0.00. No se creó ningún cliente OpenAI.")
    return out_dir


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", type=Path, default=Path("outfit.db"))
    parser.add_argument("--selection", action="append", required=True)
    parser.add_argument("--extra", action="append", default=[])
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument(
        "--batch",
        action="store_true",
        help="Agrupa todas las prendas en una única respuesta y acción web.",
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--execute", action="store_true")
    parser.add_argument("--approved-max-calls", type=int)
    parser.add_argument("--approval-budget", type=float)
    args = parser.parse_args()

    if args.dry_run and (args.approved_max_calls is not None or args.approval_budget is not None):
        parser.error("El dry-run no necesita flags de aprobación.")
    if args.execute and (args.approved_max_calls is None or args.approval_budget is None):
        parser.error("--execute requiere --approved-max-calls y --approval-budget.")
    try:
        run(
            args.database,
            args.selection,
            extras=args.extra,
            dry_run=args.dry_run,
            out_dir=args.output_dir,
            approved_max_calls=args.approved_max_calls,
            approval_budget=args.approval_budget,
            batch=args.batch,
        )
    except ExperimentError as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    main()
