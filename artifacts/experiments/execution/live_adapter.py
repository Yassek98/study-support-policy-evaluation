"""
OpenRouter Chat Completions adapter for the experiment runner.
Fornisce l'adapter per invocazioni HTTP reali e simulate via FakeHttpTransport,
con trasporto iniettabile, isolamento delle credenziali, tracciamento dual-payload
e conformita al contratto BaseAdapter.
"""

import copy
import http.client
import json
import os
import re
import socket
import time
import urllib.error
import urllib.request
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple, Union

try:
    from artifacts.experiments.execution.runner import (
        AdapterRequest,
        BaseAdapter,
        RawAdapterResponse,
        TokenUsage,
    )
    from artifacts.experiments.execution.structured_output import canonical_json_dumps
except ImportError:
    from execution.runner import (
        AdapterRequest,
        BaseAdapter,
        RawAdapterResponse,
        TokenUsage,
    )
    from execution.structured_output import canonical_json_dumps


class TransportException(Exception):
    """Eccezione di trasporto/rete di basso livello (connessione interrotta, socket error)."""
    pass


class TimeoutException(Exception):
    """Eccezione di timeout HTTP o socket."""
    pass


@dataclass(frozen=True)
class HttpResponse:
    """Risposta HTTP immutabile restituita dall'HttpTransport."""
    status_code: int
    headers: MappingProxyType
    body: bytes


class HttpTransport(ABC):
    """Interfaccia astratta del trasporto HTTP iniettabile per test offline e chiamate reali."""

    @abstractmethod
    def request(
        self,
        method: str,
        url: str,
        headers: Dict[str, str],
        body: bytes,
        timeout_seconds: float,
    ) -> HttpResponse:
        """Esegue una singola richiesta HTTP senza retry interni."""
        pass


class UrllibHttpTransport(HttpTransport):
    """
    Trasporto HTTP di produzione basato sulla libreria standard urllib.
    Non aggiunge dipendenze esterne. Cattura gli errori HTTP per preservare il body di errore.
    """

    def request(
        self,
        method: str,
        url: str,
        headers: Dict[str, str],
        body: bytes,
        timeout_seconds: float,
    ) -> HttpResponse:
        req = urllib.request.Request(
            url=url,
            data=body if method.upper() in ("POST", "PUT", "PATCH") else None,
            headers=headers,
            method=method.upper(),
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout_seconds) as response:
                resp_body = response.read()
                resp_headers = dict(response.headers)
                return HttpResponse(
                    status_code=response.status,
                    headers=MappingProxyType(resp_headers),
                    body=resp_body,
                )
        except urllib.error.HTTPError as ex:
            # Gli errori HTTP 4xx/5xx restituiscono un body valido per la classificazione
            err_body = ex.read()
            err_headers = dict(ex.headers)
            return HttpResponse(
                status_code=ex.code,
                headers=MappingProxyType(err_headers),
                body=err_body,
            )
        except (urllib.error.URLError, socket.timeout, TimeoutError) as ex:
            if isinstance(ex, (socket.timeout, TimeoutError)) or "timed out" in str(ex).lower():
                raise TimeoutException(f"Richiesta HTTP scaduta ({timeout_seconds}s): {ex}") from ex
            raise TransportException(f"Errore di trasporto HTTP: {ex}") from ex
        except (http.client.HTTPException, ConnectionError, OSError) as ex:
            raise TransportException(f"Errore di connessione HTTP: {ex}") from ex


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Turn redirects into ordinary HTTP errors instead of forwarding credentials."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class RestrictedUrllibHttpTransport(HttpTransport):
    """One-request transport with an exact URL allowlist and bounded response.

    It is intended for credential-bearing experiment calls. Redirects are not
    followed, response bodies are read with a hard byte ceiling, and no retries are
    performed. HTTP error bodies remain available for typed provider accounting.
    """

    def __init__(self, *, allowed_urls: Sequence[str], max_response_bytes: int = 1_048_576):
        urls = tuple(allowed_urls)
        if not urls or any(not isinstance(url, str) or not url.startswith("https://") for url in urls):
            raise ValueError("Restricted transport requires explicit HTTPS URLs")
        if type(max_response_bytes) is not int or max_response_bytes <= 0:
            raise ValueError("Positive max_response_bytes required")
        self._allowed_urls = frozenset(urls)
        self._max_response_bytes = max_response_bytes
        self._opener = urllib.request.build_opener(_NoRedirectHandler())

    def _read_bounded(self, stream) -> bytes:
        body = stream.read(self._max_response_bytes + 1)
        if len(body) > self._max_response_bytes:
            raise TransportException("HTTP response exceeded the configured byte limit")
        return body

    def request(self, method, url, headers, body, timeout_seconds):
        if url not in self._allowed_urls:
            raise TransportException("HTTP destination is outside the exact allowlist")
        if type(timeout_seconds) not in (int, float) or timeout_seconds <= 0:
            raise ValueError("Positive timeout required")
        req = urllib.request.Request(url=url,
            data=body if method.upper() in ("POST", "PUT", "PATCH") else None,
            headers=headers, method=method.upper())
        try:
            with self._opener.open(req, timeout=timeout_seconds) as response:
                return HttpResponse(response.status, MappingProxyType(dict(response.headers)),
                                    self._read_bounded(response))
        except urllib.error.HTTPError as ex:
            # Includes blocked 30x responses; their Location is never followed.
            return HttpResponse(ex.code, MappingProxyType(dict(ex.headers)), self._read_bounded(ex))
        except (urllib.error.URLError, socket.timeout, TimeoutError) as ex:
            if isinstance(ex, (socket.timeout, TimeoutError)) or "timed out" in str(ex).lower():
                raise TimeoutException(f"HTTP request timed out after {timeout_seconds}s") from ex
            raise TransportException("HTTP transport failed") from ex
        except TransportException:
            raise
        except (http.client.HTTPException, ConnectionError, OSError) as ex:
            raise TransportException("HTTP connection failed") from ex


