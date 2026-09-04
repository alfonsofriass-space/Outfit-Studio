import json
import sqlite3
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from experiments import product_search_experiment as experiment


def _item(item_type, *, color=None, material=None, fit=None, details=None):
    return {
        "category": "upper",
        "item_type": item_type,
        "color": color,
        "material": material,
        "fit": fit,
        "details": details or [],
        "certainty": "high",
        "visual_phrase_en": "must never enter the query",
    }


def _database(path, outfits):
    connection = sqlite3.connect(path)
    connection.execute("CREATE TABLE outfits (id INTEGER PRIMARY KEY, outfit_json TEXT NOT NULL)")
    for outfit_id, items in outfits.items():
        connection.execute(
            "INSERT INTO outfits VALUES (?, ?)",
            (
                outfit_id,
                json.dumps(
                    {
                        "status": "ok",
                        "outfit_summary": "Resumen",
                        "items": items,
                        "styling_notes_en": [],
                    }
                ),
            ),
        )
    connection.commit()
    connection.close()


def _responses_reply(payload, sources, *, web_call_count=1, tokens=(900, 240)):
    """Respuesta de la Responses API: sus acciones web y el mensaje JSON final.

    `payload` es lo único que cambia entre las dos rutas: `{"candidates": [...]}` por
    prenda y `{"items": [...]}` en el lote.
    """
    web_calls = [
        {
            "type": "web_search_call",
            "action": {
                "type": "search" if index == 0 else "open_page",
                "sources": [{"url": url} for url in sources] if index == 0 else None,
            },
        }
        for index in range(web_call_count)
    ]
    text = json.dumps(payload)
    data = {
        "output": [
            *web_calls,
            {
                "type": "message",
                "content": [{"type": "output_text", "text": text, "annotations": []}],
            },
        ],
        "usage": {"input_tokens": tokens[0], "output_tokens": tokens[1]},
    }
    return SimpleNamespace(output_text=text, model_dump=lambda **_: data)


def test_query_uses_only_confirmed_attributes_without_repetitions():
    item = experiment.OutfitItem.model_validate(
        _item(
            "sandalias con tiras estilo romano",
            color="marrones",
            details=["tiras estilo romano"],
        )
    )

    query = experiment.build_query(item, "de piel")

    assert query == "sandalias con tiras estilo romano marrones de piel comprar online España"
    assert "must never enter the query" not in query


@pytest.mark.parametrize(
    "raw_item",
    [
        _item("pantalón"),
        _item("complementos", color="dorados"),
    ],
)
def test_query_rejects_insufficient_or_generic_items(raw_item):
    with pytest.raises(experiment.ExperimentError):
        experiment.build_query(experiment.OutfitItem.model_validate(raw_item))


def test_load_cases_validates_indexes_and_unselected_extras(tmp_path):
    database = tmp_path / "outfit.db"
    _database(
        database,
        {4: [_item("kimono", color="azul"), _item("top", color="blanco")]},
    )

    cases = experiment.load_cases(database, ["4:0,1"], ["4:1=cuello redondo"])

    assert [(case.outfit_id, case.item_index) for case in cases] == [(4, 0), (4, 1)]
    assert "cuello redondo" in cases[1].query
    with pytest.raises(experiment.ExperimentError, match="no tiene una prenda"):
        experiment.load_cases(database, ["4:2"])
    with pytest.raises(experiment.ExperimentError, match="no seleccionadas"):
        experiment.load_cases(database, ["4:0"], ["4:1=cuello redondo"])


