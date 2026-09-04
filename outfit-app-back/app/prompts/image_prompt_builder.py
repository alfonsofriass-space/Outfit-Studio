"""Composición determinista y adaptativa del prompt de imagen.

El LLM aporta una ``visual_phrase_en`` por pieza. Este módulo decide la
geometría del board:

- 1-3 piezas: silueta vertical sencilla.
- 4+ piezas: silueta ancha, con capas superiores lado a lado, parte inferior
  centrada, calzado abajo y un rail semántico de accesorios.

El objetivo es conservar la lectura corporal del outfit sin encoger las prendas
ni dejar grandes áreas vacías. Cada zona se redacta según lo que contiene y el
rail se dimensiona por su número de accesorios: una zona descrita en plural o
más ancha que su contenido lleva al modelo a inventar piezas para llenarla.
"""

import logging
import re
import unicodedata
from typing import NamedTuple

from app.schemas import OutfitItem

logger = logging.getLogger(__name__)


class BuiltImagePrompt(NamedTuple):
    prompt: str
    # Accesorios fuera del board por el cap: se informan al cliente en la
    # respuesta (no se recortan en silencio).
    accessories_omitted: list[str]


STYLE_BLOCK = (
    "Body-readable flat-lay outfit board, e-commerce catalog style, on a plain "
    "white or very light gray background. Realistic studio product photography, "
    "uniform soft lighting, very soft shadows."
)

SIMPLE_LAYOUT_INSTRUCTION = (
    "Arrange the garments top-to-bottom in the order they are worn on a body, "
    "with proportions consistent as if all pieces belong to the same person. "
    "Keep the pieces large and use the available canvas with compact, even gaps:"
)

WIDE_LAYOUT_INSTRUCTION = (
    "Create a wide, compact body-shaped composition, not a narrow single-file "
    "column. Use roughly 90% of the usable canvas width and height, with small "
    "uniform gaps and no large empty areas; fill the canvas by scaling the listed "
    "pieces up, never by adding extra ones. Keep every main garment large enough "
    "for its cut, material and details to remain legible:"
)

NO_EXTRA_PIECES_SENTENCE = (
    "Draw only the pieces listed above: do not add any extra garment, accessory or "
    "object, and do not complete the outfit with anything that is not listed."
)

CONSTRAINTS_BLOCK = (
    f"{NO_EXTRA_PIECES_SENTENCE} "
    "Every piece complete, laid flat, front-facing, fully visible and separate; "
    "do not layer or overlap garments. No person, no body parts, no mannequin, "
    "no hangers, no text, no props, no furniture, no logos unless explicitly "
    "described."
)

EXPLICIT_STYLING_HEADER = (
    "Explicit styling relationships requested by the user; follow them exactly and "
    "let them override the default separation or zone placement only where needed:"
)

EXPLICIT_STYLING_CONSTRAINTS_BLOCK = (
    f"{NO_EXTRA_PIECES_SENTENCE} "
    "Every piece complete, laid flat, front-facing and clearly visible. Keep pieces "
    "separate and do not layer or overlap them except where an explicit styling "
    "relationship above requires it. For intentional layering, overlap only the "
    "necessary area and keep both pieces recognizable, with their defining shape, "
    "color and details legible. No person, no body parts, no mannequin, no hangers, "
    "no text, no props, no furniture, no logos unless explicitly described."
)

# Cap de atención: con demasiados accesorios el modelo descuida las prendas.
MAX_ACCESSORIES_SHOWN = 4
WIDE_LAYOUT_MIN_ITEMS = 4

# Reservar el 24% del lienzo para un solo accesorio deja una columna casi vacía
# que el modelo rellena inventando complementos. El raíl crece con su contenido.
ACCESSORY_RAIL_SHARE = {1: 16, 2: 20}
DEFAULT_ACCESSORY_RAIL_SHARE = 24

_TOKEN_RE = re.compile(r"[a-z0-9]+")

_LEGWEAR_KEYWORDS = frozenset(
    {
        "media",
        "medias",
        "panty",
        "panties",
        "leotardo",
        "leotardos",
        "calcetin",
        "calcetines",
        "tights",
        "stocking",
        "stockings",
    }
)

_OUTER_UPPER_KEYWORDS = frozenset(
    {
        "abrigo",
        "anorak",
        "blazer",
        "bomber",
        "cazadora",
        "chaqueta",
        "gabardina",
        "kimono",
        "parka",
    }
)
_MID_UPPER_KEYWORDS = frozenset({"cardigan", "chaleco", "jersey", "sudadera", "sueter"})
_BASE_UPPER_KEYWORDS = frozenset({"blusa", "camisa", "camiseta", "polo", "top"})

