import base64
import csv
import json
import struct
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from factories import make_item

from app.pricing import estimate_text_cost
from app.schemas import (
    ImageDetails,
    OutfitExtraction,
    OutfitItem,
    ProposalSetExtraction,
    ProposedOutfit,
)
from app.services.openai_proposals import ProposalCallResult
from experiments import run_experiment


def _complex_extraction() -> OutfitExtraction:
    return OutfitExtraction(
        status="ok",
        outfit_summary="outfit complejo de prueba",
        items=[
            make_item("upper", "abrigo", "long camel coat"),
            make_item("upper", "jersey", "cream turtleneck sweater"),
            make_item("lower", "vaqueros", "dark jeans"),
            make_item("accessory", "bufanda", "checked scarf"),
            make_item("footwear", "botas", "leather boots"),
        ],
        styling_notes_en=["checked scarf tied around the neck"],
    )


def _write_worn_source(source_dir):
    extraction = _complex_extraction()
    extraction_path = source_dir / "002_C10_extraction.json"
    extraction_path.write_text(
        json.dumps(
            {
                "case_id": "C10",
                "description": "outfit complejo guardado para vestir",
                "expected_items": len(extraction.items),
                "extraction": extraction.model_dump(mode="json"),
            }
        ),
        encoding="utf-8",
    )
    flat_lay_path = source_dir / "002_C10_wide.png"
    flat_lay_path.write_bytes(
        b"\x89PNG\r\n\x1a\n" + struct.pack(">I", 13) + b"IHDR" + struct.pack(">II", 1024, 1024)
    )
    return extraction


def _image_response(*, image_input_tokens: int):
    return SimpleNamespace(
        data=[SimpleNamespace(b64_json=base64.b64encode(b"fake-png").decode())],
        usage=SimpleNamespace(
            input_tokens_details=SimpleNamespace(
                text_tokens=350,
                image_tokens=image_input_tokens,
            ),
            output_tokens=180,
        ),
    )


def test_ab_corpus_has_twelve_sufficient_complex_cases():
    assert len(run_experiment.AB_COMPLEX_TEST_SET) == 12

    for case in run_experiment.AB_COMPLEX_TEST_SET:
        assert 5 <= case.expected_items <= 8
        assert run_experiment.evaluate_minimum_info(case.description).is_sufficient


def test_flat_baseline_keeps_content_but_has_no_body_zones():
    extraction = _complex_extraction()

    prompt = run_experiment.build_flat_baseline_prompt(
        extraction.items,
        extraction.styling_notes_en,
    )

    assert "long camel coat" in prompt
    assert "cream turtleneck sweater" in prompt
    assert "checked scarf" in prompt
    assert "Top area" not in prompt
    assert "Middle area" not in prompt
    assert "Bottom area" not in prompt
    assert run_experiment.STYLE_BLOCK in prompt
    assert "checked scarf tied around the neck" in prompt
    assert "except where an explicit styling relationship above requires it" in prompt


def test_ab_calls_text_once_and_image_twice_and_persists_blind_artifacts(
    monkeypatch,
    tmp_path,
):
    generated_dir = tmp_path / "generated"
    generated_dir.mkdir()
    image_prompts = []

    def fake_extract(_description):
        return _complex_extraction(), "test-nano", None

    def fake_generate(prompt):
        image_prompts.append(prompt)
        source = generated_dir / f"image_{len(image_prompts)}.png"
        source.write_bytes(b"fake-png")
        return ImageDetails(
            model="test-image",
            quality="low",
            size="1024x1024",
            url_or_base64=f"/images/{source.name}",
        )

    monkeypatch.setattr(run_experiment, "extract_outfit_from_text", fake_extract)
    monkeypatch.setattr(run_experiment, "generate_outfit_image", fake_generate)
    monkeypatch.setattr(run_experiment, "GENERATED_DIR", generated_dir)

    out_dir = tmp_path / "ab-output"
    result_dir = run_experiment.run_ab_complex(limit=1, out_dir=out_dir)

    assert result_dir == out_dir
    assert len(image_prompts) == 2
    assert any("Wide upper-body band" in prompt for prompt in image_prompts)
    assert any("Wide upper-body band" not in prompt for prompt in image_prompts)
    assert all("checked scarf tied around the neck" in prompt for prompt in image_prompts)

    with (out_dir / "results_blind.csv").open(encoding="utf-8", newline="") as file:
        rows = list(csv.DictReader(file))
    assert len(rows) == 2
    assert {row["blind_label"] for row in rows} == {"X", "Y"}
    assert all(row["extraction_file"] for row in rows)
    assert all(row["prompt_file"] for row in rows)
    assert all(row["image_file"] for row in rows)
    assert all(row["score_body_order"] == "" for row in rows)

    mapping = json.loads((out_dir / "variant_map.json").read_text(encoding="utf-8"))
    assert set(mapping["cases"]["C01"].values()) == {
        run_experiment.FLAT_BASELINE,
        run_experiment.ZONED_BUILDER,
    }
    extraction = json.loads(next(out_dir.glob("*_extraction.json")).read_text(encoding="utf-8"))
    assert extraction["extraction"]["items"][0]["visual_phrase_en"] == "long camel coat"

    prompt_files = list(out_dir.glob("*_prompt.txt"))
    image_files = list(out_dir.glob("*.png"))
    assert len(prompt_files) == 2
    assert len(image_files) == 2