def test_dry_run_never_creates_client_and_writes_exact_request(monkeypatch, tmp_path):
    database = tmp_path / "outfit.db"
    _database(database, {4: [_item("kimono", color="azul"), _item("top", color="blanco")]})
    monkeypatch.setattr(
        experiment,
        "_get_client",
        lambda: pytest.fail("El dry-run no debe crear un cliente."),
    )
    out_dir = tmp_path / "dry"

    experiment.run(database, ["4:0,1"], dry_run=True, out_dir=out_dir)

    manifest = json.loads((out_dir / "manifest.json").read_text())
    request = json.loads((out_dir / manifest["planned_cases"][0]["request_file"]).read_text())
    assert manifest["provider_call_attempts"] == 0
    assert manifest["planned_web_search_calls"] == 4
    assert manifest["cost_budget"]["display_ceiling_total"] == 0.06
    assert request["model"] == "gpt-5.4-nano"
    assert request["reasoning"] == {"effort": "low"}
    assert request["max_tool_calls"] == 2
    assert request["tools"][0]["filters"]["allowed_domains"] == list(experiment.STORES)
    assert "usa todos los términos descriptivos" in request["input"]
    assert "conserva el tipo de prenda" in request["input"]
    assert not list(out_dir.glob("*_raw_response.json"))


def test_eight_item_budget_matches_the_approval_summary():
    budget = experiment.cost_budget(8)

    assert budget == {
        "strategy": "per_item",
        "item_count": 8,
        "calculated_ceiling_per_item": 0.0242,
        "display_ceiling_per_item": 0.03,
        "calculated_total": 0.1936,
        "display_ceiling_total": 0.24,
        "recommended_approval_budget": 0.28,
    }


def test_batch_dry_run_writes_one_bounded_request_without_client(monkeypatch, tmp_path):
    database = tmp_path / "outfit.db"
    _database(
        database,
        {
            5: [
                _item("camisa", color="blanca", material="lino"),
                _item("pantalón palazzo", color="beige"),
            ]
        },
    )
    monkeypatch.setattr(
        experiment,
        "_get_client",
        lambda: pytest.fail("El dry-run no debe crear un cliente."),
    )
    out_dir = tmp_path / "batch-dry"

    experiment.run(database, ["5:0,1"], dry_run=True, out_dir=out_dir, batch=True)

    manifest = json.loads((out_dir / "manifest.json").read_text())
    request_files = list(out_dir.glob("*_request.json"))
    request = json.loads(request_files[0].read_text())
    assert len(request_files) == 1
    assert manifest["strategy"] == "outfit_batch"
    assert manifest["planned_web_search_calls"] == 1
    assert manifest["provider_call_attempts"] == 0
    assert manifest["cost_budget"] == {
        "strategy": "outfit_batch",
        "item_count": 2,
        "planned_web_search_calls": 1,
        "calculated_absolute_ceiling": 0.03835,
        "display_ceiling_total": 0.04,
        "recommended_approval_budget": 0.04,
    }
    assert request["max_tool_calls"] == 1
    assert request["max_output_tokens"] == experiment.BATCH_MAX_OUTPUT_TOKENS
    assert "outfit5_item0" in request["input"]
    assert "outfit5_item1" in request["input"]


def test_mocked_batch_maps_results_and_marks_missing_items(monkeypatch, tmp_path):
    database = tmp_path / "outfit.db"
    _database(
        database,
        {
            5: [
                _item("camisa", color="blanca", material="lino"),
                _item("pantalón palazzo", color="beige"),
            ]
        },
    )
    shirt_url = "https://www.zara.com/es/es/camisa-p0123.html"
    response = _responses_reply(
        {
            "items": [
                {
                    "item_id": "outfit5_item0",
                    "candidates": [
                        {
                            "title": "Camisa de lino",
                            "product_url": shirt_url,
                            "price_text": "29,95 EUR",
                        }
                    ],
                }
            ]
        },
        [shirt_url],
        tokens=(8_700, 600),
    )
    responses = SimpleNamespace(create=Mock(return_value=response))
    monkeypatch.setattr(
        experiment,
        "_get_client",
        lambda: SimpleNamespace(responses=responses),
    )
    out_dir = tmp_path / "batch-real"

    experiment.run(
        database,
        ["5:0,1"],
        dry_run=False,
        out_dir=out_dir,
        approved_max_calls=1,
        approval_budget=0.04,
        batch=True,
    )

    rows = json.loads((out_dir / "results.json").read_text())
    manifest = json.loads((out_dir / "manifest.json").read_text())
    responses.create.assert_called_once()
    assert rows[0]["status"] == "completed"
    assert rows[0]["results"][0]["product_url"] == shirt_url
    assert rows[1]["status"] == "completed_missing"
    assert rows[1]["results"] == []
    assert manifest["observed_web_search_calls"] == 1
    assert manifest["execution"]["missing_item_ids"] == ["outfit5_item1"]
    assert manifest["execution"]["usage_cost_estimate"] == 0.01249


