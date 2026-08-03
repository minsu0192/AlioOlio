import json

import httpx

from alio_olio.alio import AlioClient


def test_list_paginates_and_maps_fields():
    calls = []
    def handler(request):
        payload = json.loads(request.content)
        calls.append(payload["pageNo"])
        item = {"seq": 100 + payload["pageNo"], "pname": "기관", "title": "공고", "termStart": "2026.08.01",
                "termEnd": "2026.08.10", "frstDate": "2026.08.01", "ing": "진행중", "person": "2",
                "workTypeNa": "정규직,비정규직", "carrerNa": "신입", "locationNa": "서울"}
        return httpx.Response(200, json={"status": "success", "data": {"recruitList": [item], "page": {"totalPage": 2}}})
    client = AlioClient(transport=httpx.MockTransport(handler))
    result = client.list_postings()
    assert calls == [1, 2]
    assert [p.seq for p in result] == [101, 102]
    assert result[0].employment_types == ["정규직", "비정규직"]
