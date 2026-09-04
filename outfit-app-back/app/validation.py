import re
import unicodedata
from enum import Enum
from typing import NamedTuple

from app.schemas import OutfitExtraction, OutfitItem

MINIMUM_INFO_MESSAGE = (
    "Necesito al menos dos piezas del outfit, o una prenda fuerte con color, "
    "material, corte o algún detalle visual."
)
STRONG_ITEM_DETAIL_MESSAGE = (
    "La prenda es válida, pero necesito al menos un color, material, corte o "
    "detalle visual antes de generar la imagen."
)

# Único mensaje de rechazo previo al LLM. El resto del contrato se comunica después
# de la extracción, donde el recuento de prendas procede del modelo y no del
# diccionario, de modo que la aplicación nunca afirma que falta una pieza que el
# usuario sí escribió con un nombre que la heurística no conoce.
NO_CLOTHING_SIGNAL_MESSAGE = (
    "No he reconocido ninguna prenda ni ningún detalle visual en tu descripción. "
    "Dime qué prendas quieres ver y añade su color, material o corte."
)

CLARIFICATION_SUGGESTION = (
    "Por ejemplo: camiseta blanca y pantalón negro, o un abrigo largo de lana."
)

# Puerta de la vía de inspiración. La entrada es una situación, no un outfit, así que
# no puede exigir prendas: hacerlo repetiría el error del diccionario cerrado, esta vez
# sobre un vocabulario todavía más abierto (ocasiones, lugares, épocas del año).
NO_SITUATION_SIGNAL_MESSAGE = (
    "No he entendido ninguna situación. Cuéntame el plan: la ocasión, dónde es y "
    "en qué época del año."
)
SITUATION_SUGGESTION = "Por ejemplo: una boda de tarde en octubre, al aire libre."

# Los nombres principales cuentan como prendas distintas incluso si pertenecen
# a la misma categoría y no hay una conjunción entre ellos (p. ej. "camiseta
# debajo de chaqueta").
CORE_ITEM_KEYWORDS = {
    "upper": frozenset(
        {
            "chaqueta",
            "cazadora",
            "camiseta",
            "camisa",
            "jersey",
            "sudadera",
            "blazer",
            "abrigo",
            "chaqueton",
            "gabardina",
            "polo",
            "chaleco",
            "top",
            "parka",
            "blusa",
            "cardigan",
            "anorak",
            "sueter",
            "kimono",
            "trench",
            "hoodie",
        }
    ),
    "lower": frozenset(
        {
            "pantalon",
            "falda",
            "short",
            "bermuda",
            "legging",
            "jogger",
            "jeans",
            "banador",
        }
    ),
    "one_piece": frozenset({"vestido", "mono", "peto", "traje", "jumpsuit", "chandal"}),
    "footwear": frozenset(
        {
            "zapatilla",
            "bota",
            "botin",
            "mocasin",
            "sandalia",
            "zapato",
            "bailarina",
            "alpargata",
            "zueco",
            "chancla",
            "escarpin",
            "tenis",
        }
    ),
    "accessory": frozenset(
        {
            "bolso",
            "mochila",
            "gafa",
            "gorra",
            "cinturon",
            "rinonera",
            "bufanda",
            "reloj",
            "panuelo",
            "collar",
            "corbata",
            "pendiente",
            "pulsera",
            "sombrero",
            "guante",
            "calcetin",
            "media",
        }
    ),
}

# Estos términos pueden nombrar una pieza por sí solos, pero cuando aparecen
# junto a un nombre principal de su categoría suelen ser un subtipo de esa misma
# prenda: "chaqueta bomber", "pantalón cargo", "zapatos de tacón".
ITEM_ALIAS_KEYWORDS = {
    # "americana" es alias y no nombre principal: en "chaqueta americana" nombra el
    # subtipo de una única prenda, igual que "bomber".
    "upper": frozenset({"bomber", "americana"}),
    "lower": frozenset({"vaquero", "tejano", "chino", "cargo", "palazzo", "culotte"}),
    "one_piece": frozenset(),
    "footwear": frozenset({"deportiva", "tacon", "sneaker", "oxford", "chelsea"}),
    "accessory": frozenset(),
}

