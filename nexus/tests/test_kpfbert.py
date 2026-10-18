from datetime import datetime, timedelta, timezone

import pytest

from nexus.relations.fragments import Fragment
from nexus.relations.kpfbert import load_predictor, model_snippets, render_relation_report

KST = timezone(timedelta(hours=9))


def frag(i):
    return Fragment(f"{i:016x}", "KOSPI:A", "KOSDAQ:B", "가전자", "나소재", f"가전자는 나소재와 문장 {i}", f"N{i}", "제목",
                    "합성일보", datetime(2026, 7, 1, 10, tzinfo=KST), "", None, None)


def test_model_snippets_follow_label_direction_and_threshold():
    frags = [frag(1), frag(2), frag(3), frag(4)]
    preds = [("SUPPLY_BA", 0.91), ("PARTNER", 0.88), ("NONE", 0.99), ("COMPETE", 0.5)]
    out = model_snippets(frags, preds, tau=0.8)
    assert [(s.src, s.dst, s.rel, s.pattern, s.confidence) for s in out] == [
        ("KOSDAQ:B", "KOSPI:A", "SUPPLY", "kpfbert", 0.91), ("KOSPI:A", "KOSDAQ:B", "PARTNER", "kpfbert", 0.88)]
    assert out[0].news_id == "N1" and out[0].snippet == frags[0].text


def test_load_predictor_without_model_folder(tmp_path):
    assert load_predictor(tmp_path / "missing") is None


def test_load_predictor_without_torch(tmp_path, monkeypatch):
    (tmp_path / "config.json").write_text("{}", encoding="utf-8")
    (tmp_path / "tau.json").write_text('{"tau": 0.8}', encoding="utf-8")
    import builtins
    real_import = builtins.__import__
    monkeypatch.setattr(builtins, "__import__",
                        lambda name, *a, **k: (_ for _ in ()).throw(ImportError(name)) if name.startswith("torch")
                        else real_import(name, *a, **k))
    assert load_predictor(tmp_path) is None


def test_report_has_sections():
    text = render_relation_report({"labels": {"PARTNER": 10, "NONE": 90}, "review": None, "tau": 0.8,
                                   "test_precision": 0.9, "test_recall": 0.5, "rule_precision": 0.95,
                                   "rule_recall": 0.2, "per_class": {}, "confusion": [], "relations_before": 37,
                                   "relations_after": 60, "companies_after": 35})
    for head in ("# NEXUS 관계 판별기 검증 보고서", "## 시험 구간", "사람 검토 대기", "37 → 60"):
        assert head in text


def test_report_states_the_80_percent_bar_when_rejected():
    text = render_relation_report({"labels": {}, "review": None, "tau": 0.9, "test_precision": 0.7,
                                   "test_recall": 0.5, "rule_precision": 0.9, "rule_recall": 0.2, "per_class": {},
                                   "confusion": [], "applied": False})
    assert "시험 정확도가 80% 미만이라 모델을 적용하지 않았다" in text


def test_training_runs_on_a_tiny_model(tmp_path):
    pytest.importorskip("torch")
    transformers = pytest.importorskip("transformers")
    from nexus.relations.kpfbert import train_relation_model
    base = tmp_path / "tiny"
    vocab = ["[PAD]", "[UNK]", "[CLS]", "[SEP]", "[MASK]", "@", "#", "가전자", "나소재", "협력", "공급", "문장"]
    base.mkdir()
    (base / "vocab.txt").write_text("\n".join(vocab), encoding="utf-8")
    transformers.BertTokenizer(str(base / "vocab.txt")).save_pretrained(base)
    transformers.BertForSequenceClassification(transformers.BertConfig(
        vocab_size=len(vocab), hidden_size=16, num_hidden_layers=1, num_attention_heads=2, intermediate_size=32,
        num_labels=7)).save_pretrained(base)
    data = [(frag(i), "PARTNER" if i % 2 else "NONE") for i in range(16)]
    stats = train_relation_model(data[:10], data[10:13], data[13:], tmp_path / "out", base=str(base), revision=None,
                                 epochs=1, batch=4, threads=1)
    assert 0.5 <= stats["tau"] <= 0.95 and (tmp_path / "out" / "tau.json").is_file()
    predictor, tau = load_predictor(tmp_path / "out", threads=1)
    assert len(predictor([frag(1), frag(2)])) == 2


def test_class_weights_are_softened_inverse_frequency():
    from nexus.relations.kpfbert import class_weights
    w = class_weights({"NONE": 900, "PARTNER": 100}, ("PARTNER", "NONE", "COMPETE"))
    # sqrt of inverse frequency: rare classes count more, but not so much that the model over-predicts relations
    assert round(w[0] / w[1], 6) == 3.0          # sqrt(900 / 100)
    assert w[2] == 1.0                            # absent class
