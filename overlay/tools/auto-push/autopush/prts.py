"""Read-only client for the public PRTS Plus Copilot library."""

from __future__ import annotations

import json
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .metrics import RoundMetrics, TestResult

DEFAULT_PRTS_BASE_URL = "https://prts.maa.plus"


class PrtsError(RuntimeError):
    """Raised when a PRTS Plus request or response is invalid."""


@dataclass(frozen=True)
class PrtsJob:
    id: int
    stage_id: str
    title: str
    content: dict[str, Any]
    hot_score: float
    rating_ratio: float
    views: int
    upload_time: str


class PrtsClient:
    """Query and save only public Copilot jobs for one exact stage."""

    def __init__(
        self,
        base_url: str = DEFAULT_PRTS_BASE_URL,
        *,
        timeout_seconds: int = 30,
        metrics: RoundMetrics | None = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.metrics = metrics

    def _request_json(self, path: str, query: dict[str, Any]) -> dict[str, Any]:
        if self.metrics is not None:
            self.metrics.record_tool_call()
        url = f"{self.base_url}{path}?{urllib.parse.urlencode(query)}"
        request = urllib.request.Request(url, method="GET")
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        try:
            with opener.open(request, timeout=self.timeout_seconds) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except (OSError, TimeoutError, UnicodeDecodeError, json.JSONDecodeError) as exception:
            raise PrtsError(str(exception)) from exception
        if not isinstance(payload, dict) or payload.get("status_code") != 200:
            raise PrtsError("PRTS Plus returned an invalid response")
        return payload

    def query_jobs(
        self,
        stage_id: str,
        *,
        count: int = 20,
        page_size: int = 100,
        max_pages: int = 5,
        include_actions: bool = True,
    ) -> list[PrtsJob]:
        jobs: dict[int, PrtsJob] = {}
        for page in range(1, max_pages + 1):
            payload = self._request_json(
                "/copilot/query",
                {
                    "levelKeyword": stage_id,
                    "limit": page_size,
                    "page": page,
                    "orderBy": "hot_score",
                },
            )
            data = payload.get("data")
            if not isinstance(data, dict) or not isinstance(data.get("data"), list):
                raise PrtsError("PRTS Plus query payload has no job list")
            for row in data["data"]:
                job = self._parse_row(row)
                if job is not None and job.stage_id == stage_id:
                    jobs[job.id] = job
            if len(jobs) >= count or not data.get("has_next"):
                break
        candidates = sorted(
            jobs.values(),
            key=lambda item: (item.hot_score, item.rating_ratio, item.views),
            reverse=True,
        )
        if not include_actions:
            return candidates[:count]

        complete_jobs: list[PrtsJob] = []
        for candidate in candidates:
            detailed = self.get_job(candidate.id)
            if detailed is not None and detailed.stage_id == stage_id:
                complete_jobs.append(detailed)
            if len(complete_jobs) >= count:
                break
        return complete_jobs

    def get_job(self, job_id: int) -> PrtsJob | None:
        payload = self._request_json(f"/copilot/get/{job_id}", {})
        row = payload.get("data")
        if not isinstance(row, dict):
            return None
        return self._parse_row(row)

    @staticmethod
    def _parse_row(row: Any) -> PrtsJob | None:
        if not isinstance(row, dict):
            return None
        try:
            content = json.loads(str(row["content"]))
            stage_id = str(content["stage_name"])
            return PrtsJob(
                id=int(row["id"]),
                stage_id=stage_id,
                title=str(content.get("doc", {}).get("title", "")),
                content=content,
                hot_score=float(row.get("hot_score") or 0),
                rating_ratio=float(row.get("rating_ratio") or 0),
                views=int(row.get("views") or 0),
                upload_time=str(row.get("upload_time") or ""),
            )
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            return None

    def save_jobs(
        self,
        jobs: list[PrtsJob],
        output_dir: str | Path,
    ) -> list[Path]:
        destination = Path(output_dir)
        destination.mkdir(parents=True, exist_ok=True)
        paths: list[Path] = []
        for job in jobs:
            path = destination / f"{job.stage_id}-{job.id}.json"
            path.write_text(
                json.dumps(job.content, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            paths.append(path)
            if self.metrics is not None:
                self.metrics.add_test(
                    TestResult(
                        name="prts-job-download",
                        success=True,
                        detail=(
                            f"hot={job.hot_score} rating={job.rating_ratio} "
                            f"views={job.views}"
                        ),
                        strategy_path=str(path.resolve()),
                    )
                )
        return paths
