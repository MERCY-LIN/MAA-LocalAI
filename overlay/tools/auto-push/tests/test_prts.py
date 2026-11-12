import json

from autopush.metrics import BudgetLimits, RoundMetrics
from autopush.prts import PrtsClient


def test_query_jobs_filters_exact_stage_and_saves_content(tmp_path):
    metrics = RoundMetrics(
        round_name="prts",
        stage="act55side_09",
        limits=BudgetLimits(tool_calls=4),
    )
    client = PrtsClient(metrics=metrics)

    def request_json(path, _query):
        if path == "/copilot/query":
            jobs = [
                {
                    "id": 1,
                    "content": json.dumps(
                        {
                            "stage_name": "act55side_09",
                            "doc": {"title": "TL-9"},
                            "opers": [],
                        }
                    ),
                    "hot_score": 2,
                    "rating_ratio": 1,
                    "views": 10,
                },
                {
                    "id": 2,
                    "content": json.dumps(
                        {
                            "stage_name": "act55side_ex01",
                            "doc": {"title": "other"},
                            "opers": [],
                        }
                    ),
                    "hot_score": 100,
                    "rating_ratio": 1,
                    "views": 100,
                },
            ]
            return {"status_code": 200, "data": {"data": jobs, "has_next": False}}
        return {
            "status_code": 200,
            "data": {
                "id": 1,
                "content": json.dumps(
                    {
                        "stage_name": "act55side_09",
                        "doc": {"title": "TL-9"},
                        "opers": [],
                        "actions": [{"type": "SpeedUp"}],
                    }
                ),
                "hot_score": 2,
                "rating_ratio": 1,
                "views": 10,
            },
        }

    client._request_json = request_json
    jobs = client.query_jobs("act55side_09", count=10)
    paths = client.save_jobs(jobs, tmp_path)

    assert [job.id for job in jobs] == [1]
    assert jobs[0].content["actions"] == [{"type": "SpeedUp"}]
    assert paths[0].name == "act55side_09-1.json"
    assert json.loads(paths[0].read_text(encoding="utf-8"))["stage_name"] == "act55side_09"
    assert metrics.tests[0].name == "prts-job-download"
