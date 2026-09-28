import pytest
from pydantic import ValidationError

from deerflow.config.model_config import ModelConfig


def _make_model(**overrides) -> ModelConfig:
    return ModelConfig(
        name="openai-responses",
        display_name="OpenAI Responses",
        description=None,
        use="langchain_openai:ChatOpenAI",
        model="gpt-5",
        **overrides,
    )


def test_responses_api_fields_are_declared_in_model_schema():
    assert "use_responses_api" in ModelConfig.model_fields
    assert "output_version" in ModelConfig.model_fields


def test_responses_api_fields_round_trip_in_model_dump():
    config = _make_model(
        api_key="$OPENAI_API_KEY",
        use_responses_api=True,
        output_version="responses/v1",
    )

    dumped = config.model_dump(exclude_none=True)

    assert dumped["use_responses_api"] is True
    assert dumped["output_version"] == "responses/v1"


def test_context_window_round_trips_when_positive():
    assert _make_model(context_window=128_000).context_window == 128_000


@pytest.mark.parametrize("context_window", [0, -1])
def test_context_window_rejects_non_positive_capacity(context_window):
    with pytest.raises(ValidationError, match="context_window"):
        _make_model(context_window=context_window)


def test_numeric_fields_from_environment_placeholders_become_numbers():
    # AppConfig.resolve_env_variables hands every $VAR over as a string. ChatOpenAI coerces max_tokens itself but
    # keeps a string request_timeout (its type admits Any), which then fails every request as a connection error.
    config = _make_model(request_timeout="300", max_tokens="32000")

    assert config.request_timeout == 300.0 and type(config.request_timeout) is float
    assert config.max_tokens == 32000 and type(config.max_tokens) is int
    dumped = config.model_dump(exclude_none=True)
    assert dumped["request_timeout"] == 300.0 and dumped["max_tokens"] == 32000


def test_unset_numeric_fields_stay_out_of_the_constructor_arguments():
    dumped = _make_model().model_dump(exclude_none=True)

    assert "request_timeout" not in dumped and "max_tokens" not in dumped


@pytest.mark.parametrize("value", ["0", "-1", "nan", "inf", "soon", 0, -5.0])
def test_request_timeout_rejects_non_positive_or_non_finite_seconds(value):
    with pytest.raises(ValidationError, match="request_timeout"):
        _make_model(request_timeout=value)


@pytest.mark.parametrize("value", ["1.5", "32k", "many", "\uff13\uff12\uff10\uff10\uff10"])
def test_max_tokens_rejects_what_is_not_a_whole_number(value):
    with pytest.raises(ValidationError, match="max_tokens"):
        _make_model(max_tokens=value)
