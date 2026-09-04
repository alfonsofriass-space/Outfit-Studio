import pytest
from factories import make_item

from app.prompts.image_prompt_builder import (
    CONSTRAINTS_BLOCK,
    EXPLICIT_STYLING_CONSTRAINTS_BLOCK,
    EXPLICIT_STYLING_HEADER,
    MAX_ACCESSORIES_SHOWN,
    NO_EXTRA_PIECES_SENTENCE,
    STYLE_BLOCK,
    WIDE_LAYOUT_MIN_ITEMS,
    build_image_prompt,
)
from app.schemas import OutfitItem


def test_builder_uses_simple_vertical_layout_for_up_to_three_items():
    built = build_image_prompt(
        [
            make_item("upper", "chaqueta", "black jacket"),
            make_item("lower", "vaqueros", "wide blue jeans"),
            make_item("footwear", "zapatillas", "white sneakers"),
        ]
    )

    assert "Top area: black jacket" in built.prompt
    assert "Middle area, below the tops: wide blue jeans" in built.prompt
    assert "Bottom area: white sneakers" in built.prompt
    assert "Wide upper-body band" not in built.prompt


def test_builder_uses_wide_body_layout_from_four_items():
    assert WIDE_LAYOUT_MIN_ITEMS == 4
    built = build_image_prompt(
        [
            make_item("footwear", "botas", "black leather boots"),
            make_item("accessory", "bufanda", "plaid scarf"),
            make_item("upper", "abrigo", "camel long coat"),
            make_item("lower", "vaqueros", "dark jeans"),
        ]
    )

    upper = built.prompt.index("Wide upper-body band")
    lower = built.prompt.index("Lower-body band")
    footwear = built.prompt.index("Footwear centered at the bottom")
    accessory = built.prompt.index("Compact right accessory rail")
    assert upper < lower < footwear < accessory
    assert "left roughly 84%" in built.prompt
    assert "not a narrow single-file column" in built.prompt
    assert built.accessories_omitted == []


def test_prompt_enumerates_the_exact_pieces_of_the_board():
    built = build_image_prompt(
        [
            make_item("upper", "camiseta", "yellow t-shirt"),
            make_item("lower", "pantalón cargo", "beige cargo shorts"),
            make_item("footwear", "zapatillas", "dark trail sneakers"),
            make_item("accessory", "gorra", "red cap"),
        ]
    )

    assert (
        "The board shows exactly these 4 pieces and nothing else: yellow t-shirt; "
        "beige cargo shorts; dark trail sneakers; red cap." in built.prompt
    )
    assert NO_EXTRA_PIECES_SENTENCE in built.prompt


def test_single_upper_never_asks_for_side_by_side_garments():
    built = build_image_prompt(
        [
            make_item("upper", "camiseta", "yellow t-shirt"),
            make_item("lower", "pantalón cargo", "beige cargo shorts"),
            make_item("footwear", "zapatillas", "dark trail sneakers"),
            make_item("accessory", "gorra", "red cap"),
        ]
    )

    band = built.prompt.split("Wide upper-body band", 1)[1].split("\n", 1)[0]
    assert "there is no second top" in band
    assert "side-by-side" not in band
    assert "outermost to innermost" not in band


def test_accessory_rail_width_follows_the_number_of_accessories():
    def rail_share(count):
        accessories = [make_item("accessory", f"acc{i}", f"accessory {i}") for i in range(count)]
        built = build_image_prompt(
            [
                make_item("upper", "camisa", "white shirt"),
                make_item("lower", "pantalón", "black pants"),
                make_item("footwear", "zapatos", "black shoes"),
                *accessories,
            ]
        )
        return built.prompt

    assert "roughly 16% of the canvas" in rail_share(1)
    assert "It holds exactly this 1 accessory and nothing else" in rail_share(1)
    assert "roughly 20% of the canvas" in rail_share(2)
    assert "roughly 24% of the canvas" in rail_share(3)
    assert "It holds exactly these 3 accessories and nothing else" in rail_share(3)


def test_wide_builder_sorts_upper_layers_outer_to_inner():
    built = build_image_prompt(
        [
            make_item("upper", "camisa", "white shirt"),
            make_item("upper", "jersey", "beige sweater"),
            make_item("upper", "abrigo", "gray wool coat"),
            make_item("lower", "pantalón", "black pants"),
        ]
    )

    assert "outermost to innermost: gray wool coat; beige sweater; white shirt." in built.prompt
    assert "side-by-side" in built.prompt
    assert "never place one on top of another" not in built.prompt


