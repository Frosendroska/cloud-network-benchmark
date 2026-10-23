from datetime import datetime, timezone

from cloud_network_benchmark import diagnostics_guest as guest
from cloud_network_benchmark.diagnostics import build_collection_context
from cloud_network_benchmark.contracts.common import StructuredEvidence
from types import SimpleNamespace


def snap(cpu=(100,20,40,800,5,10,5,2), cores=None, tcp=4, nic=None):
    nic = nic or dict(rx_dropped=0,tx_dropped=0,rx_errors=0,tx_errors=0,rx_bytes=0,tx_bytes=0)
    return {"cpu":{"cpu":list(cpu), **(cores or {"cpu0":list(cpu), "cpu1":list(cpu)})}, "tcp":tcp,"nic":nic}


def test_cpu_and_counter_deltas_keep_zero_and_large_integers():
    old=snap()
    new=snap(cpu=(110,22,44,820,5,12,7,3),tcp=11,nic=dict(rx_dropped=0,tx_dropped=2,rx_errors=0,tx_errors=1,rx_bytes=10**19,tx_bytes=20))
    row=guest.derive_sample(old,new,deployment_id="RUN-deployment",vm_role="vm_a",sampled_at=datetime.now(timezone.utc),monotonic_seconds=2,interval_seconds=1)
    assert abs(row["cpu_total_percent"]["value"] - 100*20/41) < 1e-9
    assert abs(row["cpu_steal_percent"]["value"] - 100/41) < 1e-9
    assert abs(row["cpu_max_core_percent"]["value"] - 100*20/41) < 1e-9
    assert row["tcp_retransmissions"]["value"] == 7
    assert row["interface_rx_drops"]["value"] == 0
    assert row["rx_bytes"]["value"] == 10**19


def test_resets_and_core_change_are_unavailable_without_wrapping():
    old=snap(cpu=(100,20,40,800,5,10,5,2), tcp=99)
    new=snap(cpu=(90,20,40,800,5,10,5,2), tcp=1, cores={"cpu0":[51,10,20,400,2,5,3,1]})
    row=guest.derive_sample(old,new,deployment_id="RUN-deployment",vm_role="vm_b",sampled_at=datetime.now(timezone.utc),monotonic_seconds=3,interval_seconds=2)
    assert row["cpu_total_percent"]["value"] is None
    assert row["cpu_max_core_percent"]["value"] is None
    assert row["tcp_retransmissions"]["value"] is None
    assert {"cpu_total_percent","tcp_retransmissions"} <= set(row["reset_fields"])
    assert row["cpu_max_core_percent"]["unavailable_reason"]


def test_rooted_linux_fixture_and_named_tcp_counter(tmp_path):
    import shutil
    from pathlib import Path
    shutil.copytree(Path(__file__).parents[1]/"fixtures/f04", tmp_path, dirs_exist_ok=True)
    result=guest.read_linux_snapshot("eth0",root=tmp_path)
    assert result["tcp"] == 4
    assert result["cpu"]["cpu0"][0] == 50
    assert result["nic"]["rx_bytes"] == 100


def test_rooted_interface_symlink_reads_mtu_statistics_and_lower_vf_links(tmp_path):
    import shutil
    from pathlib import Path
    root=tmp_path/"fixture"; shutil.copytree(Path(__file__).parents[1]/"fixtures/f04",root)
    class_net=root/"sys/class/net"; nic=root/"sys/devices/net/eth0"
    nic.parent.mkdir(parents=True)
    shutil.move(str(class_net/"eth0"),str(nic))
    (class_net/"eth0").symlink_to("/sys/devices/net/eth0")
    (nic/"device").mkdir(parents=True,exist_ok=True)
    (nic/"device/driver").symlink_to("/sys/bus/pci/drivers/hv_netvsc")
    (root/"sys/bus/pci/drivers/hv_netvsc").mkdir(parents=True)
    vf=root/"sys/devices/pci/vf0"; (vf/"device").mkdir(parents=True)
    (vf/"device/driver").symlink_to("/sys/bus/pci/drivers/ena")
    (root/"sys/bus/pci/drivers/ena").mkdir(parents=True)
    (nic/"lower_vf0").symlink_to("/sys/devices/pci/vf0")
    observed=guest.read_linux_snapshot("eth0",root=root)
    assert observed["nic"]["rx_bytes"] == 100
    (nic/"lower_vf_relative").symlink_to("../../pci/vf0")
    vm=SimpleNamespace(image_identity="image",region="region",zone="zone",physical_zone_id={"value":None,"unavailable_reason":"not exposed"},actual_shape="shape")
    context=build_collection_context(SimpleNamespace(schema_version=2,deployment_id="RUN-deployment",identity=SimpleNamespace(deployment_id="RUN-deployment"),provider="azure",vm_a=vm,vm_b=vm,documented_network_limit=StructuredEvidence(value={"source":"docs"})),role="vm_a",interface="eth0",output_root=root,duration_seconds=60)
    metadata=guest.read_static_metadata(context,root=root,run_command=lambda *_:{"returncode":1,"stdout":"","stderr":"missing"})
    assert metadata["mtu"]["value"] == 9000
    assert metadata["acceleration"]["value"] == "observed linked VF driver: ena"
    nic_relative=root/"sys/devices/net/eth1"; (nic_relative/"statistics").mkdir(parents=True)
    (nic_relative/"mtu").write_text("9000")
    for counter in ("rx_dropped","tx_dropped","rx_errors","tx_errors","rx_bytes","tx_bytes"):
        shutil.copyfile(nic/"statistics"/counter,nic_relative/"statistics"/counter)
    (class_net/"eth1").symlink_to("../../devices/net/eth1")
    assert guest.read_linux_snapshot("eth1",root=root)["nic"]["rx_bytes"] == 100


