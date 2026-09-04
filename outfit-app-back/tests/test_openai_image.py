import base64
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from app.services import openai_image

# PNG 1x1 transparente válido, en base64 (para que _save_image_b64 no falle al decodificar).
_TINY_PNG_B64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk"
    "+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
)


def _fake_openai_response():
    # Imita response.data[0].b64_json
    return SimpleNamespace(data=[SimpleNamespace(b64_json=_TINY_PNG_B64)])


def _fake_worn_response(*, include_usage=True):
    usage = (
        SimpleNamespace(
            output_tokens=158,
            input_tokens_details=SimpleNamespace(text_tokens=264, image_tokens=1024),
        )
        if include_usage
        else None
    )
    return SimpleNamespace(
        data=[SimpleNamespace(b64_json=_TINY_PNG_B64)],
        usage=usage,
    )


def test_image_generate_call_has_correct_params(tmp_path):
    """
    Blinda el fix del bug real: gpt-image-2 NO acepta response_format (da error 400).
    Verifica que NO se pasa ese parámetro y que SÍ se pasan los correctos.
    """
    fake_client = MagicMock()
    fake_client.images.generate.return_value = _fake_openai_response()

    with (
        patch.object(openai_image, "_get_client", return_value=fake_client),
        patch.object(openai_image, "GENERATED_DIR", tmp_path),
    ):
        result = openai_image.generate_outfit_image(
            "un image_prompt de prueba suficientemente largo"
        )

    # La llamada se hizo exactamente una vez
    fake_client.images.generate.assert_called_once()
    _, kwargs = fake_client.images.generate.call_args

    # NUNCA debe incluir response_format (esto es lo que rompía con error 400)
    assert "response_format" not in kwargs, "gpt-image-2 no acepta response_format"

    # Debe incluir los parámetros correctos
    assert kwargs["model"] == "gpt-image-2"
    assert kwargs["size"] == "1024x1024"
    assert kwargs["quality"] == "low"
    assert kwargs["n"] == 1

    # Y devuelve la ruta pública propia (no la URL de OpenAI), con la imagen en disco
    assert result.url_or_base64.startswith("/images/")
    saved = tmp_path / result.url_or_base64.rsplit("/", 1)[1]
    assert saved.exists()
    assert saved.read_bytes() == base64.b64decode(_TINY_PNG_B64)


def test_image_generate_raises_on_error(tmp_path):
    """
    Si la llamada a OpenAI falla, lanza ImageGenerationError. NUNCA devuelve un
    ImageDetails con un path falso (eso persistía filas basura con coste fantasma
    y consumía el límite de regeneraciones — hallazgo F1 de la auditoría).
    """
    fake_client = MagicMock()
    fake_client.images.generate.side_effect = Exception("boom")

    with (
        patch.object(openai_image, "_get_client", return_value=fake_client),
        patch.object(openai_image, "GENERATED_DIR", tmp_path),
    ):
        with pytest.raises(openai_image.ImageGenerationError):
            openai_image.generate_outfit_image("prompt")

    # y no queda ningún fichero huérfano
    assert list(tmp_path.iterdir()) == []


def test_client_has_bounded_timeout_and_retries():
    """
    Fix F8: sin timeout explícito el SDK usa 600 s y 2 reintentos — workers
    colgados 10 min y hasta 3 imágenes facturadas por 1 pedida.
    """
    with patch.object(openai_image, "OpenAI") as openai_cls:
        openai_image._get_client()
    kwargs = openai_cls.call_args.kwargs
    assert kwargs["timeout"] == 120.0
    assert kwargs["max_retries"] == 1


def test_worn_view_edits_reference_once_without_retry_or_fidelity_override(tmp_path):
    reference = tmp_path / "flat-lay.png"
    reference.write_bytes(base64.b64decode(_TINY_PNG_B64))
    fake_client = MagicMock()
    fake_client.images.edit.return_value = _fake_worn_response()

    with (
        patch.object(openai_image, "_get_client", return_value=fake_client) as client_mock,
        patch.object(openai_image, "GENERATED_DIR", tmp_path),
    ):
        result = openai_image.generate_worn_view_image("worn prompt", reference)

    client_mock.assert_called_once_with(max_retries=0)
    fake_client.images.edit.assert_called_once()
    kwargs = fake_client.images.edit.call_args.kwargs
    assert kwargs["image"].name == str(reference)
    assert kwargs["image"].closed
    assert kwargs["model"] == "gpt-image-2"
    assert kwargs["size"] == "1024x1536"
    assert kwargs["quality"] == "low"
    assert kwargs["n"] == 1
    assert "input_fidelity" not in kwargs
    assert "response_format" not in kwargs
    assert result.cost_estimate == pytest.approx(0.014252)
    saved = tmp_path / result.image.url_or_base64.rsplit("/", 1)[1]
    assert saved.exists()


def test_worn_view_uses_documented_fallback_when_usage_is_missing(tmp_path):
    reference = tmp_path / "flat-lay.png"
    reference.write_bytes(base64.b64decode(_TINY_PNG_B64))
    fake_client = MagicMock()
    fake_client.images.edit.return_value = _fake_worn_response(include_usage=False)

    with (
        patch.object(openai_image, "_get_client", return_value=fake_client),
        patch.object(openai_image, "GENERATED_DIR", tmp_path),
    ):
        result = openai_image.generate_worn_view_image("worn prompt", reference)

    assert result.cost_estimate == 0.015


@pytest.mark.parametrize(
    "public_path",
    ["https://example.com/image.png", "/images/../secret.png", "/images/folder/image.png"],
)
def test_generated_image_path_rejects_non_local_or_nested_paths(tmp_path, public_path):
    with patch.object(openai_image, "GENERATED_DIR", tmp_path):
        with pytest.raises(ValueError, match="ruta|fuera"):
            openai_image.resolve_generated_image_path(public_path)


def test_unverified_price_combo_fails_before_paying(monkeypatch):
    """
    Con una combinación quality/size sin tarifa verificada debe fallar ANTES de
    llamar a la API: fallar después significa pagar una imagen y perder el outfit.
    """
    monkeypatch.setattr(openai_image.get_settings(), "image_size", "1536x1024")
    fake_client = MagicMock()

    with patch.object(openai_image, "_get_client", return_value=fake_client):
        with pytest.raises(ValueError, match="1536x1024"):
            openai_image.generate_outfit_image("prompt")

    fake_client.images.generate.assert_not_called()  # no se pagó nada
