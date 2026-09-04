from types import SimpleNamespace
from unittest.mock import MagicMock, call, patch

import httpx
import pytest
from factories import make_item
from openai import (
    APIConnectionError,
    APITimeoutError,
    AuthenticationError,
    BadRequestError,
    InternalServerError,
    PermissionDeniedError,
    RateLimitError,
)

from app.prompts.text_system_prompt import SYSTEM_PROMPT
from app.schemas import OutfitExtraction, OutfitItem
from app.services import openai_text

DESCRIPTION = "chaqueta negra y vaqueros azules"
PRIMARY_MODEL = "test-primary"
FALLBACK_MODEL = "test-fallback"


@pytest.fixture(autouse=True)
def _model_names(monkeypatch):
    settings = openai_text.get_settings()
    monkeypatch.setattr(settings, "openai_text_model_primary", PRIMARY_MODEL)
    monkeypatch.setattr(settings, "openai_text_model_fallback", FALLBACK_MODEL)


def _extraction(
    *,
    status: str = "ok",
    items: list[OutfitItem] | None = None,
) -> OutfitExtraction:
    return OutfitExtraction(
        status=status,
        outfit_summary="look de prueba",
        items=items
        if items is not None
        else [
            make_item("upper", "chaqueta", "black jacket"),
            make_item("lower", "vaqueros", "blue jeans"),
        ],
    )


def _low_quality_extraction() -> OutfitExtraction:
    return _extraction(
        items=[
            make_item("upper", "chaqueta", "jacket", certainty="low"),
            make_item("lower", "vaqueros", "jeans", certainty="low"),
        ]
    )


def _status_error(error_type, status_code: int):
    request = httpx.Request("POST", "https://api.openai.com/v1/chat/completions")
    response = httpx.Response(status_code, request=request)
    return error_type("upstream error", response=response, body=None)


def test_system_prompt_keeps_mismatched_footwear_as_one_pair():
    assert "UN único par de botines desparejados, no dos pares" in SYSTEM_PROMPT
    assert "one mismatched ankle boot pair, one black and one brown" in SYSTEM_PROMPT
    assert "styling_notes_en" in SYSTEM_PROMPT


def test_system_prompt_extracts_only_explicit_brands():
    assert 'Usa "brand" únicamente cuando el usuario nombre una marca concreta' in SYSTEM_PROMPT
    assert "Si no hay una marca explícita, usa null" in SYSTEM_PROMPT


def test_primary_success_does_not_call_fallback():
    primary = _extraction()
    with patch.object(openai_text, "_call_openai_text", return_value=primary) as model_call:
        extraction, used_primary, used_fallback = openai_text.extract_outfit_from_text(DESCRIPTION)

    assert extraction is primary
    assert used_primary == PRIMARY_MODEL
    assert used_fallback is None
    model_call.assert_called_once_with(DESCRIPTION, PRIMARY_MODEL)


def test_primary_clarification_does_not_call_fallback():
    clarification = _extraction(status="needs_clarification", items=[])
    with patch.object(openai_text, "_call_openai_text", return_value=clarification) as model_call:
        extraction, _, used_fallback = openai_text.extract_outfit_from_text(DESCRIPTION)

    assert extraction is clarification
    assert used_fallback is None
    model_call.assert_called_once_with(DESCRIPTION, PRIMARY_MODEL)


def test_minimum_contract_failure_is_terminal_and_does_not_call_fallback():
    """Una lectura fiel de un texto con una sola prenda no mejora con otro modelo.

    Gastar mini aquí duplicaba el coste y terminaba en `TextModelOutputError` →
    HTTP 502, en vez de dejar que el servicio pidiera una aclaración concreta.
    """
    single_item = _extraction(items=[make_item("footwear", "zapatillas", "white sneakers")])
    with patch.object(openai_text, "_call_openai_text", return_value=single_item) as model_call:
        extraction, _, used_fallback = openai_text.extract_outfit_from_text(DESCRIPTION)

    assert extraction is single_item
    assert used_fallback is None
    model_call.assert_called_once_with(DESCRIPTION, PRIMARY_MODEL)


@pytest.mark.parametrize(
    "primary",
    [
        pytest.param(_low_quality_extraction(), id="low-certainty"),
    ],
)
def test_primary_quality_problem_calls_fallback_once(primary):
    fallback = _extraction()
    with patch.object(
        openai_text,
        "_call_openai_text",
        side_effect=[primary, fallback],
    ) as model_call:
        extraction, used_primary, used_fallback = openai_text.extract_outfit_from_text(DESCRIPTION)

    assert extraction is fallback
    assert used_primary == PRIMARY_MODEL
    assert used_fallback == FALLBACK_MODEL
    assert model_call.call_args_list == [
        call(DESCRIPTION, PRIMARY_MODEL),
        call(DESCRIPTION, FALLBACK_MODEL),
    ]


def test_primary_parse_problem_calls_fallback_once():
    fallback = _extraction()
    with patch.object(
        openai_text,
        "_call_openai_text",
        side_effect=[openai_text.TextModelOutputError("bad output"), fallback],
    ) as model_call:
        extraction, _, used_fallback = openai_text.extract_outfit_from_text(DESCRIPTION)

    assert extraction is fallback
    assert used_fallback == FALLBACK_MODEL
    assert model_call.call_count == 2


