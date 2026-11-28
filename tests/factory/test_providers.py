"""Proveedores de modelos: credenciales, listado dinamico, fabrica de LLMs, probe y temperatura.

Sin red real: httpx.MockTransport para los listados y un BaseLlm de prueba para el probe.
FACTORY_HOME apunta a un temporal (conftest).
"""

from __future__ import annotations

import json
import os

import httpx
import pytest
from google.adk.models.base_llm import BaseLlm
from google.adk.models.llm_response import LlmResponse
from google.genai import types

from factory_app import credentials, probe, providers
from factory_app.models import build_llm
from factory_app.orchestrator import _is_transient
from factory_app.roles import FactoryError

ALL_VARS = [v for keys, url in credentials.ENV_VARS.values() for v in (*keys, url) if v]


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for var in ALL_VARS:
        monkeypatch.delenv(var, raising=False)


def _mock(handler):
    providers.transport = httpx.MockTransport(handler)


@pytest.fixture(autouse=True)
def _reset_transport():
    yield
    providers.transport = None


# ---------------------------------------------------------------- credenciales
def test_saved_key_is_masked_and_never_returned():
    st = credentials.save("anthropic", api_key="sk-ant-abcdef123456")  # pragma: allowlist secret
    assert st == {"has_key": True, "key_source": "file", "last4": "3456", "url": None,
                  "url_source": "none"}
    assert "sk-ant" not in json.dumps(credentials.status("anthropic"))
    assert credentials.get_key("anthropic") == "sk-ant-abcdef123456"
    assert (credentials.home() / "credentials.json").is_file()


def test_real_env_var_wins_and_cannot_be_changed_from_the_ui(monkeypatch):
    credentials.save("openai", api_key="sk-file-0000aaaa")  # pragma: allowlist secret
    monkeypatch.setenv("OPENAI_API_KEY", "sk-env-1111bbbb")
    assert credentials.get_key("openai") == "sk-env-1111bbbb"
    assert credentials.status("openai")["key_source"] == "env"
    with pytest.raises(FactoryError, match="variable de entorno"):
        credentials.save("openai", api_key="sk-new-2222cccc")  # pragma: allowlist secret
    with pytest.raises(FactoryError, match="variable de entorno"):
        credentials.delete_key("openai")


def test_file_key_is_applied_to_env_and_removed_on_delete():
    credentials.save("gemini", api_key="AIzaFAKEKEY12345")  # pragma: allowlist secret
    assert os.environ["GEMINI_API_KEY"] == "AIzaFAKEKEY12345"  # pragma: allowlist secret
    # lo que pusimos nosotros no cuenta como "entorno real"
    assert credentials.status("gemini")["key_source"] == "file"
    st = credentials.delete_key("gemini")
    assert st["has_key"] is False and "GEMINI_API_KEY" not in os.environ


@pytest.mark.parametrize("bad", ["", "   ", "sk with space"])
def test_rejects_empty_or_spaced_keys(bad):
    with pytest.raises(FactoryError):
        credentials.save("openai", api_key=bad)


def test_urls_must_be_http_and_have_defaults():
    assert credentials.get_url("ollama") == "http://localhost:11434"
    with pytest.raises(FactoryError, match="http"):
        credentials.save("openai_compatible", url="file:///etc/passwd")
    st = credentials.save("openai_compatible", url="http://127.0.0.1:1234/v1/")
    assert st["url"] == "http://127.0.0.1:1234/v1"
    with pytest.raises(FactoryError):
        credentials.save("ollama", api_key="nope")  # pragma: allowlist secret (ollama no usa clave)


def test_unknown_provider_is_rejected():
    with pytest.raises(FactoryError, match="desconocido"):
        credentials.status("mistral")


