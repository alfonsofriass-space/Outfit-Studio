"""Ejecuta smokes y comparaciones visuales reales del pipeline de outfits.

Uso habitual:
    python -m experiments.run_experiment --limit 5
    python -m experiments.run_experiment --ab-complex
    python -m experiments.run_experiment \
        --reuse-extractions experiments/output/20260715_180603_ab_complex \
        --case-ids C08,C10
    python -m experiments.run_experiment \
        --ab-worn-view experiments/output/20260715_192645_wide_refinement \
        --case-id C10 --dry-run

El modo A/B estructura cada descripción una sola vez y genera dos imágenes
ciego-etiquetadas (X/Y). De este modo, la única variable experimental es el
layout del prompt de imagen y no se duplica el coste del modelo de texto.
"""

import argparse
import base64
import csv
import json
import random
import shutil
import struct
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import NamedTuple

from openai import OpenAI

from app.config import get_settings
from app.pricing import (
    estimate_gpt_image_2_token_cost,
    estimate_image_cost,
    estimate_text_cost,
)
from app.prompts.image_prompt_builder import (
    STYLE_BLOCK,
    build_image_prompt,
    build_styling_block,
)
from app.prompts.worn_prompt_builder import build_worn_prompt
from app.schemas import ImageDetails, OutfitExtraction, OutfitItem
from app.services.openai_image import GENERATED_DIR, generate_outfit_image
from app.services.openai_proposals import propose_outfits_from_situation
from app.services.openai_text import extract_outfit_from_text
from app.validation import evaluate_minimum_info, has_minimum_info, is_outfit_valid

OUTPUT_ROOT = Path(__file__).resolve().parent / "output"
AB_BLIND_SEED = 20260715
ESTIMATED_TEXT_COST_PER_CASE = 0.0006

FLAT_BASELINE = "flat_list_baseline"
ZONED_BUILDER = "zoned_builder_v2"

WORN_TEXT_ONLY = "text_only_generation"
WORN_REFERENCE_EDIT = "flat_lay_reference_edit"
WORN_AB_BLIND_SEED = 20260719
WORN_IMAGE_MODEL = "gpt-image-2"
WORN_IMAGE_QUALITY = "low"
WORN_IMAGE_SIZE = "1024x1536"
WORN_IMAGE_OUTPUT_COST = 0.005
WORN_AUTOMATIC_RETRIES = 0

# La API no publica una fórmula previa para los tokens de entrada de imagen de
# gpt-image-2. El runner separa estos márgenes conservadores del coste de salida
# verificado y sustituye la estimación por usage cuando el proveedor lo devuelve.
WORN_TEXT_INPUT_TOKEN_BUDGET_PER_CALL = 1_000
WORN_REFERENCE_IMAGE_TOKEN_BUDGET = 10_000

RUBRIC_FIELDS = (
    "score_exact_items",
    "score_body_order",
    "score_complete_no_overlap",
    "score_proportions",
    "score_accessories",
    "score_explicit_details",
    "score_no_forbidden_elements",
)

REFINEMENT_RUBRIC_FIELDS = (
    "score_exact_items",
    "score_body_readability",
    "score_canvas_use",
    "score_complete_no_overlap",
    "score_accessory_rail",
    "score_explicit_details",
    "score_no_forbidden_elements",
)

WORN_RUBRIC_FIELDS = (
    "score_exact_items",
    "score_reference_fidelity",
    "score_layering_and_fit",
    "score_accessories",
    "score_full_body_legibility",
    "score_no_extra_or_duplicate",
    "score_no_board_or_text",
    "score_user_usefulness",
)


class ABCase(NamedTuple):
    case_id: str
    description: str
    expected_items: int


class SavedExtractionCase(NamedTuple):
    case_id: str
    source_path: Path
    description: str
    payload: dict
    extraction: OutfitExtraction


class WornSourceCase(NamedTuple):
    saved: SavedExtractionCase
    flat_lay_path: Path
    flat_lay_width: int
    flat_lay_height: int


# Set base histórico. Se conserva para smokes baratos y regresiones generales.
TEST_SET = [
    # --- simples (2 prendas) ---
    ("simple", "camiseta blanca y vaqueros azules"),
    ("simple", "sudadera gris y pantalón de chándal negro"),
    ("simple", "camisa vaquera y chinos beige"),
    ("simple", "jersey de punto marrón y falda negra"),
    # --- con footwear/accessory (3-4 piezas) ---
    ("media", "chaqueta negra, vaqueros anchos y zapatillas blancas"),
    ("media", "blazer azul marino, camisa blanca, pantalón de vestir gris y mocasines"),
    ("media", "camiseta negra, shorts vaqueros, gorra roja y sandalias"),
    ("media", "abrigo beige, jersey de cuello alto negro y botas chelsea"),
    # --- detalle fino ---
    (
        "detalle_fino",
        "chaqueta negra con pequeño bordado floral blanco en las mangas y vaqueros rectos",
    ),
    ("detalle_fino", "camisa blanca de lino con botones de nácar y pantalón de pinzas camel"),
    ("detalle_fino", "jersey de rayas horizontales azules y blancas con vaqueros claros"),
    ("detalle_fino", "vestido negro con lunares blancos pequeños y zapatos de tacón rojos"),
    ("detalle_fino", "sudadera gris con estampado gráfico en el pecho y pantalón cargo verde"),
    ("detalle_fino", "chaqueta de cuero marrón con cremalleras plateadas y botas negras"),
    # --- complejos históricos ---
    (
        "complejo",
        "abrigo largo camel, jersey de cuello alto crema, bufanda de cuadros, "
        "vaqueros oscuros y botas de piel",
    ),
    (
        "complejo",
        "gabardina beige, camisa azul, corbata granate, pantalón de vestir gris, "
        "cinturón marrón y zapatos oxford",
    ),
    (
        "complejo",
        "chaqueta bomber verde militar, camiseta blanca, riñonera negra cruzada, "
        "gafas de sol y zapatillas altas",
    ),
    (
        "complejo",
        "blazer de cuadros, camiseta negra, falda plisada, medias tupidas, bolso pequeño y botines",
    ),
    # --- one_piece / prenda fuerte ---
    ("one_piece", "vestido rojo largo de verano"),
    ("one_piece", "mono negro entallado"),
    ("one_piece", "peto vaquero y camiseta de rayas"),
    # --- casos límite / ambiguos ---
    ("limite", "algo elegante en tonos oscuros"),
    ("limite", "un look bonito para el verano"),
    ("limite", "zapatillas blancas"),
]


