import json

import httpx

from .conftest import claim_body, upload
from txn import stub_risk


def test_gateway_submits_and_polls_local_heatmap_without_following_remote_url(make_client):
    risk = httpx.ASGITransport(app=stub_risk.app)
    job_id = "HM-" + "a" * 24
    jobs = {}
    async def handle(request):
        if request.url.path.endswith("/heatmap"):
            assert request.headers["X-Risk-Secret"] == stub_risk.SECRET
            claim_id = json.loads(request.content)["claim_id"]
            evidence_id = request.url.path.split("/")[2]
            jobs[job_id] = {"job_id": job_id, "claim_id": claim_id, "evidence_id": evidence_id}
            return httpx.Response(202, json={**jobs[job_id], "status": "queued", "poll_url": "https://outside.invalid/ignored"})
        if request.url.path.startswith("/heatmaps/"):
            assert request.url.host == "risk"
            return httpx.Response(200, json={**jobs[job_id], "status": "succeeded", "result": {"grid_shape": [4, 4],
                "patch_scores": [[0.0] * 4 for _ in range(4)], "model_version": "explicit-test-double"}, "error": None})
        return await risk.handle_async_request(request)
    client = make_client(transport=httpx.MockTransport(handle))
    evidence = upload(client, "fake.jpg")
    claim = client.post("/claims", json=claim_body([evidence])).json()
    queued = client.post(f"/claims/{claim['claim_id']}/heatmap")
    assert queued.status_code == 202
    assert queued.json()["poll_url"] == f"/claims/{claim['claim_id']}/heatmap/{job_id}"
    completed = client.get(queued.json()["poll_url"])
    assert completed.status_code == 200
    assert completed.json()["status"] == "succeeded"
    assert len(completed.json()["result"]["patch_scores"]) == 4
    assert client.get(f"/claims/{claim['claim_id']}").json()["band"] == claim["band"]


def test_gateway_heatmap_rejects_green_and_unrelated_photo(client):
    evidence = upload(client)
    green = client.post("/claims", json=claim_body([evidence], key="green")).json()
    assert client.post(f"/claims/{green['claim_id']}/heatmap").status_code == 409
    fake = upload(client, "fake.jpg")
    red = client.post("/claims", json=claim_body([fake], key="red")).json()
    assert client.post(f"/claims/{red['claim_id']}/heatmap", json={"evidence_id": "not-this-claim"}).status_code == 400