def test_reused_extractions_skip_text_and_persist_focused_artifacts(
    monkeypatch,
    tmp_path,
):
    source_dir = tmp_path / "source-run"
    source_dir.mkdir()
    extraction = _complex_extraction()
    legacy_extraction = extraction.model_dump(mode="json")
    legacy_extraction.update(
        {
            "certainty": "high",
            "original_description": "caso complejo guardado",
            "style_tags": ["histórico"],
            "color_palette": [],
            "missing_fields": [],
        }
    )
    legacy_extraction["items"][0]["source"] = "explicit"
    (source_dir / "001_C08_extraction.json").write_text(
        json.dumps(
            {
                "case_id": "C08",
                "description": "caso complejo guardado",
                "expected_items": 5,
                "extraction": legacy_extraction,
            }
        ),
        encoding="utf-8",
    )

    generated_dir = tmp_path / "generated"
    generated_dir.mkdir()
    image_prompts = []

    def fail_if_text_is_called(_description):
        raise AssertionError("No debe volver a llamarse al modelo de texto")

    def fake_generate(prompt):
        image_prompts.append(prompt)
        source = generated_dir / "focused.png"
        source.write_bytes(b"fake-png")
        return ImageDetails(
            model="test-image",
            quality="low",
            size="1024x1024",
            url_or_base64=f"/images/{source.name}",
        )

    monkeypatch.setattr(
        run_experiment,
        "extract_outfit_from_text",
        fail_if_text_is_called,
    )
    monkeypatch.setattr(run_experiment, "generate_outfit_image", fake_generate)
    monkeypatch.setattr(run_experiment, "GENERATED_DIR", generated_dir)

    out_dir = tmp_path / "focused-output"
    result_dir = run_experiment.run_reused_extractions(
        source_dir,
        ["c08"],
        out_dir=out_dir,
    )

    assert result_dir == out_dir
    assert len(image_prompts) == 1
    assert "Wide upper-body band" in image_prompts[0]
    assert "checked scarf tied around the neck" in image_prompts[0]
    assert len(list(out_dir.glob("*_prompt.txt"))) == 1
    assert len(list(out_dir.glob("*.png"))) == 1

    with (out_dir / "results.csv").open(encoding="utf-8", newline="") as file:
        rows = list(csv.DictReader(file))
    assert rows[0]["case_id"] == "C08"
    assert rows[0]["text_cost_estimate"] == "0.0"
    assert rows[0]["image_file"] == "001_C08_wide.png"
    assert rows[0]["score_canvas_use"] == ""

    manifest = json.loads((out_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["case_ids"] == ["C08"]
    assert manifest["planned_text_cost_estimate"] == 0.0
    assert manifest["planned_images"] == 1

    copied = json.loads((out_dir / "001_C08_extraction.json").read_text(encoding="utf-8"))
    assert copied["reused_from"].endswith("001_C08_extraction.json")


def test_worn_prompt_preserves_items_and_only_changes_source_instruction():
    extraction = _complex_extraction()

    text_prompt = run_experiment.build_worn_prompt(extraction, use_reference=False)
    reference_prompt = run_experiment.build_worn_prompt(extraction, use_reference=True)

    for item in extraction.items:
        assert item.visual_phrase_en in text_prompt
        assert item.visual_phrase_en in reference_prompt
    assert "neutral adult fashion mannequin" in text_prompt
    assert "natural overlap required when clothes are worn" in text_prompt
    assert "Do not add, remove, duplicate, substitute or redesign" in text_prompt
    assert "supplied flat-lay board" not in text_prompt
    assert "supplied flat-lay board" in reference_prompt
    assert "checked scarf tied around the neck" in reference_prompt


def test_worn_dry_run_prepares_artifacts_without_openai(monkeypatch, tmp_path):
    source_dir = tmp_path / "source-run"
    source_dir.mkdir()
    _write_worn_source(source_dir)

    def fail_if_client_is_created():
        raise AssertionError("El dry-run no debe crear un cliente OpenAI")

    monkeypatch.setattr(run_experiment, "_get_worn_image_client", fail_if_client_is_created)

    out_dir = tmp_path / "worn-dry-run"
    result_dir = run_experiment.run_ab_worn_view(
        source_dir,
        "c10",
        dry_run=True,
        out_dir=out_dir,
    )

    assert result_dir == out_dir
    assert len(list(out_dir.glob("*_prompt.txt"))) == 2
    assert len(list(out_dir.glob("*_flat_lay.png"))) == 1
    assert not list(out_dir.glob("*_X.png"))
    assert not list(out_dir.glob("*_Y.png"))

    manifest = json.loads((out_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["dry_run"] is True
    assert manifest["planned_text_calls"] == 0
    assert manifest["planned_generate_calls"] == 1
    assert manifest["planned_edit_calls"] == 1
    assert manifest["automatic_retries"] == 0
    assert manifest["provider_call_attempts"] == 0
    assert manifest["cost_budget"]["output_cost_estimate"] == 0.01
    assert manifest["cost_budget"]["approval_budget_with_contingency"] == 0.11

    with (out_dir / "results_blind.csv").open(encoding="utf-8", newline="") as file:
        rows = list(csv.DictReader(file))
    assert len(rows) == 2
    assert {row["status"] for row in rows} == {"dry_run"}


def test_worn_ab_makes_one_generate_and_one_edit_without_retry_options(
    monkeypatch,
    tmp_path,
):
    source_dir = tmp_path / "source-run"
    source_dir.mkdir()
    _write_worn_source(source_dir)

    images = SimpleNamespace(
        generate=Mock(return_value=_image_response(image_input_tokens=0)),
        edit=Mock(return_value=_image_response(image_input_tokens=4_700)),
    )
    client = SimpleNamespace(images=images)
    monkeypatch.setattr(run_experiment, "_get_worn_image_client", lambda: client)

    out_dir = tmp_path / "worn-real-mocked"
    run_experiment.run_ab_worn_view(source_dir, "C10", out_dir=out_dir)

    images.generate.assert_called_once()
    images.edit.assert_called_once()
    for call in (images.generate.call_args, images.edit.call_args):
        assert call.kwargs["model"] == "gpt-image-2"
        assert call.kwargs["size"] == "1024x1536"
        assert call.kwargs["quality"] == "low"
        assert call.kwargs["n"] == 1
        assert "input_fidelity" not in call.kwargs
    assert "image" not in images.generate.call_args.kwargs
    assert images.edit.call_args.kwargs["image"].name.endswith("002_C10_wide.png")
    assert images.edit.call_args.kwargs["image"].closed

    assert len(list(out_dir.glob("*_X.png"))) == 1
    assert len(list(out_dir.glob("*_Y.png"))) == 1
    manifest = json.loads((out_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["provider_call_attempts"] == 2

    with (out_dir / "results_blind.csv").open(encoding="utf-8", newline="") as file:
        rows = list(csv.DictReader(file))
    assert {row["status"] for row in rows} == {"completed"}
    assert {row["usage_image_input_tokens"] for row in rows} == {"0", "4700"}
    assert all(float(row["usage_cost_estimate"]) > 0 for row in rows)


def test_worn_ab_stops_before_second_paid_call_after_failure(monkeypatch, tmp_path):
    source_dir = tmp_path / "source-run"
    source_dir.mkdir()
    _write_worn_source(source_dir)

    images = SimpleNamespace(
        generate=Mock(return_value=_image_response(image_input_tokens=0)),
        edit=Mock(side_effect=RuntimeError("provider unavailable")),
    )
    monkeypatch.setattr(
        run_experiment,
        "_get_worn_image_client",
        lambda: SimpleNamespace(images=images),
    )

    out_dir = tmp_path / "worn-failed"
    run_experiment.run_ab_worn_view(source_dir, "C10", out_dir=out_dir)

    images.edit.assert_called_once()
    images.generate.assert_not_called()
    manifest = json.loads((out_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["provider_call_attempts"] == 1
    with (out_dir / "results_blind.csv").open(encoding="utf-8", newline="") as file:
        rows = list(csv.DictReader(file))
    assert len(rows) == 1
    assert rows[0]["status"] == "image_error"


def _proposal(title: str, item_types: tuple[str, ...], *, footwear=True, brand=None):
    items = [
        OutfitItem(
            category="upper" if index == 0 else "lower",
            item_type=item_type,
            brand=brand,
            color="azul",
            certainty="high",
            visual_phrase_en=f"blue {item_type}",
        )
        for index, item_type in enumerate(item_types)
    ]
    if footwear:
        items.append(
            OutfitItem(
                category="footwear",
                item_type="zapatos",
                brand=brand,
                color="negro",
                certainty="high",
                visual_phrase_en="black shoes",
            )
        )
    return ProposedOutfit(title=title, outfit_summary=f"Resumen de {title}", items=items)


def _proposal_set(*proposals):
    return ProposalSetExtraction(status="ok", proposals=list(proposals))


def test_proposal_dry_run_prepares_artifacts_without_calling_the_provider(
    monkeypatch,
    tmp_path,
):
    def fail_if_called(_situation):
        raise AssertionError("El dry-run no debe llamar al proveedor")

    monkeypatch.setattr(run_experiment, "propose_outfits_from_situation", fail_if_called)

    out_dir = tmp_path / "proposals-dry-run"
    result_dir = run_experiment.run_proposals(4, dry_run=True, out_dir=out_dir)

    assert result_dir == out_dir
    summary = json.loads((out_dir / "summary.json").read_text(encoding="utf-8"))
    assert summary["dry_run"] is True
    assert summary["cases"] == 4
    assert summary["max_text_calls"] == 8
    assert summary["actual_text_calls"] == 0
    assert summary["image_calls"] == 0

    with (out_dir / "proposals.csv").open(encoding="utf-8", newline="") as file:
        rows = list(csv.DictReader(file))
    assert {row["status"] for row in rows} == {"dry_run"}
    assert not list(out_dir.glob("P0*.json"))


def test_proposal_run_records_usage_cost_and_objective_checks(monkeypatch, tmp_path):
    extraction = _proposal_set(
        _proposal("Lino arena", ("americana", "pantalón")),
        _proposal("Chaleco", ("chaleco", "chinos")),
        _proposal("Azul noche", ("blazer", "vaqueros")),
    )
    calls = []

    def fake_call(situation):
        calls.append(situation)
        return ProposalCallResult(extraction, "gpt-5.4-nano", None, 1200, 800)

    monkeypatch.setattr(run_experiment, "propose_outfits_from_situation", fake_call)

    out_dir = tmp_path / "proposals-run"
    run_experiment.run_proposals(2, out_dir=out_dir)

    assert len(calls) == 2
    with (out_dir / "proposals.csv").open(encoding="utf-8", newline="") as file:
        rows = list(csv.DictReader(file))

    assert [row["text_calls"] for row in rows] == ["1", "1"]
    assert {row["all_generable"] for row in rows} == {"True"}
    assert {row["distinct_silhouettes"] for row in rows} == {"True"}
    assert {row["all_with_footwear"] for row in rows} == {"True"}
    assert {row["no_invented_brand"] for row in rows} == {"True"}

    expected = estimate_text_cost("gpt-5.4-nano", input_tokens=1200, output_tokens=800)
    assert float(rows[0]["cost_estimate"]) == round(expected, 6)

    summary = json.loads((out_dir / "summary.json").read_text(encoding="utf-8"))
    assert summary["actual_text_calls"] == 2
    assert summary["image_calls"] == 0
    assert len(list(out_dir.glob("P0*.json"))) == 2


def test_proposal_run_flags_a_fallback_and_a_failed_objective_check(monkeypatch, tmp_path):
    # Tres propuestas con la misma silueta: aunque el fallback ya haya saltado,
    # hay que dejar constancia de que no son realmente distintas.
    repetidas = _proposal_set(
        _proposal("Una", ("americana", "pantalón")),
        _proposal("Dos", ("americana", "pantalón")),
        _proposal("Tres", ("americana", "pantalón")),
    )
    monkeypatch.setattr(
        run_experiment,
        "propose_outfits_from_situation",
        lambda _situation: ProposalCallResult(repetidas, "gpt-5.4-nano", "gpt-5.4-mini", 1500, 900),
    )

    out_dir = tmp_path / "proposals-fallback"
    run_experiment.run_proposals(1, out_dir=out_dir)

    with (out_dir / "proposals.csv").open(encoding="utf-8", newline="") as file:
        row = next(iter(csv.DictReader(file)))

    assert row["model_fallback"] == "gpt-5.4-mini"
    assert row["text_calls"] == "2"
    assert row["distinct_silhouettes"] == "False"
    # El coste se cobra al modelo que realmente produjo la salida.
    expected = estimate_text_cost("gpt-5.4-mini", input_tokens=1500, output_tokens=900)
    assert float(row["cost_estimate"]) == round(expected, 6)


def test_proposal_run_refuses_a_scope_above_the_approved_ceiling(monkeypatch, tmp_path):
    monkeypatch.setattr(run_experiment, "PROPOSAL_TEST_SET", run_experiment.PROPOSAL_TEST_SET * 3)
    monkeypatch.setattr(
        run_experiment,
        "propose_outfits_from_situation",
        lambda _situation: pytest.fail("No debe llamar si el alcance supera el techo"),
    )

    with pytest.raises(SystemExit):
        run_experiment.run_proposals(None, out_dir=tmp_path / "too-big")