def test_rooted_interface_symlink_missing_and_escape_targets_are_unavailable(tmp_path):
    import shutil
    from pathlib import Path
    root=tmp_path/"fixture"; shutil.copytree(Path(__file__).parents[1]/"fixtures/f04",root)
    net=root/"sys/class/net"; shutil.rmtree(net/"eth0")
    (net/"eth0").symlink_to("/sys/devices/net/missing")
    assert guest.read_linux_snapshot("eth0",root=root)["nic"]["rx_bytes"]["error"]
    (net/"eth0").unlink(); (net/"eth0").symlink_to("/etc")
    observed=guest.read_linux_snapshot("eth0",root=root)
    assert observed["nic"]["rx_bytes"]["error"]


def test_counter_recovers_from_reset_using_current_value_as_new_baseline():
    old=snap(tcp=90); reset=snap(tcp=4); recovered=snap(tcp=8)
    first=guest.derive_sample(old,reset,deployment_id="RUN-deployment",vm_role="vm_a",sampled_at=datetime.now(timezone.utc),monotonic_seconds=1,interval_seconds=1)
    second=guest.derive_sample(reset,recovered,deployment_id="RUN-deployment",vm_role="vm_a",sampled_at=datetime.now(timezone.utc),monotonic_seconds=2,interval_seconds=1)
    assert first["tcp_retransmissions"]["value"] is None
    assert second["tcp_retransmissions"]["value"] == 4


def test_context_uses_provider_observations_and_rejects_build_identity_mismatch(tmp_path):
    for provider in ("aws","azure","gcp"):
        vm=SimpleNamespace(image_identity="observed-image",region="observed-region",zone="observed-zone",
            physical_zone_id={"value":"physical-zone","unavailable_reason":None},actual_shape="observed-shape")
        identity=SimpleNamespace(deployment_id="RUN-deployment")
        deployment=SimpleNamespace(schema_version=2,deployment_id="RUN-deployment",identity=identity,provider=provider,
            vm_a=vm,vm_b=vm,documented_network_limit=StructuredEvidence(value={"source":"dated docs","limit":"provider-specific"}))
        context=build_collection_context(deployment,role="vm_a",interface="eth0",output_root=tmp_path,duration_seconds=60)
        assert context["metadata_seed"]["image"]["value"] == "observed-image"
        assert context["metadata_seed"]["documented_network_limit"]["value"]["source"] == "dated docs"
        bad=SimpleNamespace(deployment_id="OTHER",identity=identity,builds=[])
        try: build_collection_context(deployment,role="vm_a",interface="eth0",output_root=tmp_path,duration_seconds=60,prepared=bad)
        except ValueError as exc: assert "identity" in str(exc)
        else: raise AssertionError("mismatched bootstrap identity accepted")