# Doce casos de 5-8 piezas: los cuatro complejos históricos y ocho casos nuevos.
# Todos incluyen al menos un accesorio para que la rúbrica no tenga valores N/A.
AB_COMPLEX_TEST_SET = [
    ABCase(
        "C01",
        "abrigo largo camel, jersey de cuello alto crema, bufanda de cuadros, "
        "vaqueros oscuros y botas de piel",
        5,
    ),
    ABCase(
        "C02",
        "gabardina beige, camisa azul, corbata granate, pantalón de vestir gris, "
        "cinturón marrón y zapatos oxford",
        6,
    ),
    ABCase(
        "C03",
        "chaqueta bomber verde militar, camiseta blanca, riñonera negra cruzada, "
        "gafas de sol y zapatillas altas",
        5,
    ),
    ABCase(
        "C04",
        "blazer de cuadros, camiseta negra, falda plisada, medias tupidas, bolso pequeño y botines",
        6,
    ),
    ABCase(
        "C05",
        "parka verde oliva, sudadera gris con capucha, camiseta blanca, "
        "pantalón cargo negro, gorro de lana y botas marrones",
        6,
    ),
    ABCase(
        "C06",
        "cazadora vaquera azul, vestido midi floral rojo, cinturón de cuero marrón, "
        "bolso crema y botines negros",
        5,
    ),
    ABCase(
        "C07",
        "chaleco acolchado negro sobre jersey beige, camisa blanca, pantalón chino "
        "verde, reloj plateado y zapatillas blancas",
        6,
    ),
    ABCase(
        "C08",
        "abrigo de lana gris, blazer azul marino, camisa de rayas, pantalón negro, "
        "bufanda roja, guantes, bolso marrón y mocasines",
        8,
    ),
    ABCase(
        "C09",
        "chaqueta de cuero negra con cremalleras plateadas, camiseta gris, falda "
        "plisada granate, medias negras, bolso pequeño y botas altas",
        6,
    ),
    ABCase(
        "C10",
        "kimono estampado azul, top blanco, pantalón palazzo beige, collar dorado, "
        "bolso de rafia y sandalias marrones",
        6,
    ),
    ABCase(
        "C11",
        "anorak amarillo con capucha, jersey negro, vaqueros rectos, mochila gris, "
        "gorra azul y zapatillas rojas",
        6,
    ),
    ABCase(
        "C12",
        "blazer gris de raya diplomática, chaleco gris, camisa blanca, pantalón gris, "
        "corbata granate, reloj negro y zapatos oxford marrones",
        7,
    ),
]


# P10C: cuatro situaciones que cubren registros distintos. La cuarta es
# deliberadamente vaga, para ver si el modelo propone o se refugia en una
# aclaración cuando el usuario no le da casi nada.
PROPOSAL_TEST_SET = [
    ("P01", "Boda de tarde en octubre, en el campo, voy de invitado"),
    ("P02", "Cena de empresa el jueves, código de vestimenta smart casual"),
    ("P03", "Hace 8 grados y voy a andar todo el día por la ciudad"),
    ("P04", "Una comida familiar el domingo, nada especial"),
]

# Techo aprobado para P10C. El runner para si lo superaría: una desviación del
# presupuesto es motivo de parada, no de continuar y avisar después.
PROPOSAL_MAX_TEXT_CALLS = 8
PROPOSAL_INPUT_TOKEN_BUDGET = 3_000
PROPOSAL_OUTPUT_TOKEN_BUDGET = 1_600

PROPOSAL_FIELDS = (
    "case_id",
    "situation",
    "status",
    "model_primary",
    "model_fallback",
    "text_calls",
    "input_tokens",
    "output_tokens",
    "cost_estimate",
    "seconds",
    "proposal_titles",
    "all_generable",
    "distinct_silhouettes",
    "all_with_footwear",
    "no_invented_brand",
    "error",
)


def _new_output_dir(suffix: str = "") -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    dirname = f"{stamp}_{suffix}" if suffix else stamp
    out_dir = OUTPUT_ROOT / dirname
    out_dir.mkdir(parents=True, exist_ok=False)
    return out_dir


def _write_json(path: Path, payload: object) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _copy_generated_image(image: ImageDetails, destination: Path) -> None:
    source = GENERATED_DIR / Path(image.url_or_base64).name
    if not source.exists():
        raise FileNotFoundError(f"No se encontró la imagen generada: {source}")
    shutil.copy(source, destination)


def _visual_phrase(item: OutfitItem) -> str:
    return item.visual_phrase_en


def build_flat_baseline_prompt(
    items: list[OutfitItem],
    styling_notes_en: list[str] | None = None,
) -> str:
    """Baseline controlado: mismo contenido y restricciones, sin zonas corporales."""
    phrases = "; ".join(_visual_phrase(item) for item in items)
    lines = [
        STYLE_BLOCK,
        "Arrange all the following pieces in one balanced flat-lay composition:",
        f"- Items: {phrases}.",
    ]
    lines.extend(build_styling_block(styling_notes_en))
    return "\n".join(lines)


def _configured_image_cost() -> float:
    settings = get_settings()
    return estimate_image_cost(settings.image_quality, settings.image_size)


def _load_saved_extraction(source_dir: Path, case_id: str) -> SavedExtractionCase:
    normalized_case_id = case_id.strip().upper()
    matches = sorted(source_dir.glob(f"*_{normalized_case_id}_extraction.json"))
    if len(matches) != 1:
        raise ValueError(
            f"Se esperaba una extracción para {normalized_case_id} en "
            f"{source_dir}; encontradas: {len(matches)}."
        )

    source_path = matches[0]
    try:
        payload = json.loads(source_path.read_text(encoding="utf-8"))
        description = payload["description"]
        if not isinstance(description, str) or not description.strip():
            raise ValueError("la descripción original está vacía")
        extraction = OutfitExtraction.model_validate(payload["extraction"])
    except (KeyError, json.JSONDecodeError, ValueError) as exc:
        raise ValueError(f"Extracción no válida en {source_path}: {exc}") from exc

    valid, validation_message = is_outfit_valid(extraction)
    if not valid:
        raise ValueError(f"Extracción no usable en {source_path}: {validation_message}")
    return SavedExtractionCase(
        case_id=normalized_case_id,
        source_path=source_path,
        description=description,
        payload=payload,
        extraction=extraction,
    )


