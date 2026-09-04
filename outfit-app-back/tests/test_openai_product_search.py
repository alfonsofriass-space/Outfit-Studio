import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from app.services import openai_product_search


def _response(
    candidates,
    sources,
    usage=None,
    *,
    status="completed",
    incomplete_reason=None,
    web_call_count=1,
):
    web_calls = [
        {
            "type": "web_search_call",
            "action": {
                "type": "search" if index == 0 else "open_page",
                "sources": ([{"url": url} for url in sources] if index == 0 else None),
            },
        }
        for index in range(web_call_count)
    ]
    data = {
        "id": "resp_product_search_test",
        "status": status,
        "incomplete_details": ({"reason": incomplete_reason} if incomplete_reason else None),
        "output": [
            *web_calls,
            {
                "type": "message",
                "content": [
                    {
                        "type": "output_text",
                        "text": json.dumps({"candidates": candidates}),
                        "annotations": [],
                    }
                ],
            },
        ],
    }
    if usage is not None:
        data["usage"] = usage
    return SimpleNamespace(
        output_text=json.dumps({"candidates": candidates}),
        model_dump=lambda **_: data,
    )


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("gorra VERSACE verde militar", "Versace"),
        ("chaqueta Versace Jeans Couture negra", "Versace Jeans Couture"),
        ("camisa de Massimo Dutti", "Massimo Dutti"),
        ("camiseta verde militar", None),
    ],
)
def test_detect_known_brand_only_uses_explicit_supported_names(text, expected):
    assert openai_product_search.detect_known_brand(text) == expected


def test_search_uses_bounded_calls_and_keeps_only_sourced_allowed_urls(
    monkeypatch,
):
    source = "https://shop.mango.com/es/es/p/camisa-lino_123?color=blanco"
    canonical = "https://shop.mango.com/es/es/p/camisa-lino_123"
    response = _response(
        [
            {
                "title": "Camisa de lino",
                "product_url": canonical,
                "price_text": "39,99 €",
            },
            {
                "title": "Sin fuente",
                "product_url": "https://www.zara.com/es/es/inventado.html",
                "price_text": None,
            },
        ],
        [source],
        {"input_tokens": 8700, "output_tokens": 420},
    )
    create = Mock(return_value=response)
    monkeypatch.setattr(
        openai_product_search,
        "_get_client",
        lambda: SimpleNamespace(responses=SimpleNamespace(create=create)),
    )

    result = openai_product_search.search_products("camisa blanca lino comprar online España")

    create.assert_called_once()
    payload = create.call_args.kwargs
    assert payload["model"] == "gpt-5.4-nano"
    assert payload["max_tool_calls"] == 2
    assert payload["max_output_tokens"] == 4_000
    assert payload["parallel_tool_calls"] is False
    assert payload["tools"][0]["filters"]["allowed_domains"] == list(openai_product_search.STORES)
    assert "Consulta exacta: camisa blanca lino comprar online España" in payload["input"]
    assert "usa todos los términos descriptivos" in payload["input"]
    assert "conserva el tipo de prenda" in payload["input"]
    assert result.web_search_calls == 1
    assert result.cost_estimate == 0.012265
    assert [candidate.model_dump() for candidate in result.candidates] == [
        {
            "title": "Camisa de lino",
            "store": "Mango",
            "product_url": source,
            "price_text": "39,99 €",
        }
    ]


def test_search_does_not_request_web_images(monkeypatch):
    """El proveedor nunca devuelve imágenes bajo `filters.allowed_domains`.

    Medido el 2026-08-31 con una llamada real: diez entradas, las diez de texto
    (pricing.md, sección 11). Pedirlas otra vez añadiría claves muertas al
    contrato, así que la petición se queda sin `search_content_types`,
    `image_settings` ni el `include` de `web_search_call.results`.
    """
    response = _response([], [])
    create = Mock(return_value=response)
    monkeypatch.setattr(
        openai_product_search,
        "_get_client",
        lambda: SimpleNamespace(responses=SimpleNamespace(create=create)),
    )

    openai_product_search.search_products("camisa blanca lino comprar online España")

    payload = create.call_args.kwargs
    assert payload["include"] == ["web_search_call.action.sources"]
    assert "search_content_types" not in payload["tools"][0]
    assert "image_settings" not in payload["tools"][0]


def test_completed_two_action_search_is_accepted_and_costed(monkeypatch):
    source = "https://www.zara.com/es/es/bailarina-negra-p0123.html"
    response = _response(
        [
            {
                "title": "Bailarina negra de punta",
                "product_url": source,
                "price_text": "29,95 EUR",
            }
        ],
        [source],
        usage={"input_tokens": 5_148, "output_tokens": 225},
        web_call_count=2,
    )
    create = Mock(return_value=response)
    monkeypatch.setattr(
        openai_product_search,
        "_get_client",
        lambda: SimpleNamespace(responses=SimpleNamespace(create=create)),
    )

    result = openai_product_search.search_products(
        "bailarinas negras de punta comprar online España"
    )

    create.assert_called_once()
    assert result.web_search_calls == 2
    assert result.cost_estimate == 0.021311
    assert [candidate.product_url for candidate in result.candidates] == [source]