def test_metadata_reads_actual_fixture_values_and_preserves_unknown_driver():
    from pathlib import Path
    from cloud_network_benchmark.contracts.measurements import VmMetadata
    for provider in ("aws","azure","gcp"):
        context=build_collection_context(
            SimpleNamespace(schema_version=2,deployment_id="RUN-deployment",identity=SimpleNamespace(deployment_id="RUN-deployment"),
                provider=provider,vm_a=SimpleNamespace(image_identity=f"{provider}-image",region="region",zone="zone",physical_zone_id={"value":None,"unavailable_reason":"not exposed"},actual_shape="shape"),
                vm_b=None,documented_network_limit=StructuredEvidence(value={"source":"dated source","semantics":provider})),
            role="vm_a",interface="eth0",output_root=Path(__file__).parents[1],duration_seconds=60)
        metadata=guest.read_static_metadata(context,root=Path(__file__).parents[1]/"fixtures/f04",run_command=lambda *_:{"returncode":1,"stdout":"","stderr":"tool unavailable"})
        metadata.update(schema_version=2,provider=provider,deployment_id="RUN-deployment",vm_role="vm_a")
        parsed=VmMetadata.model_validate(metadata)
        assert parsed.cpu_model.value == "Synthetic Cloud CPU"
        assert parsed.kernel.value == "6.8.0-f04-fixture"
        assert parsed.mtu.value == 9000
        assert parsed.congestion_control.value == "cubic"
        assert parsed.nic_driver.value is None
        assert parsed.acceleration.value is None
        assert parsed.image.value == f"{provider}-image"
        assert parsed.documented_network_limit.value["semantics"] == provider


def test_azure_netvsc_requires_and_reports_linked_vf_driver(tmp_path):
    from pathlib import Path
    root=Path(__file__).parents[1]/"fixtures/f04"
    temp=tmp_path/"root"; __import__("shutil").copytree(root,temp)
    net=temp/"sys/class/net/eth0"; (net/"device").mkdir(parents=True,exist_ok=True)
    (net/"device/driver").symlink_to("/sys/bus/pci/drivers/hv_netvsc")
    (temp/"sys/bus/pci/drivers/hv_netvsc").mkdir(parents=True)
    vf=net/"vf0"; (vf/"device").mkdir(parents=True)
    (vf/"device/driver").symlink_to("/sys/bus/pci/drivers/ena")
    (net/"lower_vf0").symlink_to("vf0")
    (temp/"sys/bus/pci/drivers/ena").mkdir(parents=True)
    assert guest._rooted_link_name(net/"device/driver",temp) == "hv_netvsc"
    assert guest._acceleration(temp,"eth0","azure",{"value":"hv_netvsc"})["value"] == "observed linked VF driver: ena"
    (temp/"sys/bus/pci/drivers/escape").symlink_to("/etc")
    try: guest._rooted_link_name(temp/"sys/bus/pci/drivers/escape",temp)
    except OSError: pass
    else: raise AssertionError("escaping absolute sysfs symlink was followed")
    (net/"lower_missing").symlink_to("does-not-exist")
    (net/"lower_vf0").unlink()
    assert guest._acceleration(temp,"eth0","azure",{"value":"hv_netvsc"})["value"] is None


def test_static_metadata_marks_bad_mtu_and_empty_required_strings_unavailable(tmp_path):
    import shutil
    from pathlib import Path
    from cloud_network_benchmark.contracts.measurements import VmMetadata
    for value in ("0", "-1", "not-an-integer"):
        root=tmp_path/("root-"+value.replace("/", "_")); shutil.copytree(Path(__file__).parents[1]/"fixtures/f04",root)
        (root/"sys/class/net/eth0/mtu").write_text(value)
        (root/"proc/sys/kernel/osrelease").write_text("  \n")
        (root/"proc/sys/net/ipv4/tcp_congestion_control").write_text(" \n")
        (root/"proc/cpuinfo").write_text("processor : 0\nmodel name :   \n")
        vm=SimpleNamespace(image_identity="image",region="region",zone="zone",physical_zone_id={"value":None,"unavailable_reason":"not exposed"},actual_shape="shape")
        context=build_collection_context(SimpleNamespace(schema_version=2,deployment_id="RUN-deployment",identity=SimpleNamespace(deployment_id="RUN-deployment"),provider="aws",vm_a=vm,vm_b=vm,documented_network_limit=StructuredEvidence(value={"source":"docs"})),role="vm_a",interface="eth0",output_root=root,duration_seconds=60)
        metadata=guest.read_static_metadata(context,root=root,run_command=lambda *_:{"returncode":1,"stdout":"","stderr":"missing"})
        metadata.update(schema_version=2,deployment_id="RUN-deployment",vm_role="vm_a",provider="aws")
        parsed=VmMetadata.model_validate(metadata)
        assert parsed.mtu.value is None and parsed.mtu.unavailable_reason
        assert parsed.cpu_model.value is None and parsed.kernel.value is None and parsed.congestion_control.value is None
    valid=Path(__file__).parents[1]/"fixtures/f04"
    assert (valid/"sys/class/net/eth0/mtu").read_text().strip() == "9000"


