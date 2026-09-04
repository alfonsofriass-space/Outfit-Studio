from app.schemas import OutfitExtraction


def build_worn_prompt(extraction: OutfitExtraction, *, use_reference: bool) -> str:
    """Compone el prompt vestido validado; solo varía la fuente visual del A/B."""
    grouped = {
        category: [item.visual_phrase_en for item in extraction.items if item.category == category]
        for category in ("upper", "one_piece", "lower", "footwear", "accessory")
    }
    source_instruction = (
        "Use the supplied flat-lay board as the visual source of truth for each piece. "
        "Transform the outfit into a worn view; do not reproduce the board layout."
        if use_reference
        else "Use only the structured garment specification below as the visual source."
    )
    lines = [
        "Create a realistic full-body studio catalog image of this exact outfit worn by "
        "one neutral adult fashion mannequin.",
        source_instruction,
        "The mannequin is featureless and faceless, with balanced non-exaggerated "
        "proportions, standing front-facing in a relaxed neutral pose on a plain white "
        "or very light gray background.",
        "Dress the mannequin with every specified piece exactly once:",
    ]
    if grouped["upper"]:
        lines.append(
            "- Upper-body layers, listed outermost to innermost: "
            + "; ".join(grouped["upper"])
            + "."
        )
    if grouped["one_piece"]:
        lines.append("- One-piece garments: " + "; ".join(grouped["one_piece"]) + ".")
    if grouped["lower"]:
        lines.append("- Lower-body garments: " + "; ".join(grouped["lower"]) + ".")
    if grouped["footwear"]:
        lines.append("- Footwear: " + "; ".join(grouped["footwear"]) + ".")
    if grouped["accessory"]:
        lines.append(
            "- Accessories, worn or held in their conventional position: "
            + "; ".join(grouped["accessory"])
            + "."
        )
    if extraction.styling_notes_en:
        lines.append(
            "- Explicit styling relationships that must be preserved: "
            + "; ".join(extraction.styling_notes_en)
            + "."
        )
    lines.extend(
        [
            "Preserve the specified colors, materials, cuts, patterns and visible details. "
            "Do not add, remove, duplicate, substitute or redesign any piece.",
            "Show realistic fit, drape, folds and only the natural overlap required when "
            "clothes are worn. Keep every piece identifiable and its defining details visible.",
            "Keep the entire mannequin and all footwear inside the frame. No separate garments, "
            "no flat-lay board, no extra people, no body parts outside the mannequin, no props, "
            "no labels and no text or logos unless explicitly specified.",
        ]
    )
    return "\n".join(lines)


def build_worn_view_prompt(extraction: OutfitExtraction) -> str:
    """Ruta de producto: siempre usa el flat-lay como referencia visual."""
    return build_worn_prompt(extraction, use_reference=True)
