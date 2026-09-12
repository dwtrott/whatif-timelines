"""End-to-end smoke test against a running server (mock provider by default).
    python -m whatif --provider mock --port 8765 &
    python tests/smoke.py http://localhost:8765
"""
import json
import sys
import time
import urllib.request

B = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8765"


def req(path, method="GET", body=None):
    r = urllib.request.Request(B + path, method=method, data=json.dumps(body).encode() if body is not None else None,
                               headers={"content-type": "application/json"})
    return json.load(urllib.request.urlopen(r))


def wait(pred, timeout=300):
    t0 = time.time()
    while time.time() - t0 < timeout:
        d = pred()
        if d:
            return d
        time.sleep(0.5)
    raise SystemExit("timeout")


sc = req("/api/scenarios", "POST", {"title": "2008 financial crisis",
                                   "question": "How does the crisis unfold if September 2008 goes differently?",
                                   "anchor_date": "2008-03-01", "horizon_date": "2009-06-30", "step_days": 30, "n_agents": 5})
sid = sc["id"]
d = wait(lambda: (lambda x: x if x["status"] in ("ready", "failed") else None)(req(f"/api/scenarios/{sid}")))
print("status", d["status"], d["error"])
assert d["status"] == "ready", d["log"][-1]
print("personas", [p["name"] for p in d["personas"]])
b = d["branches"][d["baseline_branch_id"]]
print("baseline", [(e["date"], e["headline"][:35]) for e in b["events"]])
ev = b["events"][min(3, len(b["events"]) - 1)]
f = req(f"/api/scenarios/{sid}/fork", "POST", {"parent_branch_id": b["id"], "fork_event_id": ev["id"],
                                             "premise": "Lehman Brothers is rescued on 14 September 2008."})
bid = f["branch_id"]
br = f["scenario"]["branches"][bid]
print("forked", br["name"], br["fork_date"], br["total_rounds"], br["step_days"])
br = wait(lambda: (lambda x: x if x["status"] in ("completed", "failed", "stopped") else None)(req(f"/api/scenarios/{sid}")["branches"][bid]))
print("branch", br["status"], br["error"], "rounds", br["rounds_done"], "events", len(br["events"]))
assert br["status"] == "completed"
print([(e["date"], e["headline"][:40], e["divergence"]) for e in br["events"]][:6])
print("report", json.dumps(br["report"])[:300])
print("indicators", br["indicators"][:2])
ev2 = br["events"][4]
f2 = req(f"/api/scenarios/{sid}/fork", "POST", {"parent_branch_id": bid, "fork_event_id": ev2["id"],
                                              "premise": "The Fed cuts rates to zero immediately.", "max_rounds": 4})
b2 = wait(lambda: (lambda x: x if x["status"] in ("completed", "failed", "stopped") else None)(req(f"/api/scenarios/{sid}")["branches"][f2["branch_id"]]))
print("child", b2["status"], b2["rounds_done"], b2["total_rounds"], "depth", b2["depth"])
d = req(f"/api/scenarios/{sid}")
p = d["personas"][0]
print(req(f"/api/scenarios/{sid}/branches/{bid}/interview", "POST", {"persona_id": p["id"], "question": "What would you do differently?"}))
print(req(f"/api/scenarios/{sid}/compare?a={b['id']}&b={bid}")["points"][:1])
print("log tail:", [l["msg"][:80] for l in d["log"][-3:]])
print("OK")