# Las prendas one_piece siempre son fuertes. Estos tipos también lo son aunque
# el modelo o el diccionario los clasifiquen como upper.
STRONG_ITEM_KEYWORDS = frozenset({"abrigo", "chaqueton", "gabardina", "traje", "trench"})

_ITEM_SEPARATOR_RE = re.compile(r"(?:[,;.!?+/]|\b(?:y|e|mas)\b)")
_TOKEN_RE = re.compile(r"[a-z0-9]+")

_IRREGULAR_PLURALS = {
    "jersey": frozenset({"jerseis"}),
}

# Atributos que aportan información visual verificable sin recurrir al LLM.
# Los adjetivos en -o se expanden a género y número más abajo.
_GENDERED_VISUAL_KEYWORDS = frozenset(
    {
        "negro",
        "blanco",
        "rojo",
        "amarillo",
        "morado",
        "dorado",
        "plateado",
        "largo",
        "corto",
        "ancho",
        "estrecho",
        "ajustado",
        "entallado",
        "holgado",
        "recto",
        "acampanado",
        "acolchado",
        "plisado",
        "asimetrico",
        "estructurado",
        "cruzado",
        "rasgado",
        "lavado",
        "metalico",
        "vaquero",
    }
)

_VISUAL_DETAIL_KEYWORDS = frozenset(
    {
        # Colores sin flexión regular de género.
        "gris",
        "azul",
        "verde",
        "beige",
        "camel",
        "marron",
        "granate",
        "burdeos",
        "rosa",
        "lila",
        "violeta",
        "naranja",
        "crema",
        "caqui",
        "khaki",
        "multicolor",
        # Materiales y acabados.
        "lana",
        "algodon",
        "lino",
        "cuero",
        "piel",
        "denim",
        "seda",
        "saten",
        "terciopelo",
        "pana",
        "punto",
        "cachemir",
        "cashmere",
        "tweed",
        "nylon",
        "poliester",
        "licra",
        "ante",
        "charol",
        # Corte, silueta y longitud.
        "oversize",
        "oversized",
        "slim",
        "skinny",
        "cropped",
        "crop",
        "regular",
        "maxi",
        "midi",
        "mini",
        # Detalles constructivos o estampados.
        "capucha",
        "boton",
        "botonadura",
        "cremallera",
        "bolsillo",
        "bordado",
        "estampado",
        "raya",
        "cuadro",
        "lunar",
        "fleco",
        "volante",
        "encaje",
        "lentejuela",
        "abertura",
        "hombrera",
        "cuello",
        "manga",
        "floral",
        "transparente",
        "brillante",
        "mate",
        # La temporada aporta una indicación visual útil de peso y construcción.
        "invierno",
        "verano",
        "entretiempo",
    }
)


class MinimumInfoReason(str, Enum):
    MULTIPLE_ITEMS = "multiple_items"
    STRONG_ITEM_WITH_DETAIL = "strong_item_with_detail"
    # El diccionario no puede decidir con seguridad: hay señal de ropa pero no
    # reconoce lo suficiente. El contrato lo resuelve el modelo y `is_outfit_valid`.
    DEFERRED_TO_MODEL = "deferred_to_model"
    # Único rechazo local: el texto no contiene ni prenda ni atributo visual.
    NO_CLOTHING_SIGNAL = "no_clothing_signal"


class MinimumInfoResult(NamedTuple):
    is_sufficient: bool
    reason: MinimumInfoReason
    # Cuántas prendas reconoció la heurística y si todas eran accesorias. Se conservan
    # para el log: permiten ver cuánto entendió el diccionario sin volver a calcularlo.
    recognized_items: int = 0
    only_accessories: bool = False


class SituationInfoResult(NamedTuple):
    is_sufficient: bool
    message: str | None = None


class _ItemMention(NamedTuple):
    category: str
    keywords: frozenset[str]