def _read_png_dimensions(path: Path) -> tuple[int, int]:
    header = path.read_bytes()[:24]
    if len(header) < 24 or header[:8] != b"\x89PNG\r\n\x1a\n" or header[12:16] != b"IHDR":
        raise ValueError(f"La referencia no es un PNG válido: {path}")
    return struct.unpack(">II", header[16:24])


def _load_worn_source(source_dir: Path, case_id: str) -> WornSourceCase:
    source_dir = source_dir.resolve()
    if not source_dir.is_dir():
        raise ValueError(f"No existe el directorio de origen: {source_dir}")

    saved = _load_saved_extraction(source_dir, case_id)
    matches = sorted(source_dir.glob(f"*_{saved.case_id}_wide.png"))
    if len(matches) != 1:
        raise ValueError(
            f"Se esperaba un flat-lay *_wide.png para {saved.case_id} en "
            f"{source_dir}; encontrados: {len(matches)}."
        )

    flat_lay_path = matches[0]
    width, height = _read_png_dimensions(flat_lay_path)
    return WornSourceCase(saved, flat_lay_path, width, height)


def _worn_cost_budget() -> dict[str, float | int]:
    output_cost = 2 * WORN_IMAGE_OUTPUT_COST
    text_input_cost = estimate_gpt_image_2_token_cost(
        text_input_tokens=2 * WORN_TEXT_INPUT_TOKEN_BUDGET_PER_CALL,
    )
    reference_input_cost = estimate_gpt_image_2_token_cost(
        image_input_tokens=WORN_REFERENCE_IMAGE_TOKEN_BUDGET,
    )
    subtotal = output_cost + text_input_cost + reference_input_cost
    return {
        "output_cost_estimate": output_cost,
        "text_input_token_budget_per_call": WORN_TEXT_INPUT_TOKEN_BUDGET_PER_CALL,
        "text_input_cost_allowance": text_input_cost,
        "reference_image_token_budget": WORN_REFERENCE_IMAGE_TOKEN_BUDGET,
        "reference_image_cost_allowance": reference_input_cost,
        "calculated_subtotal": subtotal,
        "approval_budget_with_contingency": 0.11,
    }


def _get_worn_image_client() -> OpenAI:
    settings = get_settings()
    return OpenAI(
        api_key=settings.require_openai_api_key(),
        timeout=settings.openai_timeout_image,
        max_retries=WORN_AUTOMATIC_RETRIES,
    )


def _usage_value(source: object | None, field: str) -> int | None:
    if source is None:
        return None
    value = source.get(field) if isinstance(source, dict) else getattr(source, field, None)
    return value if isinstance(value, int) and value >= 0 else None


def _image_response_usage(response: object) -> dict[str, int | None]:
    usage = getattr(response, "usage", None)
    details = (
        usage.get("input_tokens_details")
        if isinstance(usage, dict)
        else getattr(usage, "input_tokens_details", None)
    )
    return {
        "text_input_tokens": _usage_value(details, "text_tokens"),
        "image_input_tokens": _usage_value(details, "image_tokens"),
        "image_output_tokens": _usage_value(usage, "output_tokens"),
    }


def _usage_cost_estimate(usage: dict[str, int | None]) -> float | None:
    if any(value is None for value in usage.values()):
        return None
    return estimate_gpt_image_2_token_cost(
        text_input_tokens=usage["text_input_tokens"],
        image_input_tokens=usage["image_input_tokens"],
        image_output_tokens=usage["image_output_tokens"],
    )


def _request_worn_image(
    client: OpenAI,
    *,
    variant: str,
    prompt: str,
    reference_path: Path,
) -> tuple[bytes, dict[str, int | None]]:
    request = {
        "model": WORN_IMAGE_MODEL,
        "prompt": prompt,
        "size": WORN_IMAGE_SIZE,
        "quality": WORN_IMAGE_QUALITY,
        "n": 1,
    }
    if variant == WORN_TEXT_ONLY:
        response = client.images.generate(**request)
    elif variant == WORN_REFERENCE_EDIT:
        with reference_path.open("rb") as reference_file:
            # gpt-image-2 usa alta fidelidad automáticamente y rechaza input_fidelity.
            response = client.images.edit(image=reference_file, **request)
    else:
        raise ValueError(f"Variante vestida desconocida: {variant}")

    data = getattr(response, "data", None)
    encoded = getattr(data[0], "b64_json", None) if data else None
    if not isinstance(encoded, str) or not encoded:
        raise ValueError("OpenAI no devolvió una imagen base64 utilizable.")
    decoded = base64.b64decode(encoded, validate=True)
    if not decoded:
        raise ValueError("OpenAI devolvió una imagen vacía.")
    return decoded, _image_response_usage(response)


