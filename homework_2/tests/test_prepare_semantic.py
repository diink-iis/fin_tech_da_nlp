"""Подготовка модели: CLI проверяется без скачивания и загрузки весов."""
import numpy as np
import pytest

from app.engines import semantic
from scripts import prepare_semantic


@pytest.mark.parametrize("args,expected", [
    ([], semantic.DEFAULT_MODEL),
    (["--model", "models/local-rubert"], "models/local-rubert"),
])
def test_prepares_selected_model_and_reports_dimension(monkeypatch, capsys, args, expected):
    calls = []

    class Engine:
        def __init__(self, model_name):
            calls.append(model_name)

        def embed(self, texts):
            assert len(texts) == 1 and texts[0].strip()
            return np.ones((1, 312), dtype=np.float32)

    monkeypatch.setattr(semantic, "SemanticEngine", Engine)
    assert prepare_semantic.main(args) == 0
    assert calls == [expected]
    output = capsys.readouterr()
    assert expected in output.out and "312" in output.out
    assert output.err == ""


def test_offline_flag_is_set_before_model_creation(monkeypatch):
    monkeypatch.setenv("HF_HUB_OFFLINE", "0")

    class Engine:
        def __init__(self, model_name):
            import os
            assert os.environ["HF_HUB_OFFLINE"] == "1"

        def embed(self, texts):
            return np.ones((1, 3), dtype=np.float32)

    monkeypatch.setattr(semantic, "SemanticEngine", Engine)
    assert prepare_semantic.main(["--offline"]) == 0


def test_model_failure_is_reported_and_returns_nonzero(monkeypatch, capsys):
    class Engine:
        def __init__(self, model_name):
            pass

        def embed(self, texts):
            raise semantic.SemanticError("веса отсутствуют в локальном кэше")

    monkeypatch.setattr(semantic, "SemanticEngine", Engine)
    assert prepare_semantic.main(["--model", "models/missing"]) == 1
    output = capsys.readouterr()
    assert output.out == ""
    assert "models/missing" in output.err and "локальном кэше" in output.err