# ---------------------------------------------------------------- listado dinamico
def test_gemini_lists_only_generate_content_models_with_pagination():
    calls = []

    def handler(request: httpx.Request):
        calls.append(request)
        assert request.headers["x-goog-api-key"] == "AIzaFAKEKEY12345"
        assert "key=" not in str(request.url)  # la clave no viaja en la URL
        if "pageToken" not in str(request.url):
            return httpx.Response(200, json={"nextPageToken": "p2", "models": [
                {"name": "models/gemini-2.5-flash", "displayName": "Gemini 2.5 Flash",
                 "supportedGenerationMethods": ["generateContent"]},
                {"name": "models/text-embedding-004",
                 "supportedGenerationMethods": ["embedContent"]},
            ]})
        return httpx.Response(200, json={"models": [
            {"name": "models/gemini-2.5-pro", "supportedGenerationMethods": ["generateContent"]},
        ]})

    credentials.save("gemini", api_key="AIzaFAKEKEY12345")  # pragma: allowlist secret
    _mock(handler)
    ids = [m["id"] for m in providers.list_models("gemini")]
    assert ids == ["gemini-2.5-flash", "gemini-2.5-pro"] and len(calls) == 2
    providers.list_models("gemini")  # cache: sin otra llamada
    assert len(calls) == 2


def test_anthropic_paginates_and_sends_version_header():
    def handler(request: httpx.Request):
        assert request.headers["x-api-key"] == "sk-ant-abcdef123456"
        assert request.headers["anthropic-version"] == "2023-06-01"
        if "after_id" not in str(request.url):
            return httpx.Response(200, json={"data": [{"id": "claude-a", "display_name": "A"}],
                                             "has_more": True, "last_id": "claude-a"})
        return httpx.Response(200, json={"data": [{"id": "claude-b"}], "has_more": False})

    credentials.save("anthropic", api_key="sk-ant-abcdef123456")  # pragma: allowlist secret
    _mock(handler)
    assert [m["id"] for m in providers.list_models("anthropic")] == ["claude-a", "claude-b"]


def test_openai_filters_non_chat_models():
    _mock(lambda r: httpx.Response(200, json={"data": [
        {"id": "gpt-4.1"}, {"id": "text-embedding-3-small"}, {"id": "whisper-1"},
        {"id": "tts-1"}, {"id": "dall-e-3"}, {"id": "omni-moderation-latest"},
    ]}))
    credentials.save("openai", api_key="sk-test-00001111")  # pragma: allowlist secret
    assert [m["id"] for m in providers.list_models("openai")] == ["gpt-4.1"]


def test_ollama_and_openai_compatible_use_the_configured_url():
    seen = []

    def handler(request: httpx.Request):
        seen.append(str(request.url))
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": "llama3.2:3b"}]})
        assert "authorization" not in request.headers  # clave opcional
        return httpx.Response(200, json={"data": [{"id": "local-model"}]})

    _mock(handler)
    assert providers.list_models("ollama")[0]["id"] == "llama3.2:3b"
    credentials.save("openai_compatible", url="http://127.0.0.1:1234")
    assert providers.list_models("openai_compatible")[0]["id"] == "local-model"
    assert seen == ["http://localhost:11434/api/tags", "http://127.0.0.1:1234/v1/models"]


@pytest.mark.parametrize(
    ("response", "kind"),
    [
        (httpx.Response(401, json={}), "INVALID_KEY"),
        (httpx.Response(403, json={}), "INVALID_KEY"),
        (httpx.Response(500, text="boom"), "ERROR"),
        (httpx.Response(200, text="<html>"), "ERROR"),
    ],
)
def test_test_connection_reports_problems(response, kind):
    _mock(lambda r: response)
    credentials.save("openai", api_key="sk-test-00001111")  # pragma: allowlist secret
    out = providers.test_connection("openai")
    assert out["ok"] is False and out["kind"] == kind


def test_test_connection_unreachable_and_missing_key():
    def handler(request):
        raise httpx.ConnectError("refused")

    _mock(handler)
    assert providers.test_connection("ollama")["kind"] == "UNREACHABLE"
    assert providers.test_connection("anthropic")["kind"] == "NO_KEY"