def _write_csv(path: Path, rows: list[dict], fieldnames: tuple[str, ...] | list[str]) -> None:
    with path.open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def run(limit: int | None, repeat: int) -> Path:
    """Ejecuta el benchmark histórico con una imagen por descripción."""
    out_dir = _new_output_dir()
    cases = TEST_SET if limit is None else TEST_SET[:limit]
    rows = []
    total_image_cost = 0.0

    print(f"Experimento {out_dir.name} — {len(cases)} descripciones × {repeat} repetición(es)\n")

    idx = 0
    for category, description in cases:
        for _ in range(repeat):
            idx += 1
            row = {
                "n": idx,
                "category": category,
                "description": description,
                "pre_check": "",
                "status": "",
                "text_primary": "",
                "text_fallback": "",
                "n_items": 0,
                "extraction_file": "",
                "prompt_file": "",
                "image_file": "",
                "cost": 0.0,
                "seconds": 0.0,
                "error": "",
            }
            started = time.monotonic()
            try:
                if not has_minimum_info(description):
                    row["pre_check"] = "needs_clarification"
                    row["status"] = "needs_clarification"
                    print(f"[{idx}] {category}: SIN INFO (heurística) — no se generó imagen")
                    continue
                row["pre_check"] = "ok"

                extraction, primary, fallback = extract_outfit_from_text(description)
                row["status"] = extraction.status
                row["text_primary"] = primary
                row["text_fallback"] = fallback or ""
                row["n_items"] = len(extraction.items)

                extraction_name = f"{idx:03d}_extraction.json"
                _write_json(
                    out_dir / extraction_name,
                    {
                        "text_primary": primary,
                        "text_fallback": fallback,
                        "extraction": extraction.model_dump(mode="json"),
                    },
                )
                row["extraction_file"] = extraction_name

                if extraction.status == "needs_clarification":
                    print(f"[{idx}] {category}: needs_clarification (post-texto)")
                    continue

                built = build_image_prompt(extraction.items, extraction.styling_notes_en)
                prompt_name = f"{idx:03d}_prompt.txt"
                (out_dir / prompt_name).write_text(built.prompt, encoding="utf-8")
                row["prompt_file"] = prompt_name

                image = generate_outfit_image(built.prompt)
                cost = estimate_image_cost(image.quality, image.size)
                total_image_cost += cost
                row["cost"] = cost

                image_name = f"{idx:03d}_{category}.png"
                _copy_generated_image(image, out_dir / image_name)
                row["image_file"] = image_name
                print(
                    f"[{idx}] {category}: OK -> {image_name}  "
                    f"(fallback={fallback or '-'}, ${cost:.4f})"
                )
            except Exception as exc:  # El CSV debe conservar cada fallo real.
                row["error"] = str(exc)
                print(f"[{idx}] {category}: EXCEPCIÓN — {exc}")
            finally:
                row["seconds"] = round(time.monotonic() - started, 2)
                rows.append(row)

    csv_path = out_dir / "results.csv"
    _write_csv(csv_path, rows, list(rows[0]))

    images_ok = sum(1 for row in rows if row["image_file"])
    fallbacks = sum(1 for row in rows if row["text_fallback"])
    clarifications = sum(1 for row in rows if row["status"] == "needs_clarification")

    print("\n" + "=" * 50)
    print(f"Total generaciones intentadas: {len(rows)}")
    print(f"Imágenes generadas OK:         {images_ok}")
    print(f"needs_clarification:           {clarifications}")
    print(f"Fallbacks a mini:              {fallbacks}")
    print(f"Coste estimado de imágenes:    ${total_image_cost:.4f}")
    print(f"\nResultados: {csv_path}")
    print(f"Imágenes:   {out_dir}")
    return out_dir


def _empty_ab_row(case: ABCase) -> dict:
    return {
        "case_id": case.case_id,
        "description": case.description,
        "expected_items": case.expected_items,
        "extracted_items": 0,
        "blind_label": "",
        "status": "",
        "text_primary": "",
        "text_fallback": "",
        "text_seconds": 0.0,
        "image_seconds": 0.0,
        "extraction_file": "",
        "prompt_file": "",
        "image_file": "",
        "image_cost_estimate": 0.0,
        "error": "",
        **{field: "" for field in RUBRIC_FIELDS},
        "reviewer_notes": "",
    }


def run_ab_complex(
    limit: int | None = None,
    *,
    out_dir: Path | None = None,
    blind_seed: int = AB_BLIND_SEED,
) -> Path:
    """Compara lista plana (A) y builder zonado (B) con evaluación ciega X/Y."""
    cases = AB_COMPLEX_TEST_SET if limit is None else AB_COMPLEX_TEST_SET[:limit]
    if not cases:
        raise ValueError("El A/B necesita al menos un caso.")

    out_dir = out_dir or _new_output_dir("ab_complex")
    out_dir.mkdir(parents=True, exist_ok=True)
    per_image_cost = _configured_image_cost()
    planned_images = len(cases) * 2
    planned_image_cost = planned_images * per_image_cost
    planned_text_cost = len(cases) * ESTIMATED_TEXT_COST_PER_CASE

    print(
        f"A/B complejo {out_dir.name} — {len(cases)} casos, {planned_images} imágenes\n"
        f"Presupuesto estimado: ${planned_image_cost:.4f} imágenes + "
        f"~${planned_text_cost:.4f} texto = ~${planned_image_cost + planned_text_cost:.4f}\n"
    )

    rng = random.Random(blind_seed)
    variant_map: dict[str, dict[str, str]] = {}
    rows: list[dict] = []
    total_image_cost = 0.0

    for position, case in enumerate(cases, start=1):
        minimum = evaluate_minimum_info(case.description)
        if not minimum.is_sufficient:
            row = _empty_ab_row(case)
            row["status"] = "needs_clarification"
            row["error"] = f"precheck:{minimum.reason.value}"
            rows.append(row)
            print(f"[{case.case_id}] BLOQUEADO por precheck — sin gasto")
            continue

        text_started = time.monotonic()
        try:
            extraction, primary, fallback = extract_outfit_from_text(case.description)
        except Exception as exc:
            row = _empty_ab_row(case)
            row["status"] = "text_error"
            row["text_seconds"] = round(time.monotonic() - text_started, 2)
            row["error"] = str(exc)
            rows.append(row)
            print(f"[{case.case_id}] ERROR de texto — sin imágenes: {exc}")
            continue

        text_seconds = round(time.monotonic() - text_started, 2)
        valid, validation_message = is_outfit_valid(extraction)
        if not valid:
            row = _empty_ab_row(case)
            row.update(
                {
                    "status": extraction.status,
                    "text_primary": primary,
                    "text_fallback": fallback or "",
                    "text_seconds": text_seconds,
                    "extracted_items": len(extraction.items),
                    "error": validation_message,
                }
            )
            rows.append(row)
            print(f"[{case.case_id}] extracción no usable — sin imágenes")
            continue

        extraction_name = f"{position:03d}_{case.case_id}_extraction.json"
        _write_json(
            out_dir / extraction_name,
            {
                "case_id": case.case_id,
                "description": case.description,
                "expected_items": case.expected_items,
                "text_primary": primary,
                "text_fallback": fallback,
                "text_seconds": text_seconds,
                "extraction": extraction.model_dump(mode="json"),
            },
        )

        flat_prompt = build_flat_baseline_prompt(extraction.items, extraction.styling_notes_en)
        zoned = build_image_prompt(extraction.items, extraction.styling_notes_en)
        prompts = {
            FLAT_BASELINE: flat_prompt,
            ZONED_BUILDER: zoned.prompt,
        }

        labels = ["X", "Y"]
        if rng.choice((True, False)):
            mapping = {"X": FLAT_BASELINE, "Y": ZONED_BUILDER}
        else:
            mapping = {"X": ZONED_BUILDER, "Y": FLAT_BASELINE}
        variant_map[case.case_id] = mapping

        for label in labels:
            variant = mapping[label]
            prompt = prompts[variant]
            prompt_name = f"{position:03d}_{case.case_id}_{label}_prompt.txt"
            (out_dir / prompt_name).write_text(prompt, encoding="utf-8")

            row = _empty_ab_row(case)
            row.update(
                {
                    "extracted_items": len(extraction.items),
                    "blind_label": label,
                    "status": extraction.status,
                    "text_primary": primary,
                    "text_fallback": fallback or "",
                    "text_seconds": text_seconds,
                    "extraction_file": extraction_name,
                    "prompt_file": prompt_name,
                }
            )

            image_started = time.monotonic()
            try:
                image = generate_outfit_image(prompt)
                cost = estimate_image_cost(image.quality, image.size)
                image_name = f"{position:03d}_{case.case_id}_{label}.png"
                _copy_generated_image(image, out_dir / image_name)
                row["image_file"] = image_name
                row["image_cost_estimate"] = cost
                total_image_cost += cost
                print(
                    f"[{case.case_id}/{label}] OK -> {image_name} "
                    f"({len(extraction.items)} items, ${cost:.4f})"
                )
            except Exception as exc:  # La otra variante debe continuar si una falla.
                row["error"] = str(exc)
                print(f"[{case.case_id}/{label}] ERROR de imagen: {exc}")
            finally:
                row["image_seconds"] = round(time.monotonic() - image_started, 2)
                rows.append(row)

    result_fields = tuple(_empty_ab_row(cases[0]))
    _write_csv(out_dir / "results_blind.csv", rows, result_fields)
    _write_json(
        out_dir / "manifest.json",
        {
            "mode": "ab_complex_blind",
            "created_at_utc": datetime.now(timezone.utc).isoformat(),
            "blind_seed": blind_seed,
            "cases": [case._asdict() for case in cases],
            "variants": {
                FLAT_BASELINE: "Mismas frases/estilo/restricciones en una lista plana.",
                ZONED_BUILDER: "Builder v2 de producción con zonas corporales.",
            },
            "rubric_fields": RUBRIC_FIELDS,
            "planned_images": planned_images,
            "planned_image_cost_estimate": planned_image_cost,
            "planned_text_cost_estimate": planned_text_cost,
            "note": "Puntuar results_blind.csv antes de abrir variant_map.json.",
        },
    )
    _write_json(
        out_dir / "variant_map.json",
        {
            "warning": "NO ABRIR ANTES DE COMPLETAR LA REVISIÓN CIEGA.",
            "cases": variant_map,
        },
    )

    images_ok = sum(bool(row["image_file"]) for row in rows)
    fallbacks = len({row["case_id"] for row in rows if row["text_fallback"]})
    errors = sum(bool(row["error"]) for row in rows)
    print("\n" + "=" * 64)
    print(f"Imágenes generadas OK:          {images_ok}/{planned_images}")
    print(f"Casos con fallback:             {fallbacks}/{len(cases)}")
    print(f"Filas con error:                {errors}")
    print(f"Coste estimado de imágenes:     ${total_image_cost:.4f}")
    print(f"Resultados ciegos:              {out_dir / 'results_blind.csv'}")
    print(f"Mapa X/Y (no abrir aún):        {out_dir / 'variant_map.json'}")
    return out_dir