def test_mocked_search_keeps_only_sourced_allowed_unique_products(monkeypatch, tmp_path):
    database = tmp_path / "outfit.db"
    _database(database, {7: [_item("top halter", color="blanco")]})
    source = "https://www.zara.com/es/es/top-p0123.html?color=white"
    canonical = "https://www.zara.com/es/es/top-p0123.html"
    response = _responses_reply(
        {
            "candidates": [
                {"title": "Top blanco", "product_url": canonical, "price_text": "25,95 EUR"},
                {"title": "Duplicado", "product_url": f"{canonical}?v=2", "price_text": None},
                {
                    "title": "Sin fuente",
                    "product_url": "https://shop.mango.com/es/inventado",
                    "price_text": None,
                },
            ]
        },
        [source],
        web_call_count=2,
    )
    responses = SimpleNamespace(create=Mock(return_value=response))
    monkeypatch.setattr(
        experiment,
        "_get_client",
        lambda: SimpleNamespace(responses=responses),
    )
    out_dir = tmp_path / "mocked"

    experiment.run(
        database,
        ["7:0"],
        dry_run=False,
        out_dir=out_dir,
        approved_max_calls=2,
        approval_budget=0.03,
    )

    rows = json.loads((out_dir / "results.json").read_text())
    manifest = json.loads((out_dir / "manifest.json").read_text())
    responses.create.assert_called_once()
    assert manifest["observed_web_search_calls"] == 2
    assert rows[0]["rejected_candidate_count"] == 2
    assert rows[0]["results"] == [
        {
            "title": "Top blanco",
            "store": "Zara",
            "product_url": source,
            "price_text": "25,95 EUR",
        }
    ]


def test_real_mode_rejects_unapproved_scope_before_client(monkeypatch, tmp_path):
    database = tmp_path / "outfit.db"
    _database(
        database,
        {2: [_item("camisa", color="blanca"), _item("pantalón", color="negro")]},
    )
    monkeypatch.setattr(
        experiment,
        "_get_client",
        lambda: pytest.fail("No debe crear cliente."),
    )

    with pytest.raises(experiment.ExperimentError, match="límites"):
        experiment.run(
            database,
            ["2:0"],
            dry_run=False,
            approved_max_calls=1,
            approval_budget=0.20,
        )
    with pytest.raises(experiment.ExperimentError, match="límites"):
        experiment.run(
            database,
            ["2:0,1"],
            dry_run=False,
            approved_max_calls=2,
            approval_budget=0.04,
            batch=True,
        )


def test_provider_error_stops_without_retry(monkeypatch, tmp_path):
    database = tmp_path / "outfit.db"
    _database(
        database,
        {6: [_item("camiseta", color="azul"), _item("vaqueros", color="azules")]},
    )
    responses = SimpleNamespace(create=Mock(side_effect=RuntimeError("timeout")))
    monkeypatch.setattr(
        experiment,
        "_get_client",
        lambda: SimpleNamespace(responses=responses),
    )
    out_dir = tmp_path / "failed"

    experiment.run(
        database,
        ["6:0,1"],
        dry_run=False,
        out_dir=out_dir,
        approved_max_calls=4,
        approval_budget=0.06,
    )

    rows = json.loads((out_dir / "results.json").read_text())
    manifest = json.loads((out_dir / "manifest.json").read_text())
    responses.create.assert_called_once()
    assert manifest["provider_call_attempts"] == 1
    assert rows[0]["status"] == "provider_or_protocol_error"
    assert rows[1]["status"] == "pending"