def test_binary_hash_is_incremental_and_budget_exhaustion_is_unknown(tmp_path):
    import hashlib
    binary=tmp_path/"tool"; binary.write_bytes(b"x"*150000)
    assert guest._hash_binary(binary,lambda:10,chunk_size=1024) == hashlib.sha256(binary.read_bytes()).hexdigest()
    remaining=iter((10,10,0))
    assert guest._hash_binary(binary,lambda:next(remaining),chunk_size=1024) is None


def test_static_binary_identity_materializes_supplied_comparison_and_deadline_unknown(tmp_path, monkeypatch):
    import hashlib
    from pathlib import Path
    binary=tmp_path/"synthetic-tool"; binary.write_bytes(b"synthetic executable bytes")
    context={"interface":"eth0","metadata_seed":{"source_builds":{"value":{
        "sockperf":{"binary_sha256":{"value":"incorrect","unavailable_reason":None},"binary_version":"v1"},
        "iperf3":{"binary_sha256":{"value":hashlib.sha256(binary.read_bytes()).hexdigest(),"unavailable_reason":None},"binary_version":"v1"}},"unavailable_reason":None}}}
    monkeypatch.setattr(guest,"shutil_which",lambda _tool:str(binary))
    metadata=guest.read_static_metadata(context,root=Path(__file__).parents[1]/"fixtures/f04",run_command=lambda *_:{"returncode":0,"stdout":"v1\n","stderr":""},remaining=lambda:5)
    assert metadata["tool_versions"]["value"]["sockperf"]["binary_sha256"]["value"] == hashlib.sha256(binary.read_bytes()).hexdigest()
    assert metadata["source_builds"]["value"]["sockperf"]["matches_bootstrap_binary"]["value"] is False
    assert metadata["source_builds"]["value"]["iperf3"]["matches_bootstrap_binary"]["value"] is True
    exhausted=guest.read_static_metadata(context,root=Path(__file__).parents[1]/"fixtures/f04",run_command=lambda *_:{"returncode":0,"stdout":"v1\n","stderr":""},remaining=lambda:0)
    for tool in ("sockperf","iperf3"):
        assert exhausted["tool_versions"]["value"][tool]["binary_sha256"]["value"] is None


def test_cpu_model_mixture_and_interface_traversal_are_preserved(tmp_path):
    from pathlib import Path
    import shutil
    root=Path(__file__).parents[1]/"fixtures/f04"
    temp=tmp_path/"fixture"
    shutil.copytree(root,temp,dirs_exist_ok=True)
    (temp/"proc/cpuinfo").write_text("processor : 0\nmodel name : CPU-A\n\nprocessor : 1\nmodel name : CPU-B\n")
    vm=SimpleNamespace(image_identity="image",region="region",zone="zone",physical_zone_id={"value":None,"unavailable_reason":"not exposed"},actual_shape="shape")
    context=build_collection_context(SimpleNamespace(schema_version=2,deployment_id="RUN-deployment",identity=SimpleNamespace(deployment_id="RUN-deployment"),provider="aws",vm_a=vm,vm_b=vm,documented_network_limit=StructuredEvidence(value={"source":"docs"})),role="vm_a",interface="eth0",output_root=temp,duration_seconds=60)
    metadata=guest.read_static_metadata(context,root=temp,run_command=lambda *_:{"returncode":1,"stdout":"","stderr":"missing"})
    assert metadata["cpu_model"]["value"] == "CPU-A; CPU-B"
    try: guest.read_linux_snapshot("../eth0",root="/tmp")
    except ValueError as exc: assert "interface" in str(exc)
    else: raise AssertionError("interface traversal accepted")


def test_malformed_tcp_header_does_not_hide_nic_values():
    try:
        guest.parse_tcp_retrans("Tcp: A B\nTcp: 1 2\n")
    except ValueError as exc:
        assert "RetransSegs" in str(exc)
    else:
        raise AssertionError("missing named TCP counter accepted")


