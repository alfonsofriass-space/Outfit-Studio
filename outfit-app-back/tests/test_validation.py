import pytest
from factories import make_item
from pydantic import ValidationError

from app.schemas import OutfitExtraction
from app.validation import (
    CLARIFICATION_SUGGESTION,
    NO_CLOTHING_SIGNAL_MESSAGE,
    STRONG_ITEM_DETAIL_MESSAGE,
    MinimumInfoReason,
    clarification_for,
    evaluate_minimum_info,
    has_minimum_info,
    is_outfit_valid,
    needs_fallback,
)


def get_base_extraction():
    return OutfitExtraction(
        status="ok",
        outfit_summary="test",
        items=[],
    )


def test_extraction_rejects_model_requested_fallback():
    with pytest.raises(ValidationError, match="status"):
        OutfitExtraction(
            status="needs_fallback",
            outfit_summary="salida no terminal",
            items=[],
        )


def test_extraction_normalizes_explicit_styling_notes():
    extraction = OutfitExtraction(
        status="ok",
        outfit_summary="look con relación explícita",
        items=[],
        styling_notes_en=["  red scarf tied around the waist as a belt  "],
    )

    assert extraction.styling_notes_en == ["red scarf tied around the waist as a belt"]

    with pytest.raises(ValidationError, match="styling_notes_en"):
        OutfitExtraction(
            status="ok",
            outfit_summary="nota vacía",
            items=[],
            styling_notes_en=["   "],
        )


def test_insufficient_description():
    extraction = get_base_extraction()
    extraction.status = "needs_clarification"

    is_valid, msg = is_outfit_valid(extraction)
    assert not is_valid
    assert "no aporta suficiente" in msg


def test_valid_simple_description():
    extraction = get_base_extraction()
    extraction.items = [
        make_item("upper", "camisa"),
        make_item("lower", "pantalón"),
    ]

    is_valid, msg = is_outfit_valid(extraction)
    assert is_valid
    assert msg == ""


def test_complex_description():
    extraction = get_base_extraction()
    extraction.items = [
        make_item("upper", "camiseta blanca"),
        make_item("upper", "chaqueta de cuero"),
        make_item("lower", "vaqueros"),
        make_item("accessory", "reloj"),
    ]

    is_valid, msg = is_outfit_valid(extraction)
    assert is_valid
    assert msg == ""


def test_category_validation_one_piece():
    extraction = get_base_extraction()
    # Solo un item, pero es un vestido (one_piece), debería ser válido
    extraction.items = [make_item("one_piece", "vestido rojo")]

    is_valid, msg = is_outfit_valid(extraction)
    assert is_valid


def test_strong_item_validation_abrigo():
    extraction = get_base_extraction()
    # Solo un item, pero es un abrigo (fuerte), debería ser válido
    extraction.items = [make_item("upper", "abrigo de lana")]

    is_valid, msg = is_outfit_valid(extraction)
    assert is_valid


@pytest.mark.parametrize(
    ("category", "item_type"),
    [
        ("upper", "abrigo"),
        ("upper", "gabardina"),
        ("one_piece", "vestido"),
        ("one_piece", "traje"),
    ],
)
def test_strong_item_without_visual_detail_is_invalid(category, item_type):
    extraction = get_base_extraction()
    extraction.items = [make_item(category, item_type)]

    is_valid, msg = is_outfit_valid(extraction)

    assert not is_valid
    assert msg == STRONG_ITEM_DETAIL_MESSAGE


def test_strong_item_accepts_structured_visual_detail():
    extraction = get_base_extraction()
    extraction.items = [
        make_item(
            "upper",
            "abrigo",
            color="negro",
            phrase="black coat",
        )
    ]

    is_valid, msg = is_outfit_valid(extraction)

    assert is_valid
    assert msg == ""


def test_two_accessories_are_invalid_post_llm():
    extraction = get_base_extraction()
    extraction.items = [
        make_item("accessory", "reloj"),
        make_item("accessory", "collar"),
    ]

    is_valid, _ = is_outfit_valid(extraction)

    assert not is_valid


def test_invalid_too_few_items():
    extraction = get_base_extraction()
    # Solo un item débil (zapatillas)
    extraction.items = [make_item("footwear", "zapatillas")]

    is_valid, msg = is_outfit_valid(extraction)
    assert not is_valid
    assert "al menos dos piezas" in msg


# ==========================================
# Tests de has_minimum_info() (heurística previa, pre-LLM)
# ==========================================


def test_has_minimum_info_poor_description():
    assert not has_minimum_info("un look bonito")
    assert not has_minimum_info("algo elegante")
    assert not has_minimum_info("un outfit oscuro")


def test_two_pieces_without_visual_attributes_are_sufficient():
    result = evaluate_minimum_info("camiseta y pantalón")

    assert result.is_sufficient
    assert result.reason == MinimumInfoReason.MULTIPLE_ITEMS