def _normalize(text: str) -> str:
    """Devuelve texto en minúsculas y sin marcas diacríticas."""
    text = text.lower()
    return "".join(
        character
        for character in unicodedata.normalize("NFD", text)
        if unicodedata.category(character) != "Mn"
    )


def _keyword_forms(keyword: str) -> frozenset[str]:
    """Formas admitidas de una keyword sin recurrir a matching por subcadena."""
    return frozenset({keyword, f"{keyword}s", f"{keyword}es"}) | _IRREGULAR_PLURALS.get(
        keyword, frozenset()
    )


def _gendered_forms(keyword: str) -> frozenset[str]:
    if keyword.endswith("o"):
        root = keyword[:-1]
        return frozenset({f"{root}o", f"{root}a", f"{root}os", f"{root}as"})
    return _keyword_forms(keyword)


_VISUAL_DETAIL_FORMS = frozenset(
    form for keyword in _VISUAL_DETAIL_KEYWORDS for form in _keyword_forms(keyword)
) | frozenset(form for keyword in _GENDERED_VISUAL_KEYWORDS for form in _gendered_forms(keyword))


def _matches_keyword(tokens: frozenset[str], keyword: str) -> bool:
    return not tokens.isdisjoint(_keyword_forms(keyword))


def _keyword_occurrences(tokens: list[str], keyword: str) -> int:
    forms = _keyword_forms(keyword)
    return sum(token in forms for token in tokens)


def _detect_item_mentions(text: str) -> list[_ItemMention]:
    """
    Detecta nombres principales por separado y agrupa alias/subtipos.

    Así, "camiseta debajo de chaqueta" cuenta como dos piezas, mientras que
    "chaqueta bomber" y "pantalón cargo" cuentan como una sola.
    """
    normalized = _normalize(text)
    clauses = (clause for clause in _ITEM_SEPARATOR_RE.split(normalized) if clause.strip())
    mentions: list[_ItemMention] = []

    for clause in clauses:
        token_list = _TOKEN_RE.findall(clause)
        tokens = frozenset(token_list)

        for category, core_keywords in CORE_ITEM_KEYWORDS.items():
            core_matches: list[str] = []
            for keyword in core_keywords:
                core_matches.extend([keyword] * _keyword_occurrences(token_list, keyword))

            if core_matches:
                mentions.extend(
                    _ItemMention(category=category, keywords=frozenset({keyword}))
                    for keyword in core_matches
                )
                continue

            alias_matches = frozenset(
                keyword
                for keyword in ITEM_ALIAS_KEYWORDS[category]
                if _matches_keyword(tokens, keyword)
            )
            if alias_matches:
                mentions.append(_ItemMention(category=category, keywords=alias_matches))

    return mentions


def _contains_visual_detail(text: str) -> bool:
    tokens = frozenset(_TOKEN_RE.findall(_normalize(text)))
    return not tokens.isdisjoint(_VISUAL_DETAIL_FORMS)


def _is_strong_mention(mention: _ItemMention) -> bool:
    return mention.category == "one_piece" or not mention.keywords.isdisjoint(STRONG_ITEM_KEYWORDS)


def _is_strong_item(category: str, item_type: str) -> bool:
    if category == "one_piece":
        return True

    tokens = frozenset(_TOKEN_RE.findall(_normalize(item_type)))
    return any(_matches_keyword(tokens, keyword) for keyword in STRONG_ITEM_KEYWORDS)


def _item_has_visual_detail(item: OutfitItem) -> bool:
    structured_values = (item.color, item.material, item.fit)
    if any(value and value.strip() for value in structured_values):
        return True
    if any(detail.strip() for detail in item.details):
        return True

    # Degradación defensiva: si el modelo dejó el atributo dentro de item_type
    # ("abrigo de lana") sigue siendo información explícita y utilizable.
    return _contains_visual_detail(item.item_type)


