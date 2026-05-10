"""
BaseAgent — the contract every Agent inherits (per CODE-SPEC §3.2).

Responsibilities of the base class:
  • Pick the model from site_config based on the subclass's task_type.
  • Provide a `_call_llm()` wrapper that records every LLM call so we can
    aggregate tokens / cost / duration across multiple calls per run.
  • Insert one `agent_runs` row per attempt with status='started', then
    update it to 'success' / 'failed' / 'retried' at the end.
  • Each agent_runs write uses its own short DB connection with autocommit
    so a 'started' row is durable even if the LLM call later crashes.
  • Exponential-backoff retry up to max_retries.

Subclass contract:
  • Set class attrs `name` and `task_type`.
  • Implement `_execute(input_data: dict) -> dict`.
  • Inside `_execute`, call `self._call_llm(prompt=..., **kwargs)` instead of
    self.llm.generate(...) so metrics are tracked.
"""

from __future__ import annotations

import json
import time
import traceback
from abc import ABC, abstractmethod
from typing import Any, ClassVar, Optional
from uuid import UUID, uuid4

from src.db.client import get_db_connection
from src.utils.llm import BaseLLMProvider, LLMResponse


class AgentFailure(Exception):
    """Raised when an agent exhausts its retries."""


class BaseAgent(ABC):
    name: ClassVar[str] = ""           # e.g. "writing", "qa"
    task_type: ClassVar[str] = ""      # used to pick a model from site_config
    max_retries: ClassVar[int] = 3     # number of retry attempts AFTER the first try
    backoff_seconds: ClassVar[tuple[float, ...]] = (0.5, 1.0, 2.0)

    # ---------------------------------------------------------------- init

    def __init__(self, llm: BaseLLMProvider, site_config: dict[str, Any]):
        if not self.name or not self.task_type:
            raise TypeError(
                f"{type(self).__name__} must define class attrs `name` and `task_type`."
            )
        self.llm = llm
        self.site_config = site_config
        self._calls: list[LLMResponse] = []

    # ------------------------------------------------------- model lookup

    def get_model(self) -> str:
        """Find the model_id for this agent's task in site_config.text_provider."""
        try:
            text_cfg = self.site_config["text_provider"]
            return text_cfg[f"{self.task_type}_model"]
        except KeyError as e:
            raise KeyError(
                f"site_config.text_provider missing key for task_type "
                f"'{self.task_type}' (expected '{self.task_type}_model'): {e}"
            ) from None

    # ---------------------------------------------------------- LLM call

    def _reset_call_metrics(self) -> None:
        self._calls = []

    def _call_llm(self, prompt: str, **kwargs: Any) -> LLMResponse:
        """
        Subclasses should call this instead of self.llm.generate(...).
        Handles model selection from site_config and records the response so
        BaseAgent can aggregate metrics for the agent_runs row.

        Multiple calls within one run are accumulated. Per D2:
          - tokens_in / tokens_out / cost_usd / duration_ms: SUM across calls
          - model: the LAST call's model (we'll revisit if mixed-model runs
            become common)
        """
        model = kwargs.pop("model", None) or self.get_model()
        resp = self.llm.generate(prompt=prompt, model=model, **kwargs)
        self._calls.append(resp)
        return resp

    def _aggregated_metrics(self) -> dict[str, Any]:
        if not self._calls:
            return {
                "tokens_in": 0,
                "tokens_out": 0,
                "cost_usd": 0.0,
                "duration_ms": 0,
                "model": None,
            }
        return {
            "tokens_in": sum(c.tokens_in for c in self._calls),
            "tokens_out": sum(c.tokens_out for c in self._calls),
            "cost_usd": sum(c.cost_usd for c in self._calls),
            "duration_ms": sum(c.duration_ms for c in self._calls),
            "model": self._calls[-1].model,
        }

    # ---------------------------------------------------- agent_runs I/O

    def _insert_started(
        self,
        run_id: UUID,
        site_id: UUID,
        article_id: Optional[UUID],
        input_data: dict[str, Any],
    ) -> None:
        """Insert a row in 'started' state. Independent connection + autocommit."""
        with get_db_connection(autocommit=True) as conn, conn.cursor() as cur:
            cur.execute(
                """
                insert into agent_runs (id, site_id, article_id, agent_name, status, input)
                values (%s, %s, %s, %s, 'started', %s::jsonb)
                """,
                (
                    str(run_id),
                    str(site_id),
                    str(article_id) if article_id else None,
                    self.name,
                    json.dumps(input_data),
                ),
            )

    def _update_finalize(
        self,
        run_id: UUID,
        status: str,
        metrics: dict[str, Any],
        output: Optional[dict[str, Any]],
        error_msg: Optional[str],
    ) -> None:
        """Update the row to a terminal/intermediate state. Same conn pattern."""
        with get_db_connection(autocommit=True) as conn, conn.cursor() as cur:
            cur.execute(
                """
                update agent_runs
                   set status      = %s,
                       output      = %s::jsonb,
                       error_msg   = %s,
                       tokens_in   = %s,
                       tokens_out  = %s,
                       cost_usd    = %s,
                       duration_ms = %s,
                       model       = %s
                 where id = %s
                """,
                (
                    status,
                    json.dumps(output) if output is not None else None,
                    error_msg,
                    metrics["tokens_in"],
                    metrics["tokens_out"],
                    metrics["cost_usd"],
                    metrics["duration_ms"],
                    metrics["model"],
                    str(run_id),
                ),
            )

    # ----------------------------------------------------------- execute

    @abstractmethod
    def _execute(self, input_data: dict[str, Any]) -> dict[str, Any]:
        """Subclass-specific work. Must return a JSON-serialisable dict."""

    # ------------------------------------------------------------- run()

    def run(
        self,
        site_id: UUID,
        article_id: Optional[UUID],
        input_data: dict[str, Any],
    ) -> dict[str, Any]:
        """
        Execute with retries. Each attempt = one agent_runs row.

        Failure flow:
          attempt 0 fails → status='retried' → attempt 1 → ... → 'success' or 'failed'
          (Phase 1.A intentionally does NOT link rows; (article_id, agent_name,
          created_at) order is enough until we need an explicit retry chain.)
        """
        attempt = 0
        last_error: Optional[Exception] = None

        while True:  # exits on success or final-fail
            self._reset_call_metrics()
            run_id = uuid4()
            self._insert_started(run_id, site_id, article_id, input_data)

            try:
                output = self._execute(input_data)
            except Exception as e:
                last_error = e
                metrics = self._aggregated_metrics()
                err_text = self._format_error(e)

                if attempt < self.max_retries:
                    self._update_finalize(
                        run_id, "retried", metrics, output=None, error_msg=err_text
                    )
                    self._sleep_backoff(attempt)
                    attempt += 1
                    continue

                # Exhausted retries
                self._update_finalize(
                    run_id, "failed", metrics, output=None, error_msg=err_text
                )
                raise AgentFailure(
                    f"{self.name} failed after {attempt + 1} attempts: {err_text}"
                ) from last_error

            # Success
            metrics = self._aggregated_metrics()
            self._update_finalize(
                run_id, "success", metrics, output=output, error_msg=None
            )
            return output

    # ---------------------------------------------------------- helpers

    def _sleep_backoff(self, attempt_idx: int) -> None:
        if attempt_idx < len(self.backoff_seconds):
            time.sleep(self.backoff_seconds[attempt_idx])

    @staticmethod
    def _format_error(e: BaseException, max_len: int = 1000) -> str:
        """Compact one-paragraph error string for the agent_runs row."""
        msg = f"{type(e).__name__}: {e}"
        # Include the last few traceback frames so debugging is possible
        tb = "".join(traceback.format_exception(type(e), e, e.__traceback__))
        full = f"{msg}\n--\n{tb}"
        return full[:max_len]
