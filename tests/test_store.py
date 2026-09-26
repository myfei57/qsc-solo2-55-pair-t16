"""文件型持久化：原子文档、分段流水与用量统计。"""

from __future__ import annotations

import json

import pytest

from cspplant.errors import StorageError
from cspplant.store.jsonfile import JsonStore
from cspplant.store.journal import JsonlJournal
from cspplant.store.metrics import StoreMetrics


def test_json_store_round_trip_and_nested_id(tmp_path):
    store = JsonStore(str(tmp_path))
    path = store.write("field/irradiance", {"dni_w_m2": 812.5})
    assert path.endswith("irradiance.json")
    assert store.exists("field/irradiance") is True
    assert store.read("field/irradiance")["dni_w_m2"] == pytest.approx(812.5)
    assert store.read("missing/doc") is None
    assert store.list_ids() == ["field/irradiance"]
    assert store.remove("field/irradiance") is True
    assert store.remove("field/irradiance") is False


def test_json_store_rejects_corrupt_document(tmp_path):
    store = JsonStore(str(tmp_path))
    store.write("broken", {"ok": True})
    (tmp_path / "broken.json").write_text("{not json", encoding="utf-8")
    with pytest.raises(StorageError):
        store.read("broken")


def test_journal_rolls_segments_and_replays(tmp_path):
    journal = JsonlJournal(str(tmp_path), "audit", segment_limit=2)
    for index in range(5):
        journal.append({"index": index})
    assert journal.count() == 5
    assert journal.active_segment() == 2
    assert len(journal.segments()) == 3
    assert [item["index"] for item in journal.read_segment(1)] == [2, 3]
    assert len(journal.read_all()) == 5
    assert journal.read_segment(99) == []


def test_journal_resumes_count_after_reopen(tmp_path):
    first = JsonlJournal(str(tmp_path), "audit", segment_limit=2)
    first.append({"index": 0})
    first.append({"index": 1})
    second = JsonlJournal(str(tmp_path), "audit", segment_limit=2)
    assert second.count() == 2
    second.append({"index": 2})
    assert second.active_segment() == 1
    assert len(second.read_all()) == 3


def test_store_metrics_counts_files(tmp_path):
    store = JsonStore(str(tmp_path))
    store.write("a", {"x": 1})
    store.write("nested/b", {"y": 2})
    journal = JsonlJournal(str(tmp_path), "audit", segment_limit=8)
    journal.append({"x": 1})
    metrics = StoreMetrics(str(tmp_path))
    summary = metrics.summary()
    assert summary["files"] == 3
    assert summary["bytes"] > 0
    assert summary["audit_segments"] == 1
    assert metrics.usage().files == 3


def test_journal_records_are_json_lines(tmp_path):
    journal = JsonlJournal(str(tmp_path), "audit", segment_limit=8)
    journal.append({"asset": "m021", "action": "track"})
    line = (tmp_path / "audit-00000.jsonl").read_text(encoding="utf-8").strip()
    assert json.loads(line)["asset"] == "m021"
