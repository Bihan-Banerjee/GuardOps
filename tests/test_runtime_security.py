"""
tests/test_runtime_security.py

Tests for Phase 7 runtime security — Loki querying, Falco alert parsing,
severity mapping, and the FalcoQueryResult data model.

Matches the actual falco_reader.py API:
  - FalcoAlert(rule, priority, severity, output, pod_name, namespace,
               container_name, timestamp, tags)
  - FalcoQueryResult(success, alerts, error_message, skipped, skip_reason,
                     query_duration_seconds, loki_url, time_window)
  - query_falco_alerts(loki_url, time_window, namespace_filter, min_severity, limit)
  - _parse_loki_response(data, namespace_filter, min_severity)
  - _parse_falco_log_line(log_line, timestamp_ns_str)
  - FALCO_PRIORITY_MAP  (dict)

Uses requests (not httpx) — matches the actual import in falco_reader.py.
"""

import json
import time
import pytest
from unittest.mock import MagicMock, patch

from backend.security.falco_reader import (
    FalcoAlert,
    FalcoQueryResult,
    FALCO_PRIORITY_MAP,
    query_falco_alerts,
    _parse_loki_response,
    _parse_falco_log_line,
    _ns_to_iso,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ts_ns() -> str:
    """Return current time as nanosecond Unix timestamp string."""
    return str(int(time.time() * 1_000_000_000))


def _falco_event(
    priority="Critical",
    rule="Shell Spawned Inside Container",
    pod="test-pod",
    namespace="default",
    container="app",
    output="shell spawned",
) -> dict:
    """Build a minimal Falco JSON event dict."""
    return {
        "priority": priority,
        "rule": rule,
        "output": output,
        "time": "2024-06-15T09:23:11Z",
        "output_fields": {
            "k8s.pod.name": pod,
            "k8s.ns.name": namespace,
            "container.name": container,
        },
    }


def _loki_response(events: list[dict]) -> dict:
    """Wrap a list of Falco event dicts in a valid Loki query_range response."""
    ts = _ts_ns()
    values = [[ts, json.dumps(e)] for e in events]
    return {
        "status": "success",
        "data": {
            "resultType": "streams",
            "result": [{"stream": {"app": "falco"}, "values": values}],
        },
    }


def _mock_requests_get(mocker, response_body: dict, status_code: int = 200):
    mock_resp = MagicMock()
    mock_resp.status_code = status_code
    mock_resp.json.return_value = response_body
    mock_resp.raise_for_status = MagicMock()
    if status_code >= 400:
        import requests
        mock_resp.raise_for_status.side_effect = requests.exceptions.HTTPError(
            f"{status_code} error"
        )
    mocker.patch("backend.security.falco_reader.requests.get", return_value=mock_resp)
    return mock_resp


# ---------------------------------------------------------------------------
# FALCO_PRIORITY_MAP — every priority string
# ---------------------------------------------------------------------------

class TestFalcoPriorityMap:
    @pytest.mark.parametrize("priority,expected", [
        ("EMERGENCY",     "CRITICAL"),
        ("ALERT",         "CRITICAL"),
        ("CRITICAL",      "CRITICAL"),
        ("ERROR",         "HIGH"),
        ("WARNING",       "MEDIUM"),
        ("NOTICE",        "LOW"),
        ("INFORMATIONAL", "LOW"),
        ("INFO",          "LOW"),
        ("DEBUG",         "LOW"),
    ])
    def test_known_priorities(self, priority, expected):
        assert FALCO_PRIORITY_MAP[priority] == expected

    def test_unknown_priority_defaults_to_low_via_get(self):
        # FALCO_PRIORITY_MAP.get(unknown, "LOW") pattern used in _parse_falco_log_line
        assert FALCO_PRIORITY_MAP.get("UNKNOWN_XYZ", "LOW") == "LOW"

    def test_unknown_priority_empty_string_defaults_to_low(self):
        assert FALCO_PRIORITY_MAP.get("", "LOW") == "LOW"

    def test_map_has_all_nine_entries(self):
        assert len(FALCO_PRIORITY_MAP) == 9


# ---------------------------------------------------------------------------
# FalcoAlert
# ---------------------------------------------------------------------------

class TestFalcoAlert:
    def _make(self, priority="Critical", rule="Shell Spawned", pod="p", namespace="ns"):
        return FalcoAlert(
            rule=rule,
            priority=priority.upper(),
            severity=FALCO_PRIORITY_MAP.get(priority.upper(), "LOW"),
            output="test output",
            pod_name=pod,
            namespace=namespace,
            container_name="app",
            timestamp="2024-06-15T09:23:11Z",
        )

    def test_severity_critical(self):
        assert self._make("CRITICAL").severity == "CRITICAL"

    def test_severity_high_from_error(self):
        assert self._make("ERROR").severity == "HIGH"

    def test_severity_medium_from_warning(self):
        assert self._make("WARNING").severity == "MEDIUM"

    def test_severity_low_from_notice(self):
        assert self._make("NOTICE").severity == "LOW"

    def test_severity_low_from_info(self):
        assert self._make("INFO").severity == "LOW"

    def test_severity_low_from_debug(self):
        assert self._make("DEBUG").severity == "LOW"

    def test_fields_stored(self):
        a = self._make(pod="my-pod", namespace="prod", rule="Write to /etc")
        assert a.pod_name == "my-pod"
        assert a.namespace == "prod"
        assert a.rule == "Write to /etc"

    def test_tags_default_empty(self):
        a = self._make()
        assert a.tags == []


# ---------------------------------------------------------------------------
# FalcoQueryResult
# ---------------------------------------------------------------------------

class TestFalcoQueryResult:
    def _result(self, alerts):
        return FalcoQueryResult(
            success=True,
            alerts=alerts,
            query_duration_seconds=0.1,
            loki_url="http://loki:3100",
            time_window="1h",
        )

    def _alert(self, severity):
        sev_to_priority = {"CRITICAL": "CRITICAL", "HIGH": "ERROR",
                           "MEDIUM": "WARNING", "LOW": "NOTICE"}
        p = sev_to_priority[severity]
        return FalcoAlert(
            rule="Test Rule", priority=p, severity=severity,
            output="", pod_name="pod", namespace="default",
            container_name="app", timestamp="2024-06-15T09:00:00Z",
        )

    def test_counts_empty(self):
        r = self._result([])
        assert r.critical_count == 0
        assert r.high_count == 0
        assert r.medium_count == 0
        assert r.low_count == 0

    def test_critical_count(self):
        r = self._result([self._alert("CRITICAL"), self._alert("CRITICAL")])
        assert r.critical_count == 2

    def test_high_count(self):
        r = self._result([self._alert("HIGH")])
        assert r.high_count == 1

    def test_medium_count(self):
        r = self._result([self._alert("MEDIUM")])
        assert r.medium_count == 1

    def test_low_count(self):
        r = self._result([self._alert("LOW"), self._alert("LOW"), self._alert("LOW")])
        assert r.low_count == 3

    def test_severity_counts_dict(self):
        r = self._result([self._alert("CRITICAL"), self._alert("HIGH"), self._alert("LOW")])
        counts = r.severity_counts
        assert counts["CRITICAL"] == 1
        assert counts["HIGH"] == 1
        assert counts["LOW"] == 1
        assert counts["MEDIUM"] == 0

    def test_has_alerts_above_critical_true(self):
        r = self._result([self._alert("CRITICAL")])
        assert r.has_alerts_above("CRITICAL") is True

    def test_has_alerts_above_critical_false_when_only_high(self):
        r = self._result([self._alert("HIGH")])
        assert r.has_alerts_above("CRITICAL") is False

    def test_has_alerts_above_high_true_when_critical(self):
        r = self._result([self._alert("CRITICAL")])
        assert r.has_alerts_above("HIGH") is True

    def test_has_alerts_above_high_true_when_high(self):
        r = self._result([self._alert("HIGH")])
        assert r.has_alerts_above("HIGH") is True

    def test_has_alerts_above_high_false_when_only_medium(self):
        r = self._result([self._alert("MEDIUM")])
        assert r.has_alerts_above("HIGH") is False

    def test_has_alerts_above_medium_true(self):
        r = self._result([self._alert("MEDIUM")])
        assert r.has_alerts_above("MEDIUM") is True

    def test_has_alerts_above_low_true_for_any_alert(self):
        r = self._result([self._alert("LOW")])
        assert r.has_alerts_above("LOW") is True

    def test_has_alerts_above_false_when_empty(self):
        r = self._result([])
        for threshold in ["CRITICAL", "HIGH", "MEDIUM", "LOW"]:
            assert r.has_alerts_above(threshold) is False

    def test_skipped_result(self):
        r = FalcoQueryResult(success=False, skipped=True, skip_reason="no loki url")
        assert r.skipped is True
        assert r.critical_count == 0

    def test_failed_result(self):
        r = FalcoQueryResult(success=False, error_message="connection refused")
        assert r.success is False
        assert r.error_message == "connection refused"


# ---------------------------------------------------------------------------
# _parse_falco_log_line
# ---------------------------------------------------------------------------

class TestParseFalcoLogLine:
    def test_critical_event_parsed(self):
        event = _falco_event("Critical", "Shell Spawned Inside Container", "my-pod", "default")
        alert = _parse_falco_log_line(json.dumps(event), _ts_ns())
        assert alert is not None
        assert alert.severity == "CRITICAL"
        assert alert.rule == "Shell Spawned Inside Container"
        assert alert.pod_name == "my-pod"
        assert alert.namespace == "default"

    def test_error_priority_maps_to_high(self):
        event = _falco_event("Error", "Package Manager Executed")
        alert = _parse_falco_log_line(json.dumps(event), _ts_ns())
        assert alert.severity == "HIGH"

    def test_warning_priority_maps_to_medium(self):
        event = _falco_event("Warning", "Write to /etc")
        alert = _parse_falco_log_line(json.dumps(event), _ts_ns())
        assert alert.severity == "MEDIUM"

    def test_notice_priority_maps_to_low(self):
        event = _falco_event("Notice", "Some low rule")
        alert = _parse_falco_log_line(json.dumps(event), _ts_ns())
        assert alert.severity == "LOW"

    def test_malformed_json_returns_none(self):
        assert _parse_falco_log_line("{not valid json", _ts_ns()) is None

    def test_empty_string_returns_none(self):
        assert _parse_falco_log_line("", _ts_ns()) is None

    def test_non_json_line_returns_none(self):
        assert _parse_falco_log_line("Falco version 0.37.1", _ts_ns()) is None

    def test_missing_priority_returns_none(self):
        event = {"rule": "Shell Spawned", "output": "test", "output_fields": {}}
        assert _parse_falco_log_line(json.dumps(event), _ts_ns()) is None

    def test_missing_rule_returns_none(self):
        event = {"priority": "Critical", "output": "test", "output_fields": {}}
        assert _parse_falco_log_line(json.dumps(event), _ts_ns()) is None

    def test_underscore_field_names_supported(self):
        event = {
            "priority": "Critical",
            "rule": "Shell Spawned",
            "output": "test",
            "output_fields": {
                "k8s_pod_name": "pod-underscore",
                "k8s_ns_name": "ns-underscore",
                "container_name": "c",
            },
        }
        alert = _parse_falco_log_line(json.dumps(event), _ts_ns())
        assert alert is not None
        assert alert.pod_name == "pod-underscore"
        assert alert.namespace == "ns-underscore"

    def test_missing_output_fields_uses_unknown(self):
        event = {"priority": "Critical", "rule": "Shell Spawned", "output": "test"}
        alert = _parse_falco_log_line(json.dumps(event), _ts_ns())
        assert alert is not None
        assert alert.pod_name == "unknown"
        assert alert.namespace == "unknown"

    def test_falco_timestamp_preferred_over_loki(self):
        event = _falco_event()
        event["time"] = "2024-01-01T00:00:00Z"
        alert = _parse_falco_log_line(json.dumps(event), _ts_ns())
        assert alert.timestamp == "2024-01-01T00:00:00Z"

    def test_tags_list_parsed(self):
        event = _falco_event()
        event["tags"] = ["network", "container"]
        alert = _parse_falco_log_line(json.dumps(event), _ts_ns())
        assert "network" in alert.tags
        assert "container" in alert.tags

    def test_tags_comma_string_parsed(self):
        event = _falco_event()
        event["tags"] = "network,container,process"
        alert = _parse_falco_log_line(json.dumps(event), _ts_ns())
        assert len(alert.tags) == 3


# ---------------------------------------------------------------------------
# _parse_loki_response
# ---------------------------------------------------------------------------

class TestParseLokiResponse:
    def test_empty_result_list_returns_empty(self):
        data = {"status": "success", "data": {"result": []}}
        alerts = _parse_loki_response(data, namespace_filter=None, min_severity="LOW")
        assert alerts == []

    def test_single_critical_parsed(self):
        data = _loki_response([_falco_event("Critical", "Shell Spawned", "pod1", "default")])
        alerts = _parse_loki_response(data, namespace_filter=None, min_severity="LOW")
        assert len(alerts) == 1
        assert alerts[0].severity == "CRITICAL"

    def test_multiple_alerts_parsed(self):
        data = _loki_response([
            _falco_event("Critical", "Shell Spawned"),
            _falco_event("Warning", "Write to /etc"),
            _falco_event("Error", "Package Manager"),
        ])
        alerts = _parse_loki_response(data, namespace_filter=None, min_severity="LOW")
        assert len(alerts) == 3

    def test_sorted_by_severity_descending(self):
        data = _loki_response([
            _falco_event("Notice", "Low Rule"),
            _falco_event("Critical", "Shell Spawned"),
            _falco_event("Warning", "Write to /etc"),
        ])
        alerts = _parse_loki_response(data, namespace_filter=None, min_severity="LOW")
        assert alerts[0].severity == "CRITICAL"
        assert alerts[-1].severity == "LOW"

    def test_namespace_filter_applied(self):
        data = _loki_response([
            _falco_event(namespace="default"),
            _falco_event(namespace="monitoring"),
            _falco_event(namespace="default"),
        ])
        alerts = _parse_loki_response(data, namespace_filter="default", min_severity="LOW")
        assert len(alerts) == 2
        assert all(a.namespace == "default" for a in alerts)

    def test_min_severity_filters_low(self):
        data = _loki_response([
            _falco_event("Notice"),
            _falco_event("Critical"),
        ])
        alerts = _parse_loki_response(data, namespace_filter=None, min_severity="HIGH")
        assert len(alerts) == 1
        assert alerts[0].severity == "CRITICAL"

    def test_min_severity_critical_only(self):
        data = _loki_response([
            _falco_event("Error"),
            _falco_event("Warning"),
            _falco_event("Critical"),
        ])
        alerts = _parse_loki_response(data, namespace_filter=None, min_severity="CRITICAL")
        assert len(alerts) == 1

    def test_malformed_log_lines_skipped(self):
        ts = _ts_ns()
        data = {
            "status": "success",
            "data": {
                "result": [{"stream": {}, "values": [
                    [ts, "not json {{{"],
                    [ts, json.dumps(_falco_event("Critical"))],
                ]}]
            }
        }
        alerts = _parse_loki_response(data, namespace_filter=None, min_severity="LOW")
        assert len(alerts) == 1

    def test_multiple_streams_merged(self):
        ts = _ts_ns()
        stream1 = {"stream": {}, "values": [[ts, json.dumps(_falco_event("Critical"))]]}
        stream2 = {"stream": {}, "values": [[ts, json.dumps(_falco_event("Warning"))]]}
        data = {"status": "success", "data": {"result": [stream1, stream2]}}
        alerts = _parse_loki_response(data, namespace_filter=None, min_severity="LOW")
        assert len(alerts) == 2


# ---------------------------------------------------------------------------
# query_falco_alerts — HTTP layer
# ---------------------------------------------------------------------------

class TestQueryFalcoAlerts:
    def test_empty_loki_url_returns_skipped(self):
        result = query_falco_alerts("", time_window="1h")
        assert result.skipped is True
        assert result.success is False
        assert result.critical_count == 0

    def test_successful_query_returns_alerts(self, mocker):
        _mock_requests_get(mocker, _loki_response([_falco_event("Critical")]))
        result = query_falco_alerts("http://localhost:3100", time_window="1h")
        assert result.success is True
        assert result.critical_count == 1

    def test_successful_query_empty_response(self, mocker):
        _mock_requests_get(mocker, {"status": "success", "data": {"result": []}})
        result = query_falco_alerts("http://localhost:3100", time_window="1h")
        assert result.success is True
        assert result.critical_count == 0

    def test_connection_error_returns_failed_result(self, mocker):
        import requests
        mocker.patch(
            "backend.security.falco_reader.requests.get",
            side_effect=requests.exceptions.ConnectionError("refused"),
        )
        result = query_falco_alerts("http://localhost:3100", time_window="1h")
        assert result.success is False
        assert result.critical_count == 0
        assert "connect" in result.error_message.lower()

    def test_timeout_returns_failed_result(self, mocker):
        import requests
        mocker.patch(
            "backend.security.falco_reader.requests.get",
            side_effect=requests.exceptions.Timeout(),
        )
        result = query_falco_alerts("http://localhost:3100", time_window="1h")
        assert result.success is False
        assert "timed out" in result.error_message.lower()

    def test_http_error_returns_failed_result(self, mocker):
        _mock_requests_get(mocker, {}, status_code=503)
        result = query_falco_alerts("http://localhost:3100", time_window="1h")
        assert result.success is False

    def test_loki_non_success_status_returns_failed(self, mocker):
        _mock_requests_get(mocker, {"status": "error", "error": "parse error"})
        result = query_falco_alerts("http://localhost:3100", time_window="1h")
        assert result.success is False
        assert "non-success" in result.error_message.lower()

    def test_malformed_json_response_returns_failed(self, mocker):
        import json
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        # json.JSONDecodeError is a subclass of ValueError — use it directly
        # because falco_reader.py catches json.JSONDecodeError specifically
        mock_resp.json.side_effect = json.JSONDecodeError("not json", "garbage", 0)
        mock_resp.raise_for_status = MagicMock()
        mock_resp.text = "garbage"
        mocker.patch("backend.security.falco_reader.requests.get", return_value=mock_resp)
        result = query_falco_alerts("http://localhost:3100", time_window="1h")
        assert result.success is False

    def test_loki_url_stored_in_result(self, mocker):
        _mock_requests_get(mocker, {"status": "success", "data": {"result": []}})
        result = query_falco_alerts("http://loki.monitoring:3100", time_window="1h")
        assert result.loki_url == "http://loki.monitoring:3100"

    def test_time_window_stored_in_result(self, mocker):
        _mock_requests_get(mocker, {"status": "success", "data": {"result": []}})
        result = query_falco_alerts("http://localhost:3100", time_window="24h")
        assert result.time_window == "24h"

    def test_query_duration_recorded(self, mocker):
        _mock_requests_get(mocker, {"status": "success", "data": {"result": []}})
        result = query_falco_alerts("http://localhost:3100", time_window="1h")
        assert result.query_duration_seconds >= 0

    def test_namespace_filter_passed_through(self, mocker):
        _mock_requests_get(mocker, _loki_response([
            _falco_event("Critical", namespace="default"),
            _falco_event("Critical", namespace="monitoring"),
        ]))
        result = query_falco_alerts(
            "http://localhost:3100", time_window="1h", namespace_filter="default"
        )
        assert result.success is True
        assert all(a.namespace == "default" for a in result.alerts)

    def test_min_severity_filter_passed_through(self, mocker):
        _mock_requests_get(mocker, _loki_response([
            _falco_event("Notice"),
            _falco_event("Critical"),
        ]))
        result = query_falco_alerts(
            "http://localhost:3100", time_window="1h", min_severity="CRITICAL"
        )
        assert result.critical_count == 1
        assert result.low_count == 0

    @pytest.mark.parametrize("time_window", ["15m", "30m", "1h", "3h", "6h", "12h", "24h", "7d"])
    def test_all_time_windows_accepted(self, mocker, time_window):
        _mock_requests_get(mocker, {"status": "success", "data": {"result": []}})
        result = query_falco_alerts("http://localhost:3100", time_window=time_window)
        assert result.success is True
        assert result.time_window == time_window


# ---------------------------------------------------------------------------
# _ns_to_iso utility
# ---------------------------------------------------------------------------

class TestNsToIso:
    def test_valid_nanosecond_timestamp(self):
        ts_ns = "1718441591000000001"
        result = _ns_to_iso(ts_ns)
        assert "2024" in result
        assert "T" in result
        assert "Z" in result

    def test_invalid_string_returned_as_is(self):
        assert _ns_to_iso("not-a-number") == "not-a-number"

    def test_empty_string_returned_as_is(self):
        assert _ns_to_iso("") == ""