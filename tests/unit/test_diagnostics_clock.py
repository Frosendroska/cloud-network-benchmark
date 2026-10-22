from datetime import datetime, timezone
from cloud_network_benchmark.diagnostics import alignment_eligibility
from cloud_network_benchmark.contracts.measurements import ClockMapping
from cloud_network_benchmark.contracts.common import Evidence, StringEvidence
from cloud_network_benchmark import diagnostics_guest as guest


def mapping(role, error, source="chronyc tracking synchronized; reference=192.0.2.1"):
    return ClockMapping(vm_role=role,monotonic_seconds=1,utc_timestamp=datetime.now(timezone.utc),
        estimated_error_ms=Evidence[float](value=error) if error is not None else Evidence[float](unavailable_reason="unknown"),
        synchronization_source=StringEvidence(value=source))


def test_pair_requires_both_sources_and_100ms_boundary():
    assert alignment_eligibility(mapping("vm_a",100),mapping("vm_b",100))[0]
    ok,reasons=alignment_eligibility(mapping("vm_a",100.001),mapping("vm_b",1))
    assert not ok and any("100 ms" in r for r in reasons)
    assert not alignment_eligibility(mapping("vm_a",1,"chronyc tracking unsynchronized; local"),mapping("vm_b",1))[0]


def test_chrony_estimate_includes_observation_brackets():
    output="""Reference ID    : C0000201 (192.0.2.1)\nStratum         : 2\nRef time (UTC)  : Wed Oct 22 12:00:00 2026\nSystem time     : 0.010 seconds fast of NTP time\nRoot delay      : 0.020000 seconds\nRoot dispersion : 0.030000 seconds\nLeap status     : Normal\n"""
    record=guest._clock_record("vm_a",5,datetime.now(timezone.utc),{"returncode":0,"stdout":output},0.01,0.02)
    assert record["estimated_error_ms"]["value"] == 70
    assert record["synchronization_source"]["value"].startswith("chronyc tracking synchronized;")


def test_invalid_or_local_clock_is_unavailable():
    record=guest._clock_record("vm_b",5,datetime.now(timezone.utc),{"returncode":0,"stdout":"Stratum : 0\nReference ID : 7F7F0101\nLeap status : Not synchronised"},0,0)
    assert record["estimated_error_ms"]["value"] is None
    assert "unsynchronized" in record["synchronization_source"]["value"]


def test_clock_query_uses_bounded_local_read_only_command_and_skips_deadline():
    class Clock:
        value=1.0
        def monotonic(self): return self.value
        def utc_now(self): return datetime.now(timezone.utc)
    calls=[]
    record=guest._mapping("vm_a",Clock(),lambda argv,timeout:(calls.append((argv,timeout)) or {"returncode":1,"stdout":"","stderr":"missing"}),deadline=1.05)
    assert calls[0][0] == ["chronyc","-n","tracking"]
    assert abs(calls[0][1] - 0.05) < 1e-12
    assert record["estimated_error_ms"]["value"] is None
    calls.clear()
    guest._mapping("vm_a",Clock(),lambda argv,timeout:(calls.append((argv,timeout)) or {}),deadline=1.0)
    assert calls == []


def test_chrony_numeric_fields_require_complete_finite_values_and_units():
    base="""Reference ID : C0000201 (192.0.2.1)
Stratum : 2
Ref time (UTC) : Wed Oct 22 12:00:00 2026
System time : 0.010 seconds fast of NTP time
Root delay : 0.020000 seconds
Root dispersion : 0.030000 seconds
Leap status : Normal
"""
    for bad in ("NaN 0.0 seconds", "0.03 seconds trailing", "Infinity seconds", "0.03 milliseconds"):
        text=base.replace("0.030000 seconds",bad)
        record=guest._clock_record("vm_a",5,datetime.now(timezone.utc),{"returncode":0,"stdout":text},0.01,0.02)
        assert record["estimated_error_ms"]["value"] is None, bad
        assert "unsynchronized" in record["synchronization_source"]["value"]
    for bad in ("2x", "0"):
        text=base.replace("Stratum : 2",f"Stratum : {bad}")
        record=guest._clock_record("vm_a",5,datetime.now(timezone.utc),{"returncode":0,"stdout":text},0.01,0.02)
        assert record["estimated_error_ms"]["value"] is None


def test_clock_invalid_reference_and_nonfinite_bracket_are_unavailable():
    text="""Reference ID : 00000000
Stratum : 2
Ref time (UTC) : Wed Oct 22 12:00:00 2026
System time : 0 seconds fast of NTP time
Root delay : 0 seconds
Root dispersion : 0 seconds
Leap status : Normal
"""
    for result, elapsed in (({"returncode":0,"stdout":text},0.0),(None,float("inf"))):
        record=guest._clock_record("vm_a",5,datetime.now(timezone.utc),result or {"returncode":0,"stdout":text},elapsed,0)
        assert record["estimated_error_ms"]["value"] is None