def test_test_connection_ok_counts_models():
    _mock(lambda r: httpx.Response(200, json={"data": [{"id": "m1"}, {"id": "m2"}]}))
    credentials.save("openai_compatible", url="http://127.0.0.1:9")
    assert providers.test_connection("openai_compatible") == {
        "ok": True, "kind": "OK", "message": "2 modelos", "models": 2}


# ---------------------------------------------------------------- temperatura
@pytest.mark.parametrize(
    ("provider", "value", "supported", "expected"),
    [
        ("gemini", 0.2, True, 0.2),
        ("gemini", 1.8, None, 1.8),
        ("anthropic", 1.5, True, 1.0),   # recortada al rango nativo 0-1
        ("openai", 2.0, True, 2.0),
        ("openai", 0.7, False, None),    # el probe detecto que no la acepta: no se envia
        ("ollama", None, True, None),
    ],
)
def test_effective_temperature(provider, value, supported, expected):
    assert providers.effective_temperature(provider, value, supported) == expected


# ---------------------------------------------------------------- fabrica de LLMs
def test_build_llm_per_provider():
    from google.adk.models.anthropic_llm import AnthropicLlm
    from google.adk.models.google_llm import Gemini
    from google.adk.models.lite_llm import LiteLlm

    assert isinstance(build_llm("gemini", "gemini-2.5-flash"), Gemini)
    credentials.save("anthropic", api_key="sk-ant-abcdef123456")  # pragma: allowlist secret
    assert isinstance(build_llm("anthropic", "claude-x"), AnthropicLlm)
    credentials.save("openai", api_key="sk-test-00001111")  # pragma: allowlist secret
    llm = build_llm("openai", "gpt-4.1")
    assert isinstance(llm, LiteLlm) and llm.model == "openai/gpt-4.1"
    assert build_llm("ollama", "llama3").model == "ollama_chat/llama3"
    credentials.save("openai_compatible", url="http://127.0.0.1:1234")
    assert build_llm("openai_compatible", "local").model == "openai/local"


def test_litellm_tool_calls_have_their_dependencies():
    # litellm importa `mcp` en cada llamada con tools (sin declararlo); sin el paquete
    # OpenAI/Ollama/compatibles fallan en el primer turno de cualquier agente.
    from litellm.responses.mcp.chat_completions_handler import acompletion_with_mcp

    assert callable(acompletion_with_mcp)


def test_build_llm_fails_clearly_without_key_or_model():
    with pytest.raises(FactoryError, match="API key de Anthropic"):
        build_llm("anthropic", "claude-x")
    with pytest.raises(FactoryError, match="falta el modelo"):
        build_llm("openai", " ")
    with pytest.raises(FactoryError, match="desconocido"):
        build_llm("mistral", "m")


# ---------------------------------------------------------------- probe
class StubLlm(BaseLlm):
    model: str = "stub"
    mode: str = "tool"  # tool | text | error | no_temperature
    error: str = ""

    async def generate_content_async(self, llm_request, stream=False):  # noqa: ARG002
        if self.mode == "error":
            raise RuntimeError(self.error)
        if self.mode == "no_temperature" and llm_request.config.temperature is not None:
            raise RuntimeError("Unsupported value: 'temperature' does not support 0.2")
        if self.mode == "text":
            part = types.Part(text="OK")
        else:
            part = types.Part(function_call=types.FunctionCall(name="ping", args={}))
        yield LlmResponse(content=types.Content(role="model", parts=[part]))


