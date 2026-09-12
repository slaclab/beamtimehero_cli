"""Client for the research sandbox HTTP service (`research_docker`).

Wraps `POST /research`, which answers an analysis question by running an
agent with web access inside a locked-down container and hands back a
Markdown report plus any figures it drew.

Modelled on :mod:`beamtimehero_cli.spec_eval`, and for the same reasons:
the service is a sibling repo that is not bundled here, the URL is pinned
to loopback, and nothing in this module raises — every failure comes back
as an ``error`` field so the handler can hand it straight to the agent.

Two things make this client different from every other one in the package.

* **The report is untrusted.** The sandbox reads the open web; anything it
  reads can contain text aimed at whatever reads the report. The caller
  wraps it in an ``<untrusted-report>`` envelope before an agent sees it
  (see ``tools_core.t_ask_question``) — this module returns the raw
  text and records that it is untrusted.
* **The tool is off by default.** ``RESEARCH_SANDBOX_ENABLED`` gates it,
  and a disabled call returns before the URL is even examined.
"""
from __future__ import annotations

import logging
from typing import Any, TypedDict
from urllib.parse import urlsplit

import requests

from beamtimehero_cli.config import RESEARCH_SANDBOX_URL

logger = logging.getLogger(__name__)

DEFAULT_API_URL = RESEARCH_SANDBOX_URL

# A research run is wall-clock bounded by the service (`budget.wall_s`,
# max 3600). The HTTP timeout has to sit comfortably above the largest
# budget the caller can ask for, or the client gives up on a run that is
# still going and the container is left to finish into a dropped socket.
_HTTP_TIMEOUT = 3900

# Same pin, same reasoning as spec-eval: the request body is a free-text
# question that the service turns into a container run with a gateway
# credential and network egress. A host with that endpoint open is a
# remote code execution service, so the URL is pinned here rather than
# trusted from RESEARCH_SANDBOX_URL — a typo, a copied production config
# or an injected environment variable must not be able to redirect the
# question, or the credential the answer is billed to, off-box.
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})

# Bypass the HTTP proxy for all sandbox traffic (always localhost).
_session = requests.Session()
_session.trust_env = False


class ResearchUsage(TypedDict):
    turns: int
    input_tokens: int
    output_tokens: int
    wall_s: float
    hit_cap: str | None


class ResearchResult(TypedDict):
    ok: bool
    run_id: str | None
    report: str              # UNTRUSTED third-party text — see module docstring
    figures: list[str]       # figure filenames written to the run's plots dir
    usage: ResearchUsage | None
    exit_code: int | None
    error: str | None


def _error_result(message: str) -> ResearchResult:
    return ResearchResult(
        ok=False,
        run_id=None,
        report="",
        figures=[],
        usage=None,
        exit_code=None,
        error=message,
    )