@pytest.mark.parametrize(
    "description",
    [
        "camiseta blanca y chaqueta negra",
        "camisa blanca, blazer azul",
        "pantalones negros y vaqueros azules",
    ],
)
def test_has_minimum_info_two_items_in_same_category(description):
    assert has_minimum_info(description)


@pytest.mark.parametrize(
    "description",
    [
        "camiseta debajo de chaqueta",
        "camisa sobre camiseta",
    ],
)
def test_has_minimum_info_two_same_category_items_without_separator(description):
    assert has_minimum_info(description)


@pytest.mark.parametrize(
    "description",
    [
        "blusa blanca y pantalón negro",
        "cárdigan beige y falda negra",
        "anorak verde con vaqueros",
        "pantalón negro y alpargatas",
    ],
)
def test_has_minimum_info_expanded_clothing_vocabulary(description):
    assert has_minimum_info(description)


def test_has_minimum_info_synonyms():
    # Casos que fallaban antes de ampliar el diccionario (destapados por el experimento)
    assert has_minimum_info("camisa vaquera y chinos beige")
    assert has_minimum_info("cazadora y deportivas")
    assert has_minimum_info("polo y bermuda")


@pytest.mark.parametrize(
    "description",
    [
        # Vocabulario cotidiano que la heurística rechazaba pese a describir un
        # outfit completo. Cada caso se comprobó ejecutando la heurística real.
        "jeans azules y camiseta blanca",
        "una americana azul marino y camisa blanca",
        "chándal gris y zapatillas",
        "bañador estampado y chanclas",
        "tenis blancos y jogger negro",
        "hoodie negro y jeans",
        "un trench beige",
    ],
)
def test_has_minimum_info_everyday_vocabulary(description):
    assert has_minimum_info(description)


@pytest.mark.parametrize(
    "description",
    [
        # "americana" es subtipo, no una segunda prenda: en "chaqueta americana" el
        # recuento debe seguir siendo 1, igual que con "cargo" o "bomber".
        "chaqueta americana negra",
        "americana",
    ],
)
def test_americana_does_not_count_as_a_second_item(description):
    result = evaluate_minimum_info(description)

    assert result.recognized_items == 1
    assert result.reason != MinimumInfoReason.MULTIPLE_ITEMS


def test_two_accessories_are_deferred_and_rejected_after_extraction():
    # Dos accesorios NO bastan, pero el diccionario no puede afirmarlo: una palabra
    # desconocida en el mismo texto podría ser la prenda que falta. Decide el modelo.
    result = evaluate_minimum_info("corbata y reloj")

    assert result.is_sufficient
    assert result.reason == MinimumInfoReason.DEFERRED_TO_MODEL
    assert result.only_accessories

    extraction = get_base_extraction()
    extraction.items = [make_item("accessory", "corbata"), make_item("accessory", "reloj")]
    is_valid, _ = is_outfit_valid(extraction)
    assert not is_valid


def test_has_minimum_info_one_strong_item():
    assert has_minimum_info("un vestido rojo")


@pytest.mark.parametrize(
    "description",
    [
        "un abrigo de lana",
        "gabardina beige",
        "abrigos largos",
        "chaquetones de invierno",
    ],
)
def test_has_minimum_info_strong_upper_item(description):
    assert has_minimum_info(description)


@pytest.mark.parametrize(
    ("description", "category"),
    [
        ("abrigo", "upper"),
        ("una gabardina", "upper"),
        ("vestido", "one_piece"),
        ("traje", "one_piece"),
    ],
)
def test_bare_strong_item_is_deferred_and_rejected_after_extraction(description, category):
    # Una prenda fuerte sin atributos sigue sin bastar, pero el rechazo lo emite
    # `is_outfit_valid` sobre lo que el modelo extrajo, no el diccionario.
    result = evaluate_minimum_info(description)

    assert result.is_sufficient
    assert result.reason == MinimumInfoReason.DEFERRED_TO_MODEL

    extraction = get_base_extraction()
    extraction.items = [make_item(category, description.split()[-1])]
    is_valid, msg = is_outfit_valid(extraction)
    assert not is_valid
    assert msg == STRONG_ITEM_DETAIL_MESSAGE


@pytest.mark.parametrize(
    "description",
    [
        "abrigo negro",
        "gabardina beige oversize",
        "vestido de seda",
    ],
)
def test_strong_item_with_visual_detail_is_sufficient(description):
    result = evaluate_minimum_info(description)

    assert result.is_sufficient
    assert result.reason == MinimumInfoReason.STRONG_ITEM_WITH_DETAIL