@pytest.mark.parametrize(
    ("llm", "status", "temp"),
    [
        (StubLlm(), "OK", True),
        (StubLlm(mode="text"), "NO_TOOLS", True),
        (StubLlm(mode="no_temperature"), "OK", False),
        (StubLlm(mode="error", error="Error code: 401 invalid x-api-key"), "INVALID_KEY", None),
        (StubLlm(mode="error", error="429 RESOURCE_EXHAUSTED quota"), "QUOTA", None),
        (StubLlm(mode="error", error="403 PERMISSION_DENIED for model"), "NO_ACCESS", None),
        (StubLlm(mode="error", error="model_not_found: gpt-9"), "NO_ACCESS", None),
        (StubLlm(mode="error", error="this model does not support tools"), "NO_TOOLS", None),
        (StubLlm(mode="error", error="something odd"), "ERROR", None),
    ],
)
def test_probe_classifies_and_caches(llm, status, temp):
    before = probe.version
    out = probe.probe("openai", "gpt-x", llm=llm)
    assert out["status"] == status and out["temperature_supported"] is temp
    assert probe.result("openai", "gpt-x")["status"] == status
    assert probe.version >= before + 1 and not probe.running
    assert probe.temperature_supported("openai", "gpt-x") is temp


def test_probe_redacts_keys_from_messages():
    credentials.save("openai", api_key="sk-proj-SECRETSECRET99")  # pragma: allowlist secret
    out = probe.probe("openai", "gpt-x", llm=StubLlm(
        mode="error", error="401 Incorrect API key provided: sk-proj-SECRETSECRET99"))
    assert "SECRETSECRET" not in out["message"]
    assert "SECRETSECRET" not in (credentials.home() / probe.FILE).read_text()


def test_probe_without_key_is_invalid_key():
    assert probe.probe("anthropic", "claude-x")["status"] == "INVALID_KEY"


def test_a_real_quota_failure_replaces_a_stored_ok():
    import time

    probe._store("gemini", "g-x", {"provider": "gemini", "model": "g-x", "status": "OK",
                                   "temperature_supported": True, "at": time.time()})
    before = probe.version
    out = probe.record_runtime_failure("gemini", "g-x", "intake",
                                       RuntimeError("429 RESOURCE_EXHAUSTED quota"))
    assert out["status"] == "QUOTA" and out["source"] == "run"
    assert "fase real (intake" in out["message"]
    assert probe.result("gemini", "g-x")["status"] == "QUOTA"
    assert probe.temperature_supported("gemini", "g-x") is True  # lo que se sabia se conserva
    assert probe.version > before  # la UI se entera por SSE


@pytest.mark.real_probe
def test_a_model_that_failed_in_a_run_is_probed_again(monkeypatch):
    import time

    done = []
    monkeypatch.setattr(probe, "probe", lambda p, m: done.append((p, m)))
    probe._store("gemini", "g-y", {"status": "OK", "at": time.time()})
    probe.record_runtime_failure("gemini", "g-y", "plan", RuntimeError("429 quota"))
    assert probe.probe_in_background([("gemini", "g-y")]) == ["gemini:g-y"]


# ---------------------------------------------------------------- backoff
@pytest.mark.parametrize(
    ("message", "transient"),
    [
        ("anthropic.RateLimitError: 429", True),
        ("Error code: 529 overloaded_error", True),
        ("litellm.ServiceUnavailableError: down", True),
        ("litellm.RateLimitError: slow down", True),
        ("litellm.AuthenticationError: invalid api key", False),
        ("litellm.NotFoundError: model not found", False),
    ],
)
def test_transient_markers_cover_other_providers(message, transient):
    assert _is_transient(RuntimeError(message)) is transient


@pytest.mark.real_probe
def test_background_probe_reuses_only_recent_ok_results(monkeypatch):
    done = []
    monkeypatch.setattr(probe, "probe", lambda p, m: done.append((p, m)))
    probe._store("ollama", "ok-model", {"status": "OK", "at": __import__("time").time()})
    probe._store("ollama", "bad-model", {"status": "ERROR", "at": __import__("time").time()})
    probe._store("ollama", "old-model", {"status": "OK", "at": 0})
    launched = probe.probe_in_background(
        [("ollama", "ok-model"), ("ollama", "bad-model"), ("ollama", "old-model"),
         ("ollama", "new-model")])
    assert launched == ["ollama:bad-model", "ollama:old-model", "ollama:new-model"]
