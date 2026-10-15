import json

from replicate.check_bundle import check_ground_truth
from replicate.live_ground_truth import build, convert, render_script

SCENARIO = {
    "workflows": [{"id": "W1", "name": "City facts", "apps": ["wikipedia.org"], "parameters": ["city"],
                   "steps": ["Search {city}", "Read", "Note it"]}],
    "runs": {"W1.1": {"workflow": "W1", "values": {"city": "Lisbon"}},
             "W1.2": {"workflow": "W1", "values": {"city": "Porto"}},
             "W1.3": {"workflow": "W1", "values": {"city": "Faro"}}},
    "noise": {"N1": "Glance at the news"},
    "script": [{"run": "W1.1", "steps": [1, 2]}, {"noise": "N1"}, {"run": "W1.1", "steps": [3, 3]},
               {"pause": 5}, {"run": "W1.2"}, {"run": "W1.3"}],
}
LOG = """2026-10-13T10:00:00+00:00 start 1
2026-10-13T10:00:20+00:00 end 1 ok
2026-10-13T10:00:21+00:00 start 2
2026-10-13T10:00:40+00:00 end 2 ok
2026-10-13T10:00:41+00:00 start 3
2026-10-13T10:00:50+00:00 end 3 ok
2026-10-13T10:01:00+00:00 start 5
2026-10-13T10:01:30+00:00 end 5 ok
2026-10-13T10:01:31+00:00 start 6
2026-10-13T10:01:40+00:00 end 6 failed site blocked
"""


def test_script_renders_partial_runs_and_values():
    script = render_script(SCENARIO)
    assert "1. Task W1.1, steps 1-2 of 3:\n   1. Search Lisbon" in script
    assert "4. Pause: do nothing for 5 seconds" in script


def test_build_and_convert(tmp_path):
    ground_truth = build(SCENARIO, LOG)
    runs = ground_truth["workflows"][0]["runs"]
    assert [run["id"] for run in runs] == ["W1.1", "W1.2"]  # W1.3 failed
    assert len(runs[0]["segments"]) == 2  # interrupted by N1
    assert [item["id"] for item in ground_truth["noise"]] == ["N1", "W1.3"]

    bundle = tmp_path / "live1"
    (bundle / "traces").mkdir(parents=True)
    action_times = [5, 15, 30, 45, 55, 70, 80, 95]  # 55 s is the pause, between chunks
    (bundle / "traces" / "dom.jsonl").write_text("".join(
        json.dumps({"seq": seq, "t": t, "type": "action", "op": "CLICK", "site": "x",
                    "element": {"role": "button", "text": ""}}) + "\n" for seq, t in enumerate(action_times)))
    (bundle / "manifest.json").write_text(json.dumps({
        "schema_version": "0.2", "session_id": "live1", "source": {"kind": "cdp_recorder"},
        "started_at": "2026-10-13T10:00:00+00:00", "duration_s": 120, "timing": {"kind": "observed"},
        "streams": {"dom": {"path": "traces/dom.jsonl", "count": len(action_times)}}}))
    converted = convert(ground_truth, bundle)
    lisbon, porto = converted["opportunities"][0]["instances"]
    assert lisbon["seqs"] == [0, 1, 3] and porto["seqs"] == [5, 6]
    assert converted["negatives"][0]["seqs"] == [2]  # the news glance
    assert converted["negatives"][-1]["seqs"] == [4]  # unlabelled pause action
    assert check_ground_truth(converted, bundle) == []
