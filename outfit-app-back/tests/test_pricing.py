import pytest

from app.pricing import (
    estimate_gpt_image_2_token_cost,
    estimate_image_cost,
    estimate_product_search_cost,
)


def test_verified_costs_1024():
    assert estimate_image_cost("low") == 0.006
    assert estimate_image_cost("medium", "1024x1024") == 0.053
    assert estimate_image_cost("high", "1024x1024") == 0.211
    assert estimate_image_cost("low", "1024x1536") == 0.005


def test_unverified_size_raises():
    with pytest.raises(ValueError, match="1536x1024"):
        estimate_image_cost("low", "1536x1024")


def test_unverified_portrait_quality_raises():
    with pytest.raises(ValueError, match="1024x1536"):
        estimate_image_cost("medium", "1024x1536")


def test_unverified_quality_raises():
    with pytest.raises(ValueError, match="ultra"):
        estimate_image_cost("ultra", "1024x1024")


def test_gpt_image_2_usage_cost_uses_each_token_modality():
    assert estimate_gpt_image_2_token_cost(
        text_input_tokens=1_000,
        image_input_tokens=10_000,
        image_output_tokens=200,
    ) == pytest.approx(0.091)


def test_gpt_image_2_usage_cost_rejects_negative_counts():
    with pytest.raises(ValueError, match="negativos"):
        estimate_gpt_image_2_token_cost(image_input_tokens=-1)


def test_product_search_cost_combines_tool_call_and_reported_usage():
    assert estimate_product_search_cost(
        web_search_calls=2,
        input_tokens=8700,
        output_tokens=420,
    ) == pytest.approx(0.022265)


def test_product_search_cost_rejects_negative_counts():
    with pytest.raises(ValueError, match="negativos"):
        estimate_product_search_cost(
            web_search_calls=1,
            input_tokens=-1,
            output_tokens=0,
        )
