from __future__ import annotations

import shutil

import pytest
import yaml
from pydantic import ValidationError

from discovery.config import DEFAULT_CONFIG_DIR, PROJECT_ROOT, load_config
from discovery.db import normalize_url, redact_url


def test_config_loads(cfg):
    llm = cfg.settings.llm
    assert llm.provider == "groq"
    assert llm.small_model and llm.large_model
    assert llm.max_concurrency >= 1
    assert llm.rate_limit_for("some/unknown-model") == llm.rate_limits["default"]
    assert cfg.settings.sources.google_community.seconds_between_pages >= 2.0


def test_every_configured_model_has_pricing(cfg):
    llm = cfg.settings.llm
    assert {llm.small_model, llm.large_model} <= set(llm.pricing)


def test_default_scoring_weights_match_decision_d8(cfg):
    assert cfg.scoring.weights.model_dump() == {
        "frequency": 0.20,
        "severity": 0.20,
        "strategic_fit": 0.20,
        "evidence_quality": 0.15,
        "product_leverage": 0.15,
        "research_value": 0.10,
    }


def test_weights_must_sum_to_one(tmp_path):
    cfg_dir = tmp_path / "config"
    shutil.copytree(DEFAULT_CONFIG_DIR, cfg_dir)
    path = cfg_dir / "scoring_weights.yaml"
    data = yaml.safe_load(path.read_text())
    data["weights"]["frequency"] = 0.5
    path.write_text(yaml.safe_dump(data))
    with pytest.raises(ValidationError, match="sum to 1.0"):
        load_config(cfg_dir)


def test_unknown_setting_is_rejected(tmp_path):
    cfg_dir = tmp_path / "config"
    shutil.copytree(DEFAULT_CONFIG_DIR, cfg_dir)
    path = cfg_dir / "settings.yaml"
    data = yaml.safe_load(path.read_text())
    data["llm"]["temprature"] = 0.2
    path.write_text(yaml.safe_dump(data))
    with pytest.raises(ValidationError, match="temprature"):
        load_config(cfg_dir)


def test_database_url_defaults_to_local_sqlite(cfg):
    assert cfg.database_url == f"sqlite:///{PROJECT_ROOT / 'data' / 'discovery.db'}"


def test_database_url_from_env(cfg, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:secret@host/db")
    assert cfg.database_url == "postgresql://u:secret@host/db"


@pytest.mark.parametrize(
    "url",
    ["postgres://u:p@h/db", "postgresql://u:p@h/db", "postgresql+psycopg://u:p@h/db"],
)
def test_postgres_urls_use_psycopg3(url):
    assert normalize_url(url) == "postgresql+psycopg://u:p@h/db"


def test_redact_url_hides_password():
    assert "secret" not in redact_url("postgresql://u:secret@h/db")
