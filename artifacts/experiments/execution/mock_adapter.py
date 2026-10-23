"""
Deterministic mock adapter for the experiment execution harness.
Non effettua chiamate di rete; restituisce risposte programmate o generate da fixture.
"""

from typing import Callable, List, Optional, Sequence, Union

try:
    from .runner import (
        AdapterRequest,
        BaseAdapter,
        RawAdapterResponse,
        TokenUsage,
    )
except ImportError:
    from artifacts.experiments.execution.runner import (
        AdapterRequest,
        BaseAdapter,
        RawAdapterResponse,
        TokenUsage,
    )


class MockAdapter(BaseAdapter):
    """
    Adapter deterministico programmabile per scenari mock locali.
    Consente di simulare successi, timeout, guasti di trasporto, errori di provider e payload invalidi.
    """

    def __init__(
        self,
        responses: Optional[Sequence[RawAdapterResponse]] = None,
        generator: Optional[Callable[[AdapterRequest], RawAdapterResponse]] = None,
    ):
        self._responses: List[RawAdapterResponse] = list(responses) if responses else []
        self._generator = generator
        self._calls: List[AdapterRequest] = []

    @property
    def calls(self) -> List[AdapterRequest]:
        return list(self._calls)

    @classmethod
    def from_payload(
        cls,
        payload: Union[str, bytes],
        token_usage: Optional[TokenUsage] = None,
        latency_ms: float = 15.0,
    ) -> "MockAdapter":
        resp = RawAdapterResponse(
            status="SUCCESS",
            raw_payload=payload,
            model_output_payload=payload,
            provider_envelope=None,
            http_status_code=200,
            latency_ms=latency_ms,
            token_usage=token_usage if token_usage is not None else TokenUsage(100, 50, 150),
        )
        return cls(responses=[resp])

    @classmethod
    def from_sequence(
        cls,
        responses: Sequence[RawAdapterResponse],
    ) -> "MockAdapter":
        return cls(responses=responses)

    def execute(self, request: AdapterRequest) -> RawAdapterResponse:
        self._calls.append(request)
        if self._generator is not None:
            return self._generator(request)
        if not self._responses:
            raise IndexError("MockAdapter: nessuna ulteriore risposta programmata disponibile")
        return self._responses.pop(0)