_HIGH_ACCESSORY_KEYWORDS = frozenset(
    {
        "bufanda",
        "collar",
        "corbata",
        "gafa",
        "gafas",
        "gorra",
        "gorro",
        "guante",
        "guantes",
        "panuelo",
        "pendiente",
        "sombrero",
    }
)
_MID_ACCESSORY_KEYWORDS = frozenset(
    {
        "bolso",
        "cinturon",
        "mochila",
        "pulsera",
        "reloj",
        "rinonera",
    }
)


def _phrase(item: OutfitItem) -> str:
    """Devuelve la frase validada y falla cerrado ante objetos construidos sin validar."""
    phrase = item.visual_phrase_en.strip()
    if not phrase:
        raise ValueError("No se puede construir un prompt sin visual_phrase_en")
    return phrase


def _tokens(text: str) -> frozenset[str]:
    normalized = "".join(
        character
        for character in unicodedata.normalize("NFD", text.lower())
        if unicodedata.category(character) != "Mn"
    )
    return frozenset(_TOKEN_RE.findall(normalized))


def _is_legwear(item: OutfitItem) -> bool:
    return not _tokens(item.item_type).isdisjoint(_LEGWEAR_KEYWORDS)


def _upper_layer_rank(item: OutfitItem) -> int:
    item_tokens = _tokens(item.item_type)
    if not item_tokens.isdisjoint(_OUTER_UPPER_KEYWORDS):
        return 0
    if not item_tokens.isdisjoint(_MID_UPPER_KEYWORDS):
        return 1
    if not item_tokens.isdisjoint(_BASE_UPPER_KEYWORDS):
        return 2
    return 1


def _accessory_body_rank(item: OutfitItem) -> int:
    item_tokens = _tokens(item.item_type)
    if not item_tokens.isdisjoint(_HIGH_ACCESSORY_KEYWORDS):
        return 0
    if not item_tokens.isdisjoint(_MID_ACCESSORY_KEYWORDS):
        return 1
    return 2


def _partition_items(items: list[OutfitItem]) -> dict[str, list[OutfitItem]]:
    zones: dict[str, list[OutfitItem]] = {
        "upper": [],
        "one_piece": [],
        "lower": [],
        "legwear": [],
        "footwear": [],
        "accessory": [],
    }
    for item in items:
        if _is_legwear(item):
            zones["legwear"].append(item)
        elif item.category == "upper":
            zones["upper"].append(item)
        elif item.category == "one_piece":
            zones["one_piece"].append(item)
        elif item.category == "lower":
            zones["lower"].append(item)
        elif item.category == "footwear":
            zones["footwear"].append(item)
        else:
            zones["accessory"].append(item)

    zones["upper"].sort(key=_upper_layer_rank)
    return zones


def _cap_and_sort_accessories(
    accessories: list[OutfitItem],
) -> tuple[list[OutfitItem], list[str]]:
    shown = accessories[:MAX_ACCESSORIES_SHOWN]
    omitted = [_phrase(item) for item in accessories[MAX_ACCESSORIES_SHOWN:]]
    shown.sort(key=_accessory_body_rank)
    if omitted:
        logger.warning(
            "Accesorios omitidos del board por el cap (%s): %s",
            MAX_ACCESSORIES_SHOWN,
            omitted,
        )
    return shown, omitted


def _joined(items: list[OutfitItem]) -> str:
    return "; ".join(_phrase(item) for item in items)


def build_styling_block(styling_notes_en: list[str] | None = None) -> list[str]:
    """Devuelve las relaciones explícitas y las restricciones compatibles con ellas."""
    notes = [note.strip() for note in (styling_notes_en or [])]
    if any(not note for note in notes):
        raise ValueError("No se puede construir un prompt con styling_notes_en vacías")
    if not notes:
        return [CONSTRAINTS_BLOCK]

    lines = [EXPLICIT_STYLING_HEADER]
    for note in notes:
        sentence = note if note.endswith((".", "!", "?")) else f"{note}."
        lines.append(f"- {sentence}")
    lines.append(EXPLICIT_STYLING_CONSTRAINTS_BLOCK)
    return lines


_ZONE_ORDER = ("upper", "one_piece", "lower", "legwear", "footwear", "accessory")


def _inventory_line(zones: dict[str, list[OutfitItem]]) -> str:
    """Enumera las piezas del board antes del layout.

    Un recuento explícito es la defensa más fuerte contra que el modelo complete
    el outfit por su cuenta cuando una zona queda holgada.
    """
    shown = [item for zone in _ZONE_ORDER for item in zones[zone]]
    determiner, noun = ("this", "piece") if len(shown) == 1 else ("these", "pieces")
    return (
        f"The board shows exactly {determiner} {len(shown)} {noun} and nothing "
        f"else: {_joined(shown)}."
    )