def test_wide_builder_uses_one_piece_as_body_anchor():
    built = build_image_prompt(
        [
            make_item("upper", "cazadora", "blue denim jacket"),
            make_item("one_piece", "vestido", "red floral midi dress"),
            make_item("accessory", "bolso", "cream handbag"),
            make_item("footwear", "botines", "black ankle boots"),
        ]
    )

    assert "Main body anchor" in built.prompt
    assert "red floral midi dress" in built.prompt
    assert "Separate upper layers as a single garment beside the body anchor" in built.prompt
    assert "blue denim jacket" in built.prompt


def test_builder_one_piece_stays_in_simple_top_zone():
    built = build_image_prompt(
        [
            make_item("one_piece", "vestido", "long red summer dress"),
            make_item("footwear", "sandalias", "sandals"),
        ]
    )
    assert "Top area: long red summer dress" in built.prompt
    assert "Middle area" not in built.prompt


def test_legwear_never_uses_accessory_rail_or_accessory_cap():
    built = build_image_prompt(
        [
            make_item("lower", "falda", "pleated skirt"),
            make_item("accessory", "medias tupidas", "opaque tights"),
            make_item("accessory", "bolso", "small handbag"),
            make_item("footwear", "botines", "ankle boots"),
        ]
    )

    assert "Legwear band" in built.prompt
    assert "opaque tights" in built.prompt.split("Legwear band", 1)[1].split("\n", 1)[0]
    rail_line = built.prompt.split("Compact right accessory rail", 1)[1].split("\n", 1)[0]
    assert "small handbag" in rail_line
    assert "opaque tights" not in rail_line
    assert built.accessories_omitted == []


def test_wide_layout_uses_full_width_without_accessories():
    built = build_image_prompt(
        [
            make_item("upper", "chaqueta", "black jacket"),
            make_item("upper", "camiseta", "white t-shirt"),
            make_item("lower", "pantalón", "blue jeans"),
            make_item("footwear", "zapatillas", "white sneakers"),
        ]
    )

    assert "Main outfit zone: use the full canvas width" in built.prompt
    assert "accessory rail" not in built.prompt


def test_accessory_rail_orders_items_by_where_they_are_worn():
    built = build_image_prompt(
        [
            make_item("upper", "abrigo", "gray coat"),
            make_item("lower", "pantalón", "black pants"),
            make_item("accessory", "bolso", "brown bag"),
            make_item("accessory", "guantes", "leather gloves"),
            make_item("accessory", "bufanda", "red scarf"),
        ]
    )

    rail = built.prompt.split("Compact right accessory rail", 1)[1].split("\n", 1)[0]
    assert rail.index("leather gloves") < rail.index("brown bag")
    assert rail.index("red scarf") < rail.index("brown bag")


def test_builder_caps_accessories_and_reports_omitted():
    accessories = [make_item("accessory", f"acc{i}", f"accessory number {i}") for i in range(6)]
    built = build_image_prompt(accessories + [make_item("upper", "camisa", "shirt")])

    shown = sum(1 for i in range(6) if f"accessory number {i}" in built.prompt)
    assert shown == MAX_ACCESSORIES_SHOWN
    assert built.accessories_omitted == [
        "accessory number 4",
        "accessory number 5",
    ]


def test_builder_always_includes_style_and_constraints():
    built = build_image_prompt([make_item("upper", "camisa", "shirt")])
    assert built.prompt.startswith(STYLE_BLOCK)
    assert built.prompt.endswith(CONSTRAINTS_BLOCK)
    assert "worn on a body" in built.prompt


def test_builder_allows_only_explicit_styling_relationships_to_override_separation():
    notes = [
        "maroon pleated skirt intentionally worn over beige palazzo pants",
        "one mismatched ankle boot pair, one black and one brown",
    ]
    built = build_image_prompt(
        [
            make_item("lower", "pantalón palazzo", "beige palazzo pants"),
            make_item("lower", "falda plisada", "maroon pleated skirt"),
            make_item(
                "footwear",
                "par de botines desparejados",
                "one mismatched ankle boot pair, one black and one brown",
            ),
        ],
        notes,
    )

    assert EXPLICIT_STYLING_HEADER in built.prompt
    assert all(note in built.prompt for note in notes)
    assert built.prompt.endswith(EXPLICIT_STYLING_CONSTRAINTS_BLOCK)
    assert "except where an explicit styling relationship above requires it" in built.prompt
    assert CONSTRAINTS_BLOCK not in built.prompt


def test_builder_rejects_item_without_visual_phrase():
    item = OutfitItem.model_construct(
        category="upper",
        item_type="chaqueta",
        color="negra",
        certainty="high",
        visual_phrase_en="",
    )

    with pytest.raises(ValueError, match="visual_phrase_en"):
        build_image_prompt([item])