@pytest.mark.parametrize(
    ("description", "category", "item_type"),
    [
        ("zapatillas blancas", "footwear", "zapatillas"),
        ("camiseta roja de seda oversize", "upper", "camiseta"),
    ],
)
def test_single_weak_item_is_deferred_and_rejected_after_extraction(
    description, category, item_type
):
    # El producto genera outfits, no fichas de producto. La regla se mantiene; lo que
    # cambia es quién la aplica, porque el diccionario no sabe si hay otra prenda.
    result = evaluate_minimum_info(description)

    assert result.is_sufficient
    assert result.reason == MinimumInfoReason.DEFERRED_TO_MODEL

    extraction = get_base_extraction()
    extraction.items = [make_item(category, item_type, color="rojo")]
    is_valid, _ = is_outfit_valid(extraction)
    assert not is_valid


def test_has_minimum_info_plurals_and_accents():
    assert has_minimum_info("chaquetas negras y gafas de sol")


@pytest.mark.parametrize(
    "description",
    [
        "un look monocromático",
        "una topografía moderna",
        "estilo monocromático con composición topográfica",
    ],
)
def test_has_minimum_info_does_not_match_keyword_substrings(description):
    assert not has_minimum_info(description)


@pytest.mark.parametrize(
    "description",
    [
        "pantalón cargo verde",
        "chaqueta bomber negra",
    ],
)
def test_alias_does_not_double_count_the_same_item(description):
    # El subtipo no añade una segunda prenda al recuento, aunque el texto ya no se
    # rechace localmente: `recognized_items` alimenta la decisión de MULTIPLE_ITEMS.
    result = evaluate_minimum_info(description)

    assert result.recognized_items == 1
    assert result.reason != MinimumInfoReason.MULTIPLE_ITEMS


# ==========================================
# Tests del mensaje de aclaración previo al LLM
# ==========================================


def test_clarification_reports_the_absence_of_any_clothing_signal():
    result = evaluate_minimum_info("algo elegante para una boda")

    assert not result.is_sufficient
    assert result.reason == MinimumInfoReason.NO_CLOTHING_SIGNAL
    message, suggestion = clarification_for(result)

    assert message == NO_CLOTHING_SIGNAL_MESSAGE
    assert suggestion == CLARIFICATION_SUGGESTION


def test_unknown_garment_name_reaches_the_model_instead_of_being_rejected():
    # El caso que motivó invertir la puerta: dos prendas reales, una con un nombre
    # que el diccionario no conoce. Antes se rechazaba en local afirmando que
    # faltaban piezas; ahora lo decide el modelo, que sí entiende español.
    result = evaluate_minimum_info("camiseta blanca y frusleras moradas")

    assert result.recognized_items == 1
    assert result.is_sufficient


def test_unknown_garment_without_any_attribute_still_reaches_the_model():
    result = evaluate_minimum_info("una sahariana de lino y unas babuchas de ante")

    assert result.recognized_items == 0
    assert result.is_sufficient
    assert result.reason == MinimumInfoReason.DEFERRED_TO_MODEL


def test_clarification_fails_closed_on_a_sufficient_description():
    result = evaluate_minimum_info("camiseta blanca y pantalón negro")

    assert result.is_sufficient
    with pytest.raises(ValueError, match="suficiente"):
        clarification_for(result)


# ==========================================
# Tests de needs_fallback() (decisión post-LLM en código)
# ==========================================


def test_needs_fallback_low_certainty_items():
    extraction = get_base_extraction()
    extraction.items = [
        make_item(
            "upper",
            "chaqueta",
            certainty="low",
            phrase="jacket",
        ),
        make_item(
            "lower",
            "vaqueros",
            certainty="low",
            phrase="jeans",
        ),
    ]
    assert needs_fallback(extraction)


@pytest.mark.parametrize("visual_phrase", ["", "   "])
def test_item_rejects_blank_visual_phrase(visual_phrase):
    with pytest.raises(ValidationError, match="visual_phrase_en"):
        make_item("upper", "chaqueta", phrase=visual_phrase)


def test_item_normalizes_visual_phrase_whitespace():
    item = make_item("upper", "chaqueta", phrase="  black jacket  ")

    assert item.visual_phrase_en == "black jacket"


def test_needs_fallback_not_needed():
    extraction = get_base_extraction()
    extraction.items = [
        make_item("upper", "chaqueta", phrase="black jacket"),
        make_item("lower", "vaqueros", phrase="wide-leg jeans"),
    ]
    assert not needs_fallback(extraction)


def test_needs_fallback_short_circuits_on_clarification():
    # Si nano ya dijo needs_clarification, mini recibiría el mismo texto vago:
    # NO debe dispararse el fallback (antes se disparaba y costaba una llamada extra).
    extraction = get_base_extraction()
    extraction.status = "needs_clarification"
    extraction.items = []
    assert not needs_fallback(extraction)
