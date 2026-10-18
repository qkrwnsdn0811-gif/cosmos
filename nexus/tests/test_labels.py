import json

from nexus.relations.fragments import Fragment
from nexus.relations.labels import (LABELS, choose_threshold, flip_label, marked_text, read_labels, split_by_pair,
                                    write_review, write_sample)


def frag(text, a="삼성전기", b="솔브레인", fid="0" * 16):
    return Fragment(fid, "KOSPI:009150", "KOSDAQ:357780", a, b, text, "N1", "", "", None, "", None, None)


def test_marks_first_standalone_mentions():
    f = frag("삼성전기는 솔브레인과 협력했고 삼성전기 측은 만족했다.")
    assert marked_text(f) == "@ 삼성전기 @는 # 솔브레인 #과 협력했고 삼성전기 측은 만족했다."
    assert marked_text(f, swap=True) == "# 삼성전기 #는 @ 솔브레인 @과 협력했고 삼성전기 측은 만족했다."


def test_marks_skip_names_inside_other_words():
    f = frag("신규 테스트를 마친 테스는 디아이와 경쟁한다.", a="테스", b="디아이")
    assert marked_text(f) == "신규 테스트를 마친 @ 테스 @는 # 디아이 #와 경쟁한다."


def test_flip_label():
    assert [flip_label(x) for x in LABELS] == ["SUPPLY_BA", "SUPPLY_AB", "INVEST_BA", "INVEST_AB", "PARTNER", "COMPETE", "NONE"]


def test_read_labels_filters_bad_lines(tmp_path):
    p = tmp_path / "labels.jsonl"
    p.write_text("\n".join([json.dumps({"fragment_id": "a", "label": "PARTNER"}),
                            json.dumps({"fragment_id": "b", "label": "FRIEND"}),
                            json.dumps({"fragment_id": "zzz", "label": "NONE"}),
                            "{broken"]), encoding="utf-8")
    labels, dropped = read_labels(p, {"a", "b"})
    assert labels == {"a": "PARTNER"} and dropped == 3


def test_sample_and_review_files(tmp_path):
    f = frag("삼성전기는 솔브레인과 협력했다.")
    names = {"KOSPI:009150": "삼성전기", "KOSDAQ:357780": "솔브레인"}
    write_sample([f], tmp_path / "s.jsonl", names)
    assert json.loads((tmp_path / "s.jsonl").read_text(encoding="utf-8")) == \
        {"fragment_id": f.fragment_id, "a": "삼성전기", "b": "솔브레인", "text": f.text}
    write_review({f.fragment_id: "PARTNER"}, {f.fragment_id: f}, tmp_path / "r.csv", names, n=100)
    assert (tmp_path / "r.csv").read_text(encoding="utf-8-sig").splitlines()[0] == "fragment_id,a,b,text,label,ok"


def test_split_keeps_each_pair_in_one_part():
    ids = {(f"A{i}", f"B{i}"): [f"{i}-{j}" for j in range(i % 5 + 1)] for i in range(40)}
    train, valid, test = split_by_pair(ids)
    parts = [set(train), set(valid), set(test)]
    for pair_ids in ids.values():
        assert sum(1 for part in parts if set(pair_ids) & part) == 1
    assert len(train) > len(valid) > 0 and len(test) > 0


def test_choose_threshold():
    preds = [("PARTNER", 0.55)] * 10 + [("PARTNER", 0.9)] * 10 + [("NONE", 0.99)] * 5
    gold = ["NONE"] * 10 + ["PARTNER"] * 10 + ["NONE"] * 5
    assert choose_threshold(preds, gold) == 0.6                       # 0.55짜리 오답이 빠지는 첫 값
    assert choose_threshold([("NONE", 0.9)] * 5, ["PARTNER"] * 5) == 0.95   # 관계 판별이 하나도 없음