class FakeHttpTransport(HttpTransport):
    """
    Programmable fake HTTP transport for fully offline contract tests.
    Consente di simulare successi, errori di quota (429), server (503), auth (401),
    malformazioni, timeout e rotture di trasporto, registrando tutte le chiamate ricevute.
    """

    def __init__(
        self,
        responses: Optional[Sequence[Union[HttpResponse, Exception]]] = None,
        handler: Optional[Callable[[str, str, Dict[str, str], bytes, float], HttpResponse]] = None,
    ):
        self._responses: List[Union[HttpResponse, Exception]] = list(responses) if responses else []
        self._handler = handler
        self._calls: List[Dict[str, Any]] = []

    @property
    def calls(self) -> List[Dict[str, Any]]:
        return list(self._calls)

    def request(
        self,
        method: str,
        url: str,
        headers: Dict[str, str],
        body: bytes,
        timeout_seconds: float,
    ) -> HttpResponse:
        self._calls.append({
            "method": method,
            "url": url,
            "headers": dict(headers),
            "body": body,
            "timeout_seconds": timeout_seconds,
        })
        if self._handler is not None:
            return self._handler(method, url, headers, body, timeout_seconds)
        if not self._responses:
            raise IndexError("FakeHttpTransport: nessuna ulteriore risposta programmata disponibile")
        item = self._responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


