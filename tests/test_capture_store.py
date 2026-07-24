"""Capture-store round-trip tests (save/list/load full untruncated responses)."""

from app.adapters.outbound import capture_store


def test_save_list_load_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(capture_store, "_captures_dir", lambda: tmp_path)

    request_body = {"messages": [{"role": "user", "content": "Design an AWS 3-tier app"}]}
    response = {"status": "success", "xml": "<mxfile>...</mxfile>", "logs": []}

    capture_id = capture_store.save_capture(request_body, response)
    assert capture_id is not None

    listed = capture_store.list_captures()
    assert len(listed) == 1
    assert listed[0]["id"] == capture_id
    assert listed[0]["prompt_snippet"] == "Design an AWS 3-tier app"

    loaded = capture_store.load_capture(capture_id)
    assert loaded is not None
    assert loaded["response"]["xml"] == "<mxfile>...</mxfile>"
    assert loaded["request"] == request_body


def test_load_missing_capture_returns_none(tmp_path, monkeypatch):
    monkeypatch.setattr(capture_store, "_captures_dir", lambda: tmp_path)
    assert capture_store.load_capture("does-not-exist") is None


def test_load_capture_rejects_path_traversal(tmp_path, monkeypatch):
    captures_dir = tmp_path / "captures"
    captures_dir.mkdir()
    monkeypatch.setattr(capture_store, "_captures_dir", lambda: captures_dir)
    outside_secret = tmp_path / "secret.json"
    outside_secret.write_text('{"leaked": true}', encoding="utf-8")
    # Sanitization strips path-traversal characters entirely rather than
    # resolving them, so this can never escape captures_dir to read the
    # sibling file — it looks for (and won't find) "secret.json" inside
    # captures_dir itself.
    assert capture_store.load_capture("../secret") is None
    assert capture_store.load_capture("../../secret") is None


def test_save_capture_never_raises_on_failure(monkeypatch):
    def boom():
        raise OSError("disk full")

    monkeypatch.setattr(capture_store, "_captures_dir", boom)
    assert capture_store.save_capture({"messages": []}, {"status": "success"}) is None


def test_save_capture_prunes_beyond_cap(tmp_path, monkeypatch):
    monkeypatch.setattr(capture_store, "_captures_dir", lambda: tmp_path)
    monkeypatch.setattr(capture_store, "_MAX_CAPTURES", 3)

    ids = []
    for i in range(5):
        cap_id = capture_store.save_capture({"messages": [{"role": "user", "content": f"req {i}"}]}, {"status": "success"})
        ids.append(cap_id)

    remaining = {p.stem for p in tmp_path.glob("*.json")}
    assert len(remaining) == 3
    # The three most recently written captures survive; the earliest two were pruned.
    assert remaining == set(ids[-3:])
