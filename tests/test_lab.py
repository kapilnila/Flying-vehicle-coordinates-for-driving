import time
import pytest
from spatial_id.test_lab import Lab

def test_limits_reject_arbitrary_targets(tmp_path):
    lab=Lab(tmp_path/"lab.db")
    for config in [{"url":"http://elsewhere"},{"concurrency":33},{"total_ids":100001},{"fault_seconds":11},{"scenario":"shell"}]:
        with pytest.raises(ValueError): lab.start(config)

def test_startup_marks_interrupted_runs(tmp_path,monkeypatch):
    lab=Lab(tmp_path/"lab.db")
    lab.save({"id":"old","status":"running","timeline":[]})
    cleaned=[]
    monkeypatch.setattr(lab,"reset_fault",lambda:cleaned.append(True))
    lab.reconcile()
    assert cleaned
    assert lab.get("old")["status"]=="interrupted"

def test_only_one_active_test(tmp_path,monkeypatch):
    lab=Lab(tmp_path/"lab.db")
    monkeypatch.setattr(lab,"execute",lambda run:lab.cancel.wait(2))
    lab.start({})
    try:
        with pytest.raises(RuntimeError): lab.start({})
    finally:
        lab.cancel.set();lab.thread.join()

def test_expired_fault_restored_by_independent_watchdog(tmp_path,monkeypatch):
    path=tmp_path/"lab.db"
    runner=Lab(path)
    with runner.connect() as c:c.execute("UPDATE fault SET expires=?",(time.time()-1,))
    watchdog=Lab(path)
    restored=[]
    monkeypatch.setattr(watchdog,"reset_fault",lambda:restored.append(True))
    watchdog.watchdog_once()
    assert restored==[True]

def test_terminal_status_always_has_report(tmp_path,monkeypatch):
    lab=Lab(tmp_path/"lab.db")
    snapshots=[]
    original=lab.save
    def capture(run):
        if run["status"] != "running":
            assert run["result"] is not None
        snapshots.append(run["status"])
        original(run)
    monkeypatch.setattr(lab,"save",capture)
    monkeypatch.setattr(lab,"reset_fault",lambda:None)
    lab.cancel.set()
    run=dict(id="cancelled",status="running",config=dict(scenario="load",duration=1,concurrency=1,batch=1,total_ids=1),timeline=[],result=None)
    lab.execute(run)
    assert snapshots[-1]=="cancelled"