def test_aggregate_cpu_survives_malformed_core_but_maximum_is_unavailable_and_recovers():
    old = snap(cores={"cpu0":[100,20,40,800,5,10,5,2], "cpu1":[100,20,40,800,5,10,5,2]})
    malformed = {"cpu":{"cpu":[110,22,44,820,5,12,7,3], "cpu0":[110,22,44,820,5,12,7,3], "cpu1":{"error":"bad core row"}},"tcp":5,"nic":old["nic"]}
    row=guest.derive_sample(old,malformed,deployment_id="RUN-deployment",vm_role="vm_a",sampled_at=datetime.now(timezone.utc),monotonic_seconds=2,interval_seconds=1)
    assert row["cpu_total_percent"]["value"] is not None
    assert row["cpu_steal_percent"]["value"] is not None
    assert row["cpu_max_core_percent"]["value"] is None
    assert guest.parse_proc_stat("cpu 1 2 3 4 5 6 7 8\ncpu0 1 2 3 4 5 6 7 8\ncpu1 nope\n")["cpu1"]["error"]
    recovered=snap(cpu=(120,24,48,840,5,14,9,4))
    recovered_row=guest.derive_sample(malformed,recovered,deployment_id="RUN-deployment",vm_role="vm_a",sampled_at=datetime.now(timezone.utc),monotonic_seconds=3,interval_seconds=1)
    assert recovered_row["cpu_max_core_percent"]["value"] is None
    recovered_row=guest.derive_sample(snap(),recovered,deployment_id="RUN-deployment",vm_role="vm_a",sampled_at=datetime.now(timezone.utc),monotonic_seconds=4,interval_seconds=1)
    assert recovered_row["cpu_max_core_percent"]["value"] is not None


def test_aggregate_only_snapshot_never_substitutes_aggregate_for_max_core():
    old={"cpu":{"cpu":[100,20,40,800,5,10,5,2]},"tcp":1,"nic":{}}
    new={"cpu":{"cpu":[110,22,44,820,5,12,7,3]},"tcp":2,"nic":{}}
    row=guest.derive_sample(old,new,deployment_id="RUN-deployment",vm_role="vm_a",sampled_at=datetime.now(timezone.utc),monotonic_seconds=1,interval_seconds=1)
    assert row["cpu_total_percent"]["value"] is not None
    assert row["cpu_max_core_percent"]["value"] is None


def test_rooted_bad_aggregate_keeps_valid_core_maximum_and_recovers(tmp_path):
    import shutil
    from pathlib import Path
    root=tmp_path/"fixture"; shutil.copytree(Path(__file__).parents[1]/"fixtures/f04",root)
    stat=root/"proc/stat"
    stat.write_text("cpu broken\ncpu0 100 20 40 800 5 10 5 2\ncpu1 200 20 40 800 5 10 5 2\n")
    current=guest.read_linux_snapshot("eth0",root=root)
    assert current["cpu"]["cpu"]["error"]
    previous={"cpu":{"cpu":[90,20,40,790,5,9,4,1],"cpu0":[90,20,40,790,5,9,4,1],"cpu1":[190,20,40,790,5,9,4,1]},"tcp":3,"nic":current["nic"]}
    row=guest.derive_sample(previous,current,deployment_id="RUN-deployment",vm_role="vm_a",sampled_at=datetime.now(timezone.utc),monotonic_seconds=1,interval_seconds=1)
    assert row["cpu_total_percent"]["value"] is None and row["cpu_steal_percent"]["value"] is None
    assert row["cpu_max_core_percent"]["value"] is not None
    stat.write_text("cpu0 110 22 44 810 5 12 7 3\ncpu1 210 22 44 810 5 12 7 3\n")
    missing=guest.read_linux_snapshot("eth0",root=root)
    row=guest.derive_sample(current,missing,deployment_id="RUN-deployment",vm_role="vm_a",sampled_at=datetime.now(timezone.utc),monotonic_seconds=1.5,interval_seconds=.5)
    assert row["cpu_total_percent"]["value"] is None and row["cpu_steal_percent"]["value"] is None
    assert row["cpu_max_core_percent"]["value"] is not None
    stat.write_text("cpu 110 22 44 810 5 12 7 3\ncpu0 110 22 44 810 5 12 7 3\ncpu1 210 22 44 810 5 12 7 3\n")
    recovered=guest.read_linux_snapshot("eth0",root=root)
    row=guest.derive_sample(current,recovered,deployment_id="RUN-deployment",vm_role="vm_a",sampled_at=datetime.now(timezone.utc),monotonic_seconds=2,interval_seconds=1)
    assert row["cpu_total_percent"]["value"] is None
    assert row["cpu_max_core_percent"]["value"] is not None
    valid_next={"cpu":{"cpu":[120,24,48,820,5,14,9,4],"cpu0":[120,24,48,820,5,14,9,4],"cpu1":[220,24,48,820,5,14,9,4]},"tcp":6,"nic":recovered["nic"]}
    assert guest.derive_sample(recovered,valid_next,deployment_id="RUN-deployment",vm_role="vm_a",sampled_at=datetime.now(timezone.utc),monotonic_seconds=3,interval_seconds=1)["cpu_total_percent"]["value"] is not None
    missing=guest.parse_proc_stat("cpu0 1 2 3 4 5 6 7 8\ncpu1 2 3 4 5 6 7 8 9\n")
    assert "cpu" not in missing and len([key for key in missing if key.startswith("cpu")]) == 2
