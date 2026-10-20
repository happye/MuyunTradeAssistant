"""T04: shared configuration reaches real consumers without the CLI wrapper.

All consumers are imported before patching by-value dependencies. Constructors,
REPL and source probes are substituted; no external calls or account writes run.
"""
import importlib
from types import SimpleNamespace

import openai
import pytest

import src.config as config_module
import src.cli.main as cli
import src.chat.agent as chat_agent
import src.chat.tools as chat_tools
import src.data.portfolio as portfolio
import src.core.backtest_engine as backtest
from src.chat import __main__ as chat_main
from src.core import claim_llm_extractor, shadow_diff
from src.core.benzong import auto_scorer
from src.data import source_check
from src.rag import service as rag
from src.tui.app import MuyunTUI

web = importlib.import_module("src.web.app")


@pytest.fixture
def shared_config(monkeypatch):
    calls = []
    config = {"ai": {"provider": "deepseek", "request_timeout": 17,
                     "deepseek": {"api_key": "test-only", "model": "test-model"}},
              "rag": {"enabled": False}, "entry_exit": {"enabled": True},
              "fusion": {"shadow_capture": True}}

    def load():
        calls.append("shared")
        return config

    def forbidden(*args, **kwargs):
        pytest.fail("consumer called CLI configuration wrapper")

    monkeypatch.setattr(config_module, "load_config", load)
    monkeypatch.setattr(cli, "load_config", forbidden)
    return config, calls


@pytest.mark.parametrize("consumer", [auto_scorer._build_ai_client,
                                     claim_llm_extractor.LLMClaimExtractor._build_client])
def test_client_builders_use_shared_config(monkeypatch, shared_config, consumer):
    config, calls = shared_config
    constructed = []
    client = object()

    def make_client(**kwargs):
        constructed.append(kwargs)
        return client

    monkeypatch.setattr(openai, "OpenAI", make_client)
    assert consumer(None) == (client, "test-model")
    assert calls == ["shared"]
    assert len(constructed) == 1
    assert constructed[0]["timeout"] == config["ai"]["request_timeout"]
    assert constructed[0]["api_key"] == "test-only"


@pytest.mark.parametrize("ui", ["web", "tui"])
def test_ui_initialization_passes_shared_config(monkeypatch, shared_config, ui):
    config, calls = shared_config
    initialized = []
    manager = object()
    monkeypatch.setattr(chat_tools, "init_engines", initialized.append)
    monkeypatch.setattr(portfolio, "PortfolioManager", lambda: manager)
    if ui == "web":
        monkeypatch.setattr(web, "_scanner", None)
        monkeypatch.setattr(web, "_orchestrator", None)
        monkeypatch.setattr(web, "_portfolio", None)
        assert web._init_engines() is True
        assert web._portfolio is manager
    else:
        app = MuyunTUI()
        app._init_engines()
        assert app._engines_ready is True
        assert app._portfolio is manager
        assert app._config is config
    assert calls == ["shared"]
    assert len(initialized) == 1 and initialized[0] is config


def test_web_backtest_retains_normalizer_and_shared_config(monkeypatch, shared_config):
    config, calls = shared_config
    received = []

    class Engine:
        def __init__(self, **kwargs):
            received.append(kwargs)

        def run(self):
            return SimpleNamespace(total_return_pct=3, benchmark_return_pct=2, total_trades=1)

    monkeypatch.setattr(web, "_init_engines", lambda: True)
    monkeypatch.setattr(backtest, "BacktestEngine", Engine)
    result = web._backtest_work("600519", "2025-01-01", "2025-02-01", 10000)
    assert calls == ["shared"]
    assert len(received) == 1
    assert received[0]["stock_code"] == cli.normalize_stock_code("600519")
    assert received[0]["entry_exit_config"] is config["entry_exit"]
    assert result["total_return"] == 3 and result["trades"] == 1


def test_rag_factory_passes_shared_subsection(monkeypatch, shared_config):
    config, calls = shared_config
    received = []
    service = SimpleNamespace(is_available=lambda: True)
    monkeypatch.setattr(rag, "_rag_service_singleton", None)
    monkeypatch.setattr(rag, "_rag_service_failed", False)

    def make_service(cfg):
        received.append(cfg)
        return service

    monkeypatch.setattr(rag, "RAGService", make_service)
    assert rag.get_rag_service(auto_initialize=False) is service
    assert calls == ["shared"]
    assert len(received) == 1 and received[0] is config["rag"]


def test_chat_entry_passes_shared_config(monkeypatch, shared_config):
    config, calls = shared_config
    received = []
    # main removes proxy keys; restore each process environment value afterwards.
    for key in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy",
                "ALL_PROXY", "all_proxy", "NO_PROXY", "no_proxy", "PYTHONIOENCODING"):
        monkeypatch.setenv(key, "test-only")
    monkeypatch.setattr(chat_agent, "run_chat_repl", received.append)
    assert chat_main.main() == 0
    assert calls == ["shared"]
    assert len(received) == 1 and received[0] is config


def test_shadow_resolves_shared_capture_switch(shared_config):
    _, calls = shared_config
    resolution = shadow_diff._load_capture_switch(None)
    assert calls == ["shared"]
    assert resolution is not None
    assert resolution.effective_mode != "legacy_only"


def test_source_ai_probes_read_shared_config(monkeypatch, shared_config):
    _, calls = shared_config
    requests = []
    monkeypatch.setattr(source_check, "fix_curl_ssl_paths", lambda: True)
    monkeypatch.setenv("TQDM_DISABLE", "1")

    def complete(**kwargs):
        requests.append(kwargs)
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="ok"))])

    monkeypatch.setattr(openai, "OpenAI", lambda **kwargs: SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=complete))))

    def probe(name, fn, **kwargs):
        # Execute only the configured AI consumer, with a fake SDK client.
        detail = fn() if name == "AI-DeepSeek" else "skipped"
        return {"name": name, "ok": True, "detail": detail}

    monkeypatch.setattr(source_check, "_probe", probe)
    monkeypatch.setattr(source_check, "_probe_t", probe)
    result = source_check.check_all_sources()
    assert calls == ["shared"]
    assert len(requests) == 1 and requests[0]["model"] == "test-model"
    row = next(row for row in result["sources"] if row["name"] == "AI-DeepSeek")
    assert row["detail"] == "model=test-model resp='ok'"
