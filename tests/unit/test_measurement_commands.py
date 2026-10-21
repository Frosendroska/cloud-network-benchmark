from cloud_network_benchmark.measurement import iperf3_argv, sockperf_argv


def test_sockperf_and_short_iperf3_argv_preserve_protocol(tmp_path):
    assert sockperf_argv("10.1.2.3", tmp_path / "x.csv", 70) == [
        "sockperf", "under-load", "-i", "10.1.2.3", "-p", "11111", "-m", "64",
        "--mps", "1000", "--reply-every", "1", "--full-rtt", "--no-rdtsc",
        "--full-log", str(tmp_path / "x.csv"), "-t", "70",
    ]
    assert iperf3_argv("10.1.2.3", 4, 60) == [
        "iperf3", "-c", "10.1.2.3", "-p", "5201", "-P", "4", "-t", "60",
        "-O", "5", "-i", "1", "-l", "131072", "-C", "cubic", "-Z", "-J", "--get-server-output",
    ]


def test_day_tcp_is_single_streaming_uncapped_request():
    assert iperf3_argv("10.1.2.3", 8, 86400, day=True) == [
        "iperf3", "-c", "10.1.2.3", "-p", "5201", "-P", "8", "-t", "86400",
        "-O", "5", "-i", "1", "-l", "131072", "-C", "cubic", "-Z", "--json-stream", "--forceflush",
    ]