def run_reused_extractions(
    source_dir: Path,
    case_ids: list[str],
    *,
    out_dir: Path | None = None,
) -> Path:
    """Regenera casos guardados con el builder actual, sin otra llamada de texto."""
    source_dir = source_dir.resolve()
    if not source_dir.is_dir():
        raise ValueError(f"No existe el directorio de origen: {source_dir}")

    normalized_ids = [case_id.strip().upper() for case_id in case_ids if case_id.strip()]
    if not normalized_ids:
        raise ValueError("La regresión necesita al menos un case_id.")
    if len(normalized_ids) != len(set(normalized_ids)):
        raise ValueError("Los case_id de la regresión no pueden repetirse.")

    # Validamos el lote completo antes de crear salidas o incurrir en coste.
    cases = [_load_saved_extraction(source_dir, case_id) for case_id in normalized_ids]

    out_dir = out_dir or _new_output_dir("wide_refinement")
    out_dir.mkdir(parents=True, exist_ok=True)
    per_image_cost = _configured_image_cost()
    planned_image_cost = len(cases) * per_image_cost
    print(
        f"Regresión focalizada {out_dir.name} — {len(cases)} imágenes\n"
        f"Extracciones reutilizadas: {source_dir}\n"
        f"Presupuesto estimado: ${planned_image_cost:.4f} imágenes + "
        "$0.0000 texto\n"
    )

    rows: list[dict] = []
    total_image_cost = 0.0
    for position, case in enumerate(cases, start=1):
        extraction = case.extraction
        prompt = build_image_prompt(extraction.items, extraction.styling_notes_en).prompt
        extraction_name = f"{position:03d}_{case.case_id}_extraction.json"
        prompt_name = f"{position:03d}_{case.case_id}_prompt.txt"
        image_name = f"{position:03d}_{case.case_id}_wide.png"

        copied_payload = dict(case.payload)
        copied_payload["reused_from"] = str(case.source_path)
        _write_json(out_dir / extraction_name, copied_payload)
        (out_dir / prompt_name).write_text(prompt, encoding="utf-8")

        row = {
            "case_id": case.case_id,
            "description": case.description,
            "expected_items": case.payload.get("expected_items", len(extraction.items)),
            "extracted_items": len(extraction.items),
            "status": extraction.status,
            "source_extraction_file": str(case.source_path),
            "extraction_file": extraction_name,
            "prompt_file": prompt_name,
            "image_file": "",
            "image_seconds": 0.0,
            "image_cost_estimate": 0.0,
            "text_cost_estimate": 0.0,
            "error": "",
            **{field: "" for field in REFINEMENT_RUBRIC_FIELDS},
            "reviewer_notes": "",
        }

        image_started = time.monotonic()
        try:
            image = generate_outfit_image(prompt)
            cost = estimate_image_cost(image.quality, image.size)
            _copy_generated_image(image, out_dir / image_name)
            row["image_file"] = image_name
            row["image_cost_estimate"] = cost
            total_image_cost += cost
            print(
                f"[{case.case_id}] OK -> {image_name} ({len(extraction.items)} items, ${cost:.4f})"
            )
        except Exception as exc:
            row["error"] = str(exc)
            print(f"[{case.case_id}] ERROR de imagen: {exc}")
        finally:
            row["image_seconds"] = round(time.monotonic() - image_started, 2)
            rows.append(row)

    result_fields = tuple(rows[0])
    _write_csv(out_dir / "results.csv", rows, result_fields)
    _write_json(
        out_dir / "manifest.json",
        {
            "mode": "reused_extractions_wide_refinement",
            "created_at_utc": datetime.now(timezone.utc).isoformat(),
            "source_dir": str(source_dir),
            "case_ids": normalized_ids,
            "rubric_fields": REFINEMENT_RUBRIC_FIELDS,
            "planned_images": len(cases),
            "planned_image_cost_estimate": planned_image_cost,
            "planned_text_cost_estimate": 0.0,
            "note": "Usa extracciones guardadas; no ejecuta el modelo de texto.",
        },
    )

    images_ok = sum(bool(row["image_file"]) for row in rows)
    errors = sum(bool(row["error"]) for row in rows)
    print("\n" + "=" * 64)
    print(f"Imágenes generadas OK:          {images_ok}/{len(cases)}")
    print(f"Filas con error:                {errors}")
    print(f"Coste estimado de imágenes:     ${total_image_cost:.4f}")
    print("Coste estimado de texto:        $0.0000 (reutilizado)")
    print(f"Resultados:                     {out_dir / 'results.csv'}")
    return out_dir