def _accessory_rail_line(accessories: list[OutfitItem], *, wide: bool) -> str:
    count = len(accessories)
    determiner, noun = ("this", "accessory") if count == 1 else ("these", "accessories")
    order = ", ordered top-to-bottom by where each piece is worn" if count > 1 else ""
    if wide:
        share = ACCESSORY_RAIL_SHARE.get(count, DEFAULT_ACCESSORY_RAIL_SHARE)
        head = (
            f"- Compact right accessory rail, roughly {share}% of the canvas, "
            "smaller than garments but clearly legible."
        )
    else:
        head = "- Narrow right-hand side column, at smaller but legible scale."
    return (
        f"{head} It holds exactly {determiner} {count} {noun} and nothing else"
        f"{order}: {_joined(accessories)}."
    )


def _upper_band_line(uppers: list[OutfitItem]) -> str:
    if len(uppers) == 1:
        return (
            "- Wide upper-body band at the top, holding this single top garment "
            "scaled up to span the band; there is no second top and no layer over "
            f"or beside it: {_phrase(uppers[0])}."
        )
    return (
        "- Wide upper-body band at the top: place these garments separately "
        "side-by-side, aligned at shoulder/collar height, from outermost to "
        f"innermost: {_joined(uppers)}."
    )


def _build_simple_layout(zones: dict[str, list[OutfitItem]]) -> list[str]:
    lines = [STYLE_BLOCK, _inventory_line(zones), SIMPLE_LAYOUT_INSTRUCTION]
    top_items = zones["upper"] + zones["one_piece"]
    if top_items:
        lines.append(f"- Top area: {_joined(top_items)}.")
    if zones["lower"]:
        lines.append(f"- Middle area, below the tops: {_joined(zones['lower'])}.")
    if zones["legwear"]:
        lines.append(
            "- Legwear area, below the lower garment and above footwear, never "
            f"in the accessory column: {_joined(zones['legwear'])}."
        )
    if zones["footwear"]:
        lines.append(f"- Bottom area: {_joined(zones['footwear'])}.")
    if zones["accessory"]:
        lines.append(_accessory_rail_line(zones["accessory"], wide=False))
    return lines


def _build_wide_layout(zones: dict[str, list[OutfitItem]]) -> list[str]:
    accessory_count = len(zones["accessory"])
    if accessory_count:
        rail_share = ACCESSORY_RAIL_SHARE.get(accessory_count, DEFAULT_ACCESSORY_RAIL_SHARE)
        main_zone = f"the left roughly {100 - rail_share}% of the canvas"
    else:
        main_zone = "the full canvas width"
    lines = [
        STYLE_BLOCK,
        _inventory_line(zones),
        WIDE_LAYOUT_INSTRUCTION,
        f"- Main outfit zone: use {main_zone}.",
    ]

    if zones["one_piece"]:
        lines.append(
            "- Main body anchor, large and centered across the upper and middle "
            f"body zones: {_joined(zones['one_piece'])}."
        )
        if zones["upper"]:
            layers = zones["upper"]
            placement = (
                "as a single garment beside the body anchor at shoulder height, not on top of it"
                if len(layers) == 1
                else (
                    "beside the body anchor at shoulder height, aligned "
                    "side-by-side from outermost to innermost; never place one on "
                    "top of another"
                )
            )
            lines.append(f"- Separate upper layers {placement}: {_joined(layers)}.")
    elif zones["upper"]:
        lines.append(_upper_band_line(zones["upper"]))

    if zones["lower"]:
        lines.append(
            "- Lower-body band, large and centered directly below the upper band: "
            f"{_joined(zones['lower'])}."
        )
    if zones["legwear"]:
        lines.append(
            "- Legwear band immediately below or beside the lower garment and "
            "above footwear; never place it in the accessory rail: "
            f"{_joined(zones['legwear'])}."
        )
    if zones["footwear"]:
        lines.append(
            "- Footwear centered at the bottom of the main outfit zone and "
            f"aligned beneath the legs: {_joined(zones['footwear'])}."
        )
    if zones["accessory"]:
        lines.append(_accessory_rail_line(zones["accessory"], wide=True))

    lines.append(
        "Collapse every missing body band instead of reserving blank space. "
        "Prioritize a coherent outfit silhouette, and fill the canvas by enlarging "
        "the listed pieces rather than by adding new ones."
    )
    return lines


def build_image_prompt(
    items: list[OutfitItem],
    styling_notes_en: list[str] | None = None,
) -> BuiltImagePrompt:
    """Compone el prompt final y aplica layout sencillo o ancho por complejidad."""
    zones = _partition_items(items)
    shown_accessories, omitted = _cap_and_sort_accessories(zones["accessory"])
    zones["accessory"] = shown_accessories

    if len(items) >= WIDE_LAYOUT_MIN_ITEMS:
        lines = _build_wide_layout(zones)
    else:
        lines = _build_simple_layout(zones)

    lines.extend(build_styling_block(styling_notes_en))
    return BuiltImagePrompt(prompt="\n".join(lines), accessories_omitted=omitted)