def test_explicit_brand_uses_official_and_multibrand_stores_only(monkeypatch):
    official = "https://www.versace.com/es/es/gorra-medusa-verde/123.html"
    distributor = "https://www.zalando.es/versace-jeans-couture-gorra-verde-militar-vj123.html"
    wrong_brand = "https://www.zalando.es/zara-gorra-verde-militar-z123.html"
    response = _response(
        [
            {
                "title": "Gorra Medusa verde",
                "product_url": official,
                "price_text": "390 €",
            },
            {
                "title": "Versace Jeans Couture gorra verde",
                "product_url": distributor,
                "price_text": "120 €",
            },
            {
                "title": "Zara gorra verde militar",
                "product_url": wrong_brand,
                "price_text": "19,95 €",
            },
        ],
        [official, distributor, wrong_brand],
        usage={"input_tokens": 8_700, "output_tokens": 420},
    )
    create = Mock(return_value=response)
    monkeypatch.setattr(
        openai_product_search,
        "_get_client",
        lambda: SimpleNamespace(responses=SimpleNamespace(create=create)),
    )

    result = openai_product_search.search_products(
        "gorra Versace verde militar comprar online España",
        brand="Versace",
    )

    payload = create.call_args.kwargs
    assert payload["tools"][0]["filters"]["allowed_domains"] == [
        "versace.com",
        "zalando.es",
        "elcorteingles.es",
    ]
    assert "Marca obligatoria: Versace" in payload["input"]
    assert "Busca primero en la tienda oficial versace.com" in payload["input"]
    assert "no busques sustitutos de otras marcas" in payload["input"]
    assert [candidate.product_url for candidate in result.candidates] == [
        official,
        distributor,
    ]


def test_unknown_explicit_brand_uses_only_multibrand_stores(monkeypatch):
    source = "https://www.zalando.es/acme-gorra-verde-a123.html"
    response = _response(
        [
            {
                "title": "Acme gorra verde",
                "product_url": source,
                "price_text": None,
            }
        ],
        [source],
    )
    create = Mock(return_value=response)
    monkeypatch.setattr(
        openai_product_search,
        "_get_client",
        lambda: SimpleNamespace(responses=SimpleNamespace(create=create)),
    )

    result = openai_product_search.search_products(
        "gorra Acme verde comprar online España",
        brand="Acme",
    )

    assert create.call_args.kwargs["tools"][0]["filters"]["allowed_domains"] == [
        "zalando.es",
        "elcorteingles.es",
    ]
    assert [candidate.product_url for candidate in result.candidates] == [source]


def test_more_than_two_web_actions_are_rejected_without_retry(monkeypatch):
    response = _response([], [], web_call_count=3)
    create = Mock(return_value=response)
    monkeypatch.setattr(
        openai_product_search,
        "_get_client",
        lambda: SimpleNamespace(responses=SimpleNamespace(create=create)),
    )

    with pytest.raises(
        openai_product_search.ProductSearchProviderError,
        match="se observaron 3",
    ):
        openai_product_search.search_products("camisa blanca comprar online España")

    create.assert_called_once()


def test_missing_usage_uses_the_visible_conservative_estimate(monkeypatch):
    response = _response([], [])
    monkeypatch.setattr(
        openai_product_search,
        "_get_client",
        lambda: SimpleNamespace(responses=SimpleNamespace(create=Mock(return_value=response))),
    )

    result = openai_product_search.search_products("pantalón palazzo beige comprar online España")

    assert result.candidates == []
    assert result.input_tokens == 0
    assert result.output_tokens == 0
    assert result.cost_estimate == 0.03


def test_null_source_collections_are_treated_as_empty(monkeypatch):
    """Responses puede representar colecciones ausentes como null."""
    data = {
        "output": [
            {
                "type": "web_search_call",
                "action": {"type": "search", "sources": None},
            },
            {
                "type": "message",
                "content": [
                    {
                        "type": "output_text",
                        "text": json.dumps({"candidates": []}),
                        "annotations": None,
                    }
                ],
            },
        ]
    }
    response = SimpleNamespace(
        output_text=json.dumps({"candidates": []}),
        model_dump=lambda **_: data,
    )
    monkeypatch.setattr(
        openai_product_search,
        "_get_client",
        lambda: SimpleNamespace(responses=SimpleNamespace(create=Mock(return_value=response))),
    )

    result = openai_product_search.search_products(
        "blusa top halter blanca decorados griegos comprar online España"
    )

    assert result.web_search_calls == 1
    assert result.candidates == []
    assert result.cost_estimate == 0.03


def test_client_disables_automatic_retries(monkeypatch):
    client = object()
    openai_constructor = Mock(return_value=client)
    monkeypatch.setattr(openai_product_search, "OpenAI", openai_constructor)

    assert openai_product_search._get_client() is client
    assert openai_constructor.call_args.kwargs["max_retries"] == 0


def test_incomplete_response_is_classified_and_logged_without_retry(monkeypatch):
    response = _response(
        [],
        [],
        status="incomplete",
        incomplete_reason="max_output_tokens",
    )
    create = Mock(return_value=response)
    warning = Mock()
    monkeypatch.setattr(
        openai_product_search,
        "_get_client",
        lambda: SimpleNamespace(responses=SimpleNamespace(create=create)),
    )
    monkeypatch.setattr(openai_product_search.logger, "warning", warning)

    with pytest.raises(
        openai_product_search.ProductSearchIncompleteError,
        match="max_output_tokens",
    ):
        openai_product_search.search_products("falda plisada burdeos comprar online España")

    create.assert_called_once()
    warning.assert_called_once()
    assert warning.call_args.args[1] == "resp_product_search_test"
    assert warning.call_args.args[3] == "max_output_tokens"
    assert warning.call_args.args[6] == "ProductSearchIncompleteError"