def _empty_worn_row(case: SavedExtractionCase, label: str) -> dict:
    return {
        "case_id": case.case_id,
        "description": case.description,
        "blind_label": label,
        "status": "",
        "extracted_items": len(case.extraction.items),
        "source_extraction_file": str(case.source_path),
        "extraction_file": "",
        "source_flat_lay_file": "",
        "prompt_file": "",
        "image_file": "",
        "image_seconds": 0.0,
        "output_cost_estimate": WORN_IMAGE_OUTPUT_COST,
        "usage_text_input_tokens": "",
        "usage_image_input_tokens": "",
        "usage_image_output_tokens": "",
        "usage_cost_estimate": "",
        "error": "",
        **{field: "" for field in WORN_RUBRIC_FIELDS},
        "reviewer_notes": "",
    }


def run_ab_worn_view(
    source_dir: Path,
    case_id: str,
    *,
    dry_run: bool = False,
    out_dir: Path | None = None,
    blind_seed: int = WORN_AB_BLIND_SEED,
) -> Path:
    """Compara un maniquí desde texto contra una edición con flat-lay de referencia."""
    source = _load_worn_source(source_dir, case_id)
    budget = _worn_cost_budget()
    client = None if dry_run else _get_worn_image_client()

    suffix = "ab_worn_view_dry_run" if dry_run else "ab_worn_view"
    out_dir = out_dir or _new_output_dir(suffix)
    out_dir.mkdir(parents=True, exist_ok=True)

    copied_extraction_name = f"001_{source.saved.case_id}_extraction.json"
    copied_reference_name = f"001_{source.saved.case_id}_flat_lay.png"
    copied_payload = dict(source.saved.payload)
    copied_payload["reused_from"] = str(source.saved.source_path)
    copied_payload["flat_lay_reference"] = str(source.flat_lay_path)
    _write_json(out_dir / copied_extraction_name, copied_payload)
    shutil.copy2(source.flat_lay_path, out_dir / copied_reference_name)

    prompts = {
        WORN_TEXT_ONLY: build_worn_prompt(source.saved.extraction, use_reference=False),
        WORN_REFERENCE_EDIT: build_worn_prompt(source.saved.extraction, use_reference=True),
    }
    rng = random.Random(blind_seed)
    if rng.choice((True, False)):
        mapping = {"X": WORN_TEXT_ONLY, "Y": WORN_REFERENCE_EDIT}
    else:
        mapping = {"X": WORN_REFERENCE_EDIT, "Y": WORN_TEXT_ONLY}

    mode_label = "PREPARACIÓN gratuita" if dry_run else "EJECUCIÓN REAL"
    print(
        f"A/B vista vestida {source.saved.case_id} — {mode_label}\n"
        f"Extracción reutilizada: {source.saved.source_path}\n"
        f"Flat-lay reutilizado:   {source.flat_lay_path} "
        f"({source.flat_lay_width}x{source.flat_lay_height})\n"
        "Llamadas de texto:     0\n"
        "Llamadas de imagen:    2 (1 generate + 1 edit)\n"
        f"Reintentos automáticos: {WORN_AUTOMATIC_RETRIES}\n"
        f"Presupuesto máximo estimado con contingencia: "
        f"${budget['approval_budget_with_contingency']:.2f}\n"
    )

    rows: list[dict] = []
    provider_call_attempts = 0
    for label in ("X", "Y"):
        variant = mapping[label]
        prompt = prompts[variant]
        prompt_name = f"001_{source.saved.case_id}_{label}_prompt.txt"
        image_name = f"001_{source.saved.case_id}_{label}.png"
        (out_dir / prompt_name).write_text(prompt, encoding="utf-8")

        row = _empty_worn_row(source.saved, label)
        row.update(
            {
                "status": "dry_run" if dry_run else "pending",
                "extraction_file": copied_extraction_name,
                "source_flat_lay_file": copied_reference_name,
                "prompt_file": prompt_name,
            }
        )
        if dry_run:
            rows.append(row)
            print(f"[{source.saved.case_id}/{label}] preparado — sin llamada OpenAI")
            continue

        image_started = time.monotonic()
        provider_call_attempts += 1
        try:
            image_bytes, usage = _request_worn_image(
                client,
                variant=variant,
                prompt=prompt,
                reference_path=source.flat_lay_path,
            )
            (out_dir / image_name).write_bytes(image_bytes)
            usage_cost = _usage_cost_estimate(usage)
            row.update(
                {
                    "status": "completed",
                    "image_file": image_name,
                    "usage_text_input_tokens": usage["text_input_tokens"]
                    if usage["text_input_tokens"] is not None
                    else "",
                    "usage_image_input_tokens": usage["image_input_tokens"]
                    if usage["image_input_tokens"] is not None
                    else "",
                    "usage_image_output_tokens": usage["image_output_tokens"]
                    if usage["image_output_tokens"] is not None
                    else "",
                    "usage_cost_estimate": usage_cost if usage_cost is not None else "",
                }
            )
            usage_note = f", usage=${usage_cost:.4f}" if usage_cost is not None else ""
            print(f"[{source.saved.case_id}/{label}] OK -> {image_name}{usage_note}")
        except Exception as exc:
            row["status"] = "image_error"
            row["error"] = str(exc)
            print(f"[{source.saved.case_id}/{label}] ERROR de imagen: {exc}")
            # El presupuesto aprobado cubre el A/B completo, pero un fallo deja
            # el resultado inconcluso. No gastamos la segunda llamada intentando
            # completar automáticamente una comparación que ya no será válida.
            break
        finally:
            row["image_seconds"] = round(time.monotonic() - image_started, 2)
            rows.append(row)

    _write_csv(out_dir / "results_blind.csv", rows, tuple(_empty_worn_row(source.saved, "X")))
    _write_json(
        out_dir / "manifest.json",
        {
            "mode": "ab_worn_view_blind",
            "dry_run": dry_run,
            "created_at_utc": datetime.now(timezone.utc).isoformat(),
            "case_id": source.saved.case_id,
            "source_dir": str(source.flat_lay_path.parent),
            "source_extraction_file": str(source.saved.source_path),
            "source_flat_lay_file": str(source.flat_lay_path),
            "source_flat_lay_dimensions": [
                source.flat_lay_width,
                source.flat_lay_height,
            ],
            "model": WORN_IMAGE_MODEL,
            "quality": WORN_IMAGE_QUALITY,
            "size": WORN_IMAGE_SIZE,
            "blind_seed": blind_seed,
            "variants": {
                WORN_TEXT_ONLY: "Generación solo desde la extracción estructurada guardada.",
                WORN_REFERENCE_EDIT: "Edición con el flat-lay guardado como referencia visual.",
            },
            "rubric_fields": WORN_RUBRIC_FIELDS,
            "planned_text_calls": 0,
            "planned_image_calls": 2,
            "planned_generate_calls": 1,
            "planned_edit_calls": 1,
            "automatic_retries": WORN_AUTOMATIC_RETRIES,
            "provider_call_attempts": provider_call_attempts,
            "cost_budget": budget,
            "cost_note": (
                "El coste de salida está verificado. Los márgenes de input son "
                "conservadores porque su tokenización exacta solo se conoce tras la respuesta; "
                "usage se guarda en el CSV cuando está disponible."
            ),
            "review_note": (
                "Puntuar results_blind.csv mirando X/Y antes de abrir variant_map.json "
                "o los prompts. No ejecutar otra llamada si una variante falla."
            ),
        },
    )
    _write_json(
        out_dir / "variant_map.json",
        {
            "warning": "NO ABRIR ANTES DE COMPLETAR LA REVISIÓN CIEGA.",
            "case_id": source.saved.case_id,
            "mapping": mapping,
        },
    )

    if dry_run:
        print(f"\nDry-run completado sin coste: {out_dir}")
    else:
        images_ok = sum(row["status"] == "completed" for row in rows)
        print(f"\nImágenes generadas OK: {images_ok}/2")
        print(f"Resultados ciegos:     {out_dir / 'results_blind.csv'}")
        print(f"Mapa X/Y:              {out_dir / 'variant_map.json'}")
    return out_dir