def evaluate_minimum_info(text: str) -> MinimumInfoResult:
    """Decide si merece la pena que el modelo lea el texto. No aplica el contrato.

    El diccionario es cerrado y siempre tendrá huecos: cualquier rechazo basado en
    contar prendas puede equivocarse porque una palabra desconocida podría ser
    justamente la pieza que falta. Por eso la puerta local solo bloquea el caso en el
    que ninguna palabra desconocida podría cambiar el veredicto: un texto sin ninguna
    señal de ropa. El contrato mínimo lo aplica `is_outfit_valid` sobre la extracción
    del modelo, que sí entiende español.
    """
    mentions = _detect_item_mentions(text)
    recognized = len(mentions)
    only_accessories = recognized > 0 and all(
        mention.category == "accessory" for mention in mentions
    )
    has_visual_detail = _contains_visual_detail(text)

    if recognized == 0 and not has_visual_detail:
        return MinimumInfoResult(False, MinimumInfoReason.NO_CLOTHING_SIGNAL)

    if recognized >= 2 and not only_accessories:
        reason = MinimumInfoReason.MULTIPLE_ITEMS
    elif has_visual_detail and any(_is_strong_mention(mention) for mention in mentions):
        reason = MinimumInfoReason.STRONG_ITEM_WITH_DETAIL
    else:
        reason = MinimumInfoReason.DEFERRED_TO_MODEL

    return MinimumInfoResult(True, reason, recognized, only_accessories)


def has_minimum_info(text: str) -> bool:
    """Compatibilidad para consumidores que solo necesitan la decisión booleana."""
    return evaluate_minimum_info(text).is_sufficient


def clarification_for(result: MinimumInfoResult) -> tuple[str, str]:
    """Mensaje y sugerencia del único rechazo que la heurística puede afirmar.

    Falla cerrado ante un resultado suficiente: no hay aclaración que pedir y
    devolver una por descuido comunicaría al usuario un problema inexistente.
    """
    if result.is_sufficient:
        raise ValueError("No hay aclaración que pedir para una descripción suficiente")
    return NO_CLOTHING_SIGNAL_MESSAGE, CLARIFICATION_SUGGESTION


def needs_fallback(extraction: OutfitExtraction) -> bool:
    """
    Decide si hay que reintentar con el modelo de fallback (mini), en base a reglas
    objetivas sobre la salida de nano. Esta decisión la toma el código, no el modelo.
    """
    # Una descripción que necesita aclaración no se arregla con un modelo mejor:
    # mini recibiría el mismo texto vago. No gastar la segunda llamada.
    if extraction.status == "needs_clarification":
        return False

    low_certainty_items = sum(1 for item in extraction.items if item.certainty == "low")
    if low_certainty_items >= 2:
        return True

    return False


def evaluate_situation_input(text: str) -> SituationInfoResult:
    """Decide si una situación merece que el modelo la lea.

    A diferencia de `evaluate_minimum_info`, aquí no hay diccionario ni contrato que
    aplicar: cualquier palabra puede ser parte de una situación legítima. El único
    caso en el que ninguna palabra desconocida podría cambiar el veredicto es un texto
    sin una sola letra, así que ese es el único rechazo local. Todo lo demás lo decide
    el modelo, que devuelve `needs_clarification` cuando no hay situación que vestir.
    """
    if not any(character.isalpha() for character in text):
        return SituationInfoResult(False, NO_SITUATION_SIGNAL_MESSAGE)

    return SituationInfoResult(True)


def is_outfit_valid(extraction: OutfitExtraction) -> tuple[bool, str]:
    """Aplica tras el LLM el mismo contrato mínimo que la heurística previa."""
    if extraction.status == "needs_clarification":
        return False, "La descripción no aporta suficiente información de prendas."

    items = extraction.items
    if len(items) >= 2 and any(item.category != "accessory" for item in items):
        return True, ""

    strong_items = [item for item in items if _is_strong_item(item.category, item.item_type)]
    if any(_item_has_visual_detail(item) for item in strong_items):
        return True, ""
    if strong_items:
        return False, STRONG_ITEM_DETAIL_MESSAGE

    return False, MINIMUM_INFO_MESSAGE