def ask_question(
    question: str,
    *,
    experiment_id: str | None = None,
    scan_dir: str | None = None,
    wall_s: int = 900,
    max_turns: int = 40,
    max_tokens: int | None = None,
    api_url: str = DEFAULT_API_URL,
    enabled: bool | None = None,
) -> ResearchResult:
    """Ask the research sandbox a question and return its report.

    ``api_url`` must be a loopback address. ``enabled`` defaults to
    ``config.RESEARCH_SANDBOX_ENABLED``, read at call time so a test (or a
    process that sets the variable late) sees the current value. Never
    raises: a disabled tool, a rejected URL and an unreachable service are
    all reported through the ``error`` field.

    The ``report`` field is untrusted third-party text. Do not hand it to
    an agent without a wrapper saying so.
    """
    if enabled is None:
        # Read through the module, not from a from-import binding, so the
        # flag can be flipped in-process (tests, and a host that sets it
        # after this module is first imported).
        from beamtimehero_cli import config as _config

        enabled = _config.RESEARCH_SANDBOX_ENABLED

    if not enabled:
        return _error_result(
            "The research sandbox is disabled. Set RESEARCH_SANDBOX_ENABLED=1 "
            "in this process's environment to switch it on, and make sure the "
            f"research-sandbox service is running at {api_url} (see "
            "`beamtimehero ref research-sandbox`). It is off by default "
            "because the sandbox reads the open web and returns text an agent "
            "will act on, so it is a per-deployment decision rather than "
            "something that turns itself on when a port answers."
        )

    host = urlsplit(api_url).hostname
    if host not in LOOPBACK_HOSTS:
        # Return before building the request: nothing is sent to a
        # non-loopback host, not even a connection attempt.
        return _error_result(
            f"research-sandbox URL must be loopback: {api_url!r} has host "
            f"{host!r}, expected one of {sorted(LOOPBACK_HOSTS)}. This tool "
            f"posts a free-text question to a service that runs it in a "
            f"container with a gateway credential, so the service is only "
            f"ever addressed on this machine. Fix RESEARCH_SANDBOX_URL."
        )

    budget: dict[str, Any] = {"wall_s": wall_s, "max_turns": max_turns}
    if max_tokens is not None:
        budget["max_tokens"] = max_tokens
    payload: dict[str, Any] = {"question": question, "budget": budget}
    if experiment_id:
        payload["experiment_id"] = experiment_id
    if scan_dir:
        payload["scan_dir"] = scan_dir

    url = api_url.rstrip("/") + "/research"

    try:
        resp = _session.post(url, json=payload, timeout=_HTTP_TIMEOUT)
    except requests.RequestException as e:
        logger.warning("research-sandbox transport error: %s", e)
        return _error_result(
            f"transport error: {e}. This tool needs the research-sandbox "
            f"service at {api_url}, which is a separate repo "
            f"(`research_docker`): a FastAPI front end on loopback plus a "
            f"Docker image, an egress proxy and a model-gateway credential. "
            f"It is not bundled with this package, so without it this leaf is "
            f"unavailable and every other tool still works — see "
            f"`beamtimehero ref research-sandbox`."
        )

    if resp.status_code >= 500:
        return _error_result(f"server error {resp.status_code}: {resp.text[:500]}")
    if resp.status_code >= 400:
        try:
            detail = resp.json().get("detail", resp.text)
        except ValueError:
            detail = resp.text
        return _error_result(f"bad request ({resp.status_code}): {detail}")

    try:
        data = resp.json()
    except ValueError:
        return _error_result(
            f"research-sandbox returned a non-JSON body: {resp.text[:500]}"
        )

    raw_usage = data.get("usage") or {}
    usage = ResearchUsage(
        turns=int(raw_usage.get("turns") or 0),
        input_tokens=int(raw_usage.get("input_tokens") or 0),
        output_tokens=int(raw_usage.get("output_tokens") or 0),
        wall_s=float(raw_usage.get("wall_s") or 0.0),
        hit_cap=raw_usage.get("hit_cap"),
    )
    exit_code = data.get("exit_code")
    report = data.get("report", "")
    run_id = data.get("run_id")

    # A 200 with a non-zero exit code is a run that started and went
    # wrong — the commonest cause being a gateway that is not configured,
    # which looks like an immediate exit and an empty report. Give it an
    # `error` string rather than leaving `ok: false, error: null`, which
    # tells the caller nothing and cannot be acted on. The report itself
    # is still returned: a wall-clock kill exits non-zero with whatever
    # the agent had written so far, and throwing that away would discard
    # the part of the run that was paid for.
    error: str | None = None
    if exit_code != 0:
        hit = (usage or {}).get("hit_cap")
        error = (
            f"the sandbox run exited {exit_code}"
            + (f" after hitting its {hit} budget" if hit else "")
            + (". A partial report is included below. " if report else
               " and produced no report. ")
            + (f"Full stderr: GET {api_url.rstrip('/')}/runs/{run_id}/log. "
               if run_id else "")
            + "An immediate non-zero exit is usually an unconfigured model "
            f"gateway on the service host; check {api_url.rstrip('/')}/healthz."
        )

    return ResearchResult(
        ok=(exit_code == 0),
        run_id=run_id,
        report=report,
        figures=list(data.get("figures") or []),
        usage=usage,
        exit_code=exit_code,
        error=error,
    )