def _proposal_checks(proposals: list) -> dict[str, bool]:
    """Comprobaciones objetivas. Lo subjetivo lo juzga la persona leyendo el JSON."""
    generable = []
    for proposal in proposals:
        try:
            is_valid, _ = is_outfit_valid(proposal.to_extraction())
        except Exception:
            is_valid = False
        generable.append(is_valid)

    siluetas = {
        tuple(sorted(item.item_type.lower() for item in proposal.items)) for proposal in proposals
    }
    return {
        "all_generable": all(generable) and len(generable) > 0,
        "distinct_silhouettes": len(siluetas) == len(proposals),
        "all_with_footwear": all(
            any(item.category == "footwear" for item in proposal.items) for proposal in proposals
        ),
        "no_invented_brand": all(
            item.brand is None for proposal in proposals for item in proposal.items
        ),
    }


def run_proposals(
    limit: int | None,
    dry_run: bool = False,
    out_dir: Path | None = None,
) -> Path:
    """Smoke real de la vía de inspiración: ¿propone bien el modelo configurado?

    Solo texto: la calidad de una propuesta se juzga leyéndola, así que no se
    genera ni una imagen. El fallback lo decide el mismo código que usa la
    aplicación, de modo que el smoke mide la ruta real y no una simulación.
    """
    cases = PROPOSAL_TEST_SET if limit is None else PROPOSAL_TEST_SET[:limit]
    settings = get_settings()
    suffix = "proposals_dry_run" if dry_run else "proposals"
    out_dir = out_dir or _new_output_dir(suffix)
    out_dir.mkdir(parents=True, exist_ok=True)

    max_calls = len(cases) * 2
    if max_calls > PROPOSAL_MAX_TEXT_CALLS:
        raise SystemExit(
            f"El alcance pedido admite hasta {max_calls} llamadas y el techo aprobado "
            f"es {PROPOSAL_MAX_TEXT_CALLS}. Recalcula y vuelve a pedir aprobación."
        )

    budget_primary = estimate_text_cost(
        settings.openai_proposal_model,
        input_tokens=PROPOSAL_INPUT_TOKEN_BUDGET,
        output_tokens=PROPOSAL_OUTPUT_TOKEN_BUDGET,
    )
    budget_fallback = estimate_text_cost(
        settings.openai_proposal_fallback_model,
        input_tokens=PROPOSAL_INPUT_TOKEN_BUDGET,
        output_tokens=PROPOSAL_OUTPUT_TOKEN_BUDGET,
    )
    ceiling = len(cases) * (budget_primary + budget_fallback)

    mode_label = "PREPARACIÓN gratuita" if dry_run else "EJECUCIÓN REAL"
    print(f"== {mode_label}: vía de inspiración ==")
    print(f"Casos: {len(cases)} · llamadas máximas: {max_calls} · imágenes: 0")
    print(f"Modelo: {settings.openai_proposal_model}")
    print(f"Fallback: {settings.openai_proposal_fallback_model}")
    print(f"Techo estimado: ${ceiling:.6f}")
    print(f"Salida: {out_dir}")

    rows = []
    total_cost = 0.0
    total_calls = 0

    for case_id, situation in cases:
        row = {
            "case_id": case_id,
            "situation": situation,
            "status": "dry_run" if dry_run else "pending",
            "model_primary": settings.openai_proposal_model,
            "model_fallback": "",
            "text_calls": 0,
            "input_tokens": 0,
            "output_tokens": 0,
            "cost_estimate": 0.0,
            "seconds": 0.0,
            "proposal_titles": "",
            "all_generable": "",
            "distinct_silhouettes": "",
            "all_with_footwear": "",
            "no_invented_brand": "",
            "error": "",
        }

        if dry_run:
            rows.append(row)
            print(f"[{case_id}] preparado, sin llamar: “{situation}”")
            continue

        started = time.time()
        try:
            result = propose_outfits_from_situation(situation)
        except Exception as exc:
            row["status"] = "error"
            row["error"] = f"{type(exc).__name__}: {exc}"
            row["seconds"] = round(time.time() - started, 3)
            # Una llamada fallida ya puede haberse facturado: cuenta igual.
            total_calls += 1
            row["text_calls"] = 1
            rows.append(row)
            print(f"[{case_id}] ERROR {row['error']}")
            continue

        calls = 2 if result.model_fallback else 1
        total_calls += calls
        model_used = result.model_fallback or result.model_primary
        cost = estimate_text_cost(
            model_used,
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
        )
        total_cost += cost

        row.update(
            {
                "status": result.extraction.status,
                "model_fallback": result.model_fallback or "",
                "text_calls": calls,
                "input_tokens": result.input_tokens,
                "output_tokens": result.output_tokens,
                "cost_estimate": round(cost, 6),
                "seconds": round(time.time() - started, 3),
                "proposal_titles": " | ".join(
                    proposal.title for proposal in result.extraction.proposals
                ),
            }
        )
        row.update(
            {
                key: str(value)
                for key, value in _proposal_checks(list(result.extraction.proposals)).items()
            }
        )

        _write_json(
            out_dir / f"{case_id}.json",
            {
                "case_id": case_id,
                "situation": situation,
                "status": result.extraction.status,
                "model_primary": result.model_primary,
                "model_fallback": result.model_fallback,
                "input_tokens": result.input_tokens,
                "output_tokens": result.output_tokens,
                "cost_estimate": cost,
                "proposals": [proposal.model_dump() for proposal in result.extraction.proposals],
            },
        )
        rows.append(row)
        print(
            f"[{case_id}] {row['status']} · {calls} llamada(s) · "
            f"${cost:.6f} · {row['seconds']}s · {row['proposal_titles']}"
        )

        if total_calls > PROPOSAL_MAX_TEXT_CALLS:
            print(
                f"PARADA: {total_calls} llamadas superan el techo aprobado de "
                f"{PROPOSAL_MAX_TEXT_CALLS}."
            )
            break

    _write_csv(out_dir / "proposals.csv", rows, PROPOSAL_FIELDS)
    _write_json(
        out_dir / "summary.json",
        {
            "dry_run": dry_run,
            "cases": len(cases),
            "max_text_calls": max_calls,
            "estimated_ceiling": ceiling,
            "actual_text_calls": total_calls,
            "actual_cost": total_cost,
            "image_calls": 0,
            "model_primary": settings.openai_proposal_model,
            "model_fallback": settings.openai_proposal_fallback_model,
        },
    )

    print(
        f"Llamadas reales: {total_calls}/{max_calls} · "
        f"coste real: ${total_cost:.6f} de un techo de ${ceiling:.6f}"
    )
    return out_dir


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=None, help="usar solo los N primeros casos")
    parser.add_argument("--repeat", type=int, default=1, help="repetir cada descripción N veces")
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument(
        "--ab-complex",
        action="store_true",
        help="comparar lista plana vs builder v2 sobre 12 outfits complejos",
    )
    modes.add_argument(
        "--reuse-extractions",
        type=Path,
        default=None,
        metavar="SOURCE_DIR",
        help="regenerar casos desde extracciones JSON guardadas, sin coste de texto",
    )
    modes.add_argument(
        "--ab-worn-view",
        type=Path,
        default=None,
        metavar="SOURCE_DIR",
        help="comparar maniquí desde texto vs edición con un flat-lay guardado",
    )
    modes.add_argument(
        "--proposals",
        action="store_true",
        help="smoke de la vía de inspiración: solo texto, sin generar ninguna imagen",
    )
    parser.add_argument(
        "--case-ids",
        default=None,
        help="case_ids separados por comas para --reuse-extractions (p. ej. C08,C10)",
    )
    parser.add_argument(
        "--case-id",
        default=None,
        help="único case_id para --ab-worn-view (p. ej. C10)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="preparar el A/B vestido y sus costes sin llamar a OpenAI",
    )
    args = parser.parse_args()

    if args.limit is not None and args.limit < 1:
        parser.error("--limit debe ser al menos 1")
    if args.repeat < 1:
        parser.error("--repeat debe ser al menos 1")
    if args.case_ids and not args.reuse_extractions:
        parser.error("--case-ids requiere --reuse-extractions")
    if args.case_id and not args.ab_worn_view:
        parser.error("--case-id requiere --ab-worn-view")
    if args.dry_run and not (args.ab_worn_view or args.proposals):
        parser.error("--dry-run solo se admite con --ab-worn-view o --proposals")
    if args.ab_worn_view:
        if args.limit is not None:
            parser.error("--limit no se admite con --ab-worn-view")
        if args.repeat != 1:
            parser.error("--repeat no se admite con --ab-worn-view")
        if not args.case_id:
            parser.error("--ab-worn-view requiere --case-id")
        run_ab_worn_view(
            args.ab_worn_view,
            args.case_id,
            dry_run=args.dry_run,
        )
    elif args.reuse_extractions:
        if args.limit is not None:
            parser.error("--limit no se admite con --reuse-extractions")
        if args.repeat != 1:
            parser.error("--repeat no se admite con --reuse-extractions")
        if not args.case_ids:
            parser.error("--reuse-extractions requiere --case-ids")
        run_reused_extractions(
            args.reuse_extractions,
            args.case_ids.split(","),
        )
    elif args.proposals:
        if args.repeat != 1:
            parser.error("--repeat no se admite con --proposals")
        run_proposals(args.limit, dry_run=args.dry_run)
    elif args.ab_complex:
        if args.repeat != 1:
            parser.error("--repeat no se admite en el A/B; cada variante se genera una vez")
        run_ab_complex(args.limit)
    else:
        run(args.limit, args.repeat)


if __name__ == "__main__":
    main()