@pytest.mark.parametrize(
    "sdk_error",
    [
        pytest.param(
            APIConnectionError(
                request=httpx.Request("POST", "https://api.openai.com/v1/chat/completions")
            ),
            id="connection",
        ),
        pytest.param(
            APITimeoutError(
                request=httpx.Request("POST", "https://api.openai.com/v1/chat/completions")
            ),
            id="timeout",
        ),
        pytest.param(_status_error(AuthenticationError, 401), id="authentication"),
        pytest.param(_status_error(PermissionDeniedError, 403), id="permission"),
        pytest.param(_status_error(RateLimitError, 429), id="rate-limit"),
        pytest.param(_status_error(InternalServerError, 500), id="server"),
    ],
)
def test_service_errors_never_call_fallback(sdk_error):
    with patch.object(
        openai_text,
        "_call_openai_text",
        side_effect=sdk_error,
    ) as model_call:
        with pytest.raises(openai_text.TextServiceUnavailableError):
            openai_text.extract_outfit_from_text(DESCRIPTION)

    model_call.assert_called_once_with(DESCRIPTION, PRIMARY_MODEL)


def test_provider_error_never_calls_fallback():
    error = _status_error(BadRequestError, 400)
    with patch.object(openai_text, "_call_openai_text", side_effect=error) as model_call:
        with pytest.raises(openai_text.TextProviderError):
            openai_text.extract_outfit_from_text(DESCRIPTION)

    model_call.assert_called_once_with(DESCRIPTION, PRIMARY_MODEL)


def test_fallback_parse_failure_is_not_retried():
    with patch.object(
        openai_text,
        "_call_openai_text",
        side_effect=[
            _low_quality_extraction(),
            openai_text.TextModelOutputError("fallback output invalid"),
        ],
    ) as model_call:
        with pytest.raises(openai_text.TextModelOutputError):
            openai_text.extract_outfit_from_text(DESCRIPTION)

    assert model_call.call_count == 2


def test_fallback_service_failure_is_not_retried():
    rate_limit = _status_error(RateLimitError, 429)
    with patch.object(
        openai_text,
        "_call_openai_text",
        side_effect=[_low_quality_extraction(), rate_limit],
    ) as model_call:
        with pytest.raises(openai_text.TextServiceUnavailableError):
            openai_text.extract_outfit_from_text(DESCRIPTION)

    assert model_call.call_count == 2


@pytest.mark.parametrize(
    "fallback",
    [
        pytest.param(_low_quality_extraction(), id="low-certainty"),
    ],
)
def test_unusable_fallback_is_rejected_without_third_call(fallback):
    with patch.object(
        openai_text,
        "_call_openai_text",
        side_effect=[_low_quality_extraction(), fallback],
    ) as model_call:
        with pytest.raises(openai_text.TextModelOutputError):
            openai_text.extract_outfit_from_text(DESCRIPTION)

    assert model_call.call_count == 2


def test_fallback_below_minimum_contract_is_returned_not_raised():
    """Una extracción usable que el producto rechaza no es un fallo del proveedor.

    Devolverla permite pedir una aclaración concreta; convertirla en
    `TextModelOutputError` daría un HTTP 502 sin explicar al usuario qué falta.
    """
    single_item = _extraction(items=[make_item("footwear", "zapatillas", "white sneakers")])
    with patch.object(
        openai_text,
        "_call_openai_text",
        side_effect=[_low_quality_extraction(), single_item],
    ) as model_call:
        extraction, _, used_fallback = openai_text.extract_outfit_from_text(DESCRIPTION)

    assert extraction is single_item
    assert used_fallback == FALLBACK_MODEL
    assert model_call.call_count == 2


def test_fallback_clarification_is_a_valid_terminal_result():
    clarification = _extraction(status="needs_clarification", items=[])
    with patch.object(
        openai_text,
        "_call_openai_text",
        side_effect=[_low_quality_extraction(), clarification],
    ):
        extraction, _, used_fallback = openai_text.extract_outfit_from_text(DESCRIPTION)

    assert extraction is clarification
    assert used_fallback == FALLBACK_MODEL


def test_empty_parsed_response_is_an_output_error():
    fake_client = MagicMock()
    fake_client.chat.completions.parse.return_value = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(parsed=None, refusal=None))]
    )

    with patch.object(openai_text, "_get_client", return_value=fake_client):
        with pytest.raises(openai_text.TextModelOutputError):
            openai_text._call_openai_text(DESCRIPTION, PRIMARY_MODEL)


def test_model_refusal_is_not_treated_as_parse_quality():
    fake_client = MagicMock()
    fake_client.chat.completions.parse.return_value = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(parsed=None, refusal="refused"))]
    )

    with patch.object(openai_text, "_get_client", return_value=fake_client):
        with pytest.raises(openai_text.TextProviderError):
            openai_text._call_openai_text(DESCRIPTION, PRIMARY_MODEL)