class LiveAdapter(BaseAdapter):
    """
    Adapter reale e controllato per OpenRouter Chat Completions.
    Implementa:
    - Trasporto iniettabile (per test offline);
    - Tracciamento dual-payload (provider envelope vs model output);
    - Sanitizzazione delle credenziali in caso di errore;
    - Classificazione fail-closed degli errori HTTP e di trasporto;
    - Regola inviolabile: un'invocazione di execute() corrisponde a una singola chiamata HTTP (zero retry interni).
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        api_key_getter: Optional[Callable[[], Optional[str]]] = None,
        base_url: str = "https://openrouter.ai/api/v1",
        http_transport: Optional[HttpTransport] = None,
        clock_source: Callable[[], float] = time.perf_counter,
        default_timeout_seconds: float = 30.0,
        site_url: Optional[str] = None,
        site_name: Optional[str] = None,
    ):
        normalized_base_url = base_url.rstrip("/")
        if normalized_base_url != "https://openrouter.ai/api/v1":
            raise ValueError(
                "LiveAdapter accetta soltanto l'endpoint OpenRouter canonico "
                "https://openrouter.ai/api/v1"
            )
        self._api_key = api_key
        self._api_key_getter = api_key_getter
        self._base_url = normalized_base_url
        self._transport = http_transport if http_transport is not None else UrllibHttpTransport()
        self._clock_source = clock_source
        self._default_timeout_seconds = default_timeout_seconds
        self._site_url = site_url
        self._site_name = site_name

    def _resolve_api_key(self) -> Optional[str]:
        """Risolve l'API key in modo sicuro: esplicita, getter, ambiente."""
        if self._api_key:
            return self._api_key
        if self._api_key_getter is not None:
            k = self._api_key_getter()
            if k:
                return k
        env_val = os.environ.get("OPENROUTER_API_KEY")
        if env_val:
            return env_val
        return None

    def _sanitize_message(self, message: str, secret: Optional[str]) -> str:
        """Rimuove tassativamente token segreti o header Bearer da qualsiasi messaggio di errore."""
        if not message:
            return ""
        sanitized = message
        if secret and len(secret) > 4:
            sanitized = sanitized.replace(secret, "[REDACTED_API_KEY]")
        sanitized = re.sub(r"Bearer\s+[A-Za-z0-9_\.-]+", "Bearer [REDACTED_TOKEN]", sanitized)
        sanitized = re.sub(r"sk-or-v1-[a-f0-9]+", "[REDACTED_API_KEY]", sanitized, flags=re.IGNORECASE)
        return sanitized

    def execute(self, request: AdapterRequest) -> RawAdapterResponse:
        t0 = self._clock_source()
        api_key = self._resolve_api_key()

        # Invariante fail-closed su credenziali
        if not api_key:
            latency_ms = max(0.0, (self._clock_source() - t0) * 1000.0)
            return RawAdapterResponse(
                status="PROVIDER_ERROR",
                http_status_code=None,
                latency_ms=latency_ms,
                error_message="OPENROUTER_API_KEY mancante: credenziale non configurata nell'ambiente o nell'adapter",
                requested_model=request.model,
            )

        endpoint_url = f"{self._base_url}/chat/completions"
        timeout_sec = request.timeout_seconds if request.timeout_seconds is not None else self._default_timeout_seconds

        # Costruzione del payload deterministico privo di credenziali o oracoli
        req_body_dict = request.build_canonical_request_body()
        req_body_bytes = canonical_json_dumps(req_body_dict).encode("utf-8")

        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        if self._site_url:
            headers["HTTP-Referer"] = self._site_url
        if self._site_name:
            headers["X-OpenRouter-Title"] = self._site_name

        # Esecuzione della singola richiesta HTTP
        http_resp: Optional[HttpResponse] = None
        try:
            http_resp = self._transport.request(
                method="POST",
                url=endpoint_url,
                headers=headers,
                body=req_body_bytes,
                timeout_seconds=timeout_sec,
            )
        except TimeoutException as ex:
            latency_ms = max(0.0, (self._clock_source() - t0) * 1000.0)
            return RawAdapterResponse(
                status="TIMEOUT",
                latency_ms=latency_ms,
                error_message=self._sanitize_message(str(ex), api_key),
                requested_model=request.model,
            )
        except TransportException as ex:
            latency_ms = max(0.0, (self._clock_source() - t0) * 1000.0)
            return RawAdapterResponse(
                status="TRANSPORT_ERROR",
                latency_ms=latency_ms,
                error_message=self._sanitize_message(str(ex), api_key),
                requested_model=request.model,
            )
        latency_ms = max(0.0, (self._clock_source() - t0) * 1000.0)
        raw_envelope_bytes = http_resp.body

        # Parsing dell'envelope di risposta del provider
        env_data: Optional[Dict[str, Any]] = None
        try:
            text_str = raw_envelope_bytes.decode("utf-8")
            parsed = json.loads(text_str)
            if isinstance(parsed, dict):
                env_data = parsed
        except Exception:
            pass

        # Gestione risposte HTTP non JSON o non strutturate
        if env_data is None:
            if http_resp.status_code == 200:
                return RawAdapterResponse(
                    status="PROVIDER_ERROR",
                    http_status_code=200,
                    latency_ms=latency_ms,
                    provider_envelope=raw_envelope_bytes,
                    model_output_payload=None,
                    error_message="Risposta HTTP 200 con envelope provider non valido o non decodificabile come JSON",
                    requested_model=request.model,
                )
            else:
                return RawAdapterResponse(
                    status="PROVIDER_ERROR",
                    http_status_code=http_resp.status_code,
                    latency_ms=latency_ms,
                    provider_envelope=raw_envelope_bytes,
                    model_output_payload=None,
                    error_message=self._sanitize_message(
                        f"Errore HTTP {http_resp.status_code} con envelope non-JSON: {raw_envelope_bytes[:200]!r}",
                        api_key,
                    ),
                    requested_model=request.model,
                )

        # Estrazione telemetria dal provider envelope
        returned_model = env_data.get("model")
        provider_resp_id = env_data.get("id")
        returned_provider = env_data.get("provider")

        token_usage: Optional[TokenUsage] = None
        if "usage" in env_data and isinstance(env_data["usage"], dict):
            u = env_data["usage"]
            p_tok = u.get("prompt_tokens")
            c_tok = u.get("completion_tokens")
            tot_tok = u.get("total_tokens")
            if (
                isinstance(p_tok, int)
                and not isinstance(p_tok, bool)
                and p_tok >= 0
                and isinstance(c_tok, int)
                and not isinstance(c_tok, bool)
                and c_tok >= 0
                and isinstance(tot_tok, int)
                and not isinstance(tot_tok, bool)
                and tot_tok == p_tok + c_tok
            ):
                token_usage = TokenUsage(prompt_tokens=p_tok, completion_tokens=c_tok, total_tokens=tot_tok)

        # Gestione codici di stato HTTP di errore (4xx, 5xx)
        if http_resp.status_code != 200:
            err_field = env_data.get("error", {})
            if isinstance(err_field, dict):
                raw_msg = err_field.get("message", str(env_data))
            else:
                raw_msg = str(err_field)
            err_msg = self._sanitize_message(f"OpenRouter HTTP {http_resp.status_code}: {raw_msg}", api_key)
            return RawAdapterResponse(
                status="PROVIDER_ERROR",
                http_status_code=http_resp.status_code,
                latency_ms=latency_ms,
                provider_envelope=raw_envelope_bytes,
                model_output_payload=None,
                error_message=err_msg,
                token_usage=token_usage,
                requested_model=request.model,
                returned_model=returned_model,
                returned_provider=returned_provider,
                provider_response_id=provider_resp_id,
            )

        # Un HTTP 200 non e' sufficiente: modello e rotta devono coincidere con
        # il contratto richiesto, altrimenti l'esecuzione fallisce senza parsing.
        if returned_model != request.model:
            return RawAdapterResponse(
                status="PROVIDER_ERROR",
                http_status_code=200,
                latency_ms=latency_ms,
                provider_envelope=raw_envelope_bytes,
                model_output_payload=None,
                error_message=(
                    "Identita modello restituita non conforme: "
                    f"richiesto {request.model!r}, restituito {returned_model!r}"
                ),
                token_usage=token_usage,
                requested_model=request.model,
                returned_model=returned_model,
                returned_provider=returned_provider,
                provider_response_id=provider_resp_id,
            )

        requested_provider_order = request.routing_policy.get("provider_order", ())
        if not isinstance(returned_provider, str) or not returned_provider.strip() or not any(
                isinstance(provider, str)
                and returned_provider.casefold() == provider.casefold()
                for provider in requested_provider_order
        ):
            return RawAdapterResponse(
                status="PROVIDER_ERROR",
                http_status_code=200,
                latency_ms=latency_ms,
                provider_envelope=raw_envelope_bytes,
                model_output_payload=None,
                error_message=(
                    "Identita provider restituita assente o non conforme alla rotta richiesta: "
                    f"richiesta {list(requested_provider_order)!r}, "
                    f"restituita {returned_provider!r}"
                ),
                token_usage=token_usage,
                requested_model=request.model,
                returned_model=returned_model,
                returned_provider=returned_provider,
                provider_response_id=provider_resp_id,
            )

        # Gestione HTTP 200 (Successo provider)
        choices = env_data.get("choices")
        if not isinstance(choices, list) or len(choices) == 0:
            return RawAdapterResponse(
                status="PROVIDER_ERROR",
                http_status_code=200,
                latency_ms=latency_ms,
                provider_envelope=raw_envelope_bytes,
                model_output_payload=None,
                error_message="Risposta HTTP 200 priva del campo 'choices' o lista vuota",
                token_usage=token_usage,
                requested_model=request.model,
                returned_model=returned_model,
                returned_provider=returned_provider,
                provider_response_id=provider_resp_id,
            )

        first_choice = choices[0]
        if not isinstance(first_choice, dict):
            return RawAdapterResponse(
                status="PROVIDER_ERROR",
                http_status_code=200,
                latency_ms=latency_ms,
                provider_envelope=raw_envelope_bytes,
                model_output_payload=None,
                error_message="Elemento choices[0] non e' un oggetto strutturato",
                token_usage=token_usage,
                requested_model=request.model,
                returned_model=returned_model,
                returned_provider=returned_provider,
                provider_response_id=provider_resp_id,
            )

        message_obj = first_choice.get("message", {})
        if not isinstance(message_obj, dict):
            return RawAdapterResponse(
                status="PROVIDER_ERROR",
                http_status_code=200,
                latency_ms=latency_ms,
                provider_envelope=raw_envelope_bytes,
                model_output_payload=None,
                error_message="Campo 'message' in choices[0] non e' un oggetto",
                token_usage=token_usage,
                requested_model=request.model,
                returned_model=returned_model,
                returned_provider=returned_provider,
                provider_response_id=provider_resp_id,
            )

        content = message_obj.get("content")
        if not isinstance(content, (str, bytes)):
            return RawAdapterResponse(
                status="PROVIDER_ERROR",
                http_status_code=200,
                latency_ms=latency_ms,
                provider_envelope=raw_envelope_bytes,
                model_output_payload=None,
                error_message="Contenuto choices[0].message.content assente o non testuale",
                token_usage=token_usage,
                requested_model=request.model,
                returned_model=returned_model,
                returned_provider=returned_provider,
                provider_response_id=provider_resp_id,
            )

        if token_usage is None:
            return RawAdapterResponse(
                status="PROVIDER_ERROR",
                http_status_code=200,
                latency_ms=latency_ms,
                provider_envelope=raw_envelope_bytes,
                model_output_payload=None,
                error_message="Telemetria token provider assente, invalida o aritmeticamente incoerente",
                requested_model=request.model,
                returned_model=returned_model,
                returned_provider=returned_provider,
                provider_response_id=provider_resp_id,
            )

        # Estrazione completata: restituisce dual payload
        return RawAdapterResponse(
            status="SUCCESS",
            http_status_code=200,
            latency_ms=latency_ms,
            model_output_payload=content,
            provider_envelope=raw_envelope_bytes,
            token_usage=token_usage,
            requested_model=request.model,
            returned_model=returned_model,
            returned_provider=returned_provider,
            provider_response_id=provider_resp_id,
        )
