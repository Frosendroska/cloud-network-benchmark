"""Composed, credential-free qualification of the reduced operator path.

Every authority and provider receipt written here is explicitly synthetic and
exists only below pytest's temporary directory. It cannot authorize live use.
"""
from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import copy
import io
import os
import threading
import time
import traceback
import itertools
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace


class _ThreadRoutedTextIO:
    """Route actual CLI output to the invoking test Popen adapter thread."""

    def __init__(self, fallback):
        self.fallback = fallback
        self.local = threading.local()

    def write(self, value):
        target = getattr(self.local, "target", self.fallback)
        return target.write(value)

    def flush(self):
        target = getattr(self.local, "target", self.fallback)
        return target.flush()

    def __getattr__(self, name):
        return getattr(self.fallback, name)


def _install_cli_output_routers(monkeypatch):
    import sys
    stdout = _ThreadRoutedTextIO(sys.stdout)
    stderr = _ThreadRoutedTextIO(sys.stderr)
    monkeypatch.setattr(sys, "stdout", stdout)
    monkeypatch.setattr(sys, "stderr", stderr)
    return stdout, stderr


class _ActualCliPopenBoundary:
    """Adapt known Python process starts to concurrent actual CLI threads."""

    def __init__(self, stdout_router, stderr_router, *, run_clock, original_popen):
        self.stdout_router = stdout_router
        self.stderr_router = stderr_router
        self.run_clock = run_clock
        self.original_popen = original_popen
        self.processes = []
        self.lock = threading.Lock()
        self._pids = itertools.count(2_000_000_000)

    def __call__(self, command, *, cwd=None, stdout=None, stderr=None, **kwargs):
        command = list(command)
        try:
            module_index = command.index("-m")
            module = command[module_index + 1]
            arguments = command[module_index + 2:]
        except (ValueError, IndexError):
            executable = Path(command[0]).name if command else ""
            if executable in {"aws", "az", "gcloud", "terraform", "ssh", "scp"}:
                raise AssertionError(f"unexpected provider command escaped CommandRunner: {command!r}")
            return self.original_popen(command, cwd=cwd, stdout=stdout, stderr=stderr, **kwargs)
        if module not in {"cloud_network_benchmark", "cloud_network_benchmark.campaign_supervisor"}:
            raise AssertionError(f"unexpected Python subprocess module: {module!r}")
        if cwd is not None and Path(cwd).resolve() != Path.cwd().resolve():
            raise AssertionError(f"CLI cwd differs from the isolated candidate cwd: {cwd}")
        if stdout is None or not hasattr(stdout, "fileno"):
            raise AssertionError("known CLI subprocess requires the campaign's actual output log")
        output_fd = os.dup(stdout.fileno())
        output = os.fdopen(output_fd, "ab", buffering=0)
        process = SimpleNamespace(pid=next(self._pids), returncode=None, thread=None)

        def invoke():
            captured = io.StringIO()
            out_previous = getattr(self.stdout_router.local, "target", None)
            err_previous = getattr(self.stderr_router.local, "target", None)
            self.stdout_router.local.target = captured
            self.stderr_router.local.target = captured
            try:
                if module == "cloud_network_benchmark":
                    from cloud_network_benchmark.lifecycle_cli import main as lifecycle_main
                    if arguments[:1] != ["lifecycle"]:
                        raise AssertionError(f"unexpected top-level CLI subcommand: {arguments!r}")
                    process.returncode = lifecycle_main(arguments[1:], run_clock=self.run_clock)
                else:
                    from cloud_network_benchmark.campaign_supervisor import main as supervisor_main
                    process.returncode = supervisor_main(arguments)
            except BaseException:
                captured.write(traceback.format_exc())
                process.returncode = 1
            finally:
                process.output = captured.getvalue()
                try:
                    output.write(process.output.encode("utf-8"))
                    output.flush()
                finally:
                    output.close()
                    if out_previous is None:
                        del self.stdout_router.local.target
                    else:
                        self.stdout_router.local.target = out_previous
                    if err_previous is None:
                        del self.stderr_router.local.target
                    else:
                        self.stderr_router.local.target = err_previous

        process.thread = threading.Thread(target=invoke,
            name=f"offline-cli-{module.rsplit('.', 1)[-1]}", daemon=True)
        process.poll = lambda: process.returncode if not process.thread.is_alive() else None
        process.wait = lambda timeout=None: _join_process(process, timeout)
        with self.lock:
            self.processes.append(process)
        process.thread.start()
        return process

    def join_all(self, timeout: float = 150.0) -> None:
        for process in self.processes:
            process.thread.join(timeout)
            if process.thread.is_alive():
                raise TimeoutError("offline CLI adapter child did not stop within its bounded join")


def _join_process(process, timeout):
    process.thread.join(timeout)
    if process.thread.is_alive():
        raise TimeoutError("offline CLI adapter child did not stop within its bounded join")
    return process.returncode


class _OriginalPlanReviewBoundary:
    """Disclose each actual saved plan and publish a distinct offline receipt."""

    def __init__(self, *, private_root, checkpoint_root, stdout_router):
        self.private_root = Path(private_root).resolve()
        self.checkpoint_root = Path(checkpoint_root).resolve()
        self.stdout_router = stdout_router
        self.stop_event = threading.Event()
        self.lock = threading.Lock()
        self.processed = set()
        self.disclosures = []
        self.errors = []
        self.thread = threading.Thread(target=self._watch,
            name="offline-original-plan-review", daemon=True)

    def start(self):
        self.checkpoint_root.mkdir(mode=0o700, parents=True, exist_ok=False)
        self.thread.start()

    def _stable_request(self, request_path):
        """Mirror the production watcher's two-identical-read publication check."""
        required = {"action_id", "root", "state", "state_sha256", "plan_sha256",
            "json_sha256", "variables_sha256", "source_sha256", "scope"}
        first_seen = time.time()
        hard_stop = first_seen + 120.0
        last_identity = None
        last_binding = None
        while not self.stop_event.is_set():
            if request_path.is_symlink() or not request_path.is_file():
                raise ValueError("original review request is not a regular file")
            try:
                stat = request_path.stat()
                payload = request_path.read_bytes()
                binding = json.loads(payload)
            except (OSError, json.JSONDecodeError, UnicodeDecodeError):
                binding = None
                stat = None
                payload = b""
            if isinstance(binding, dict) and required.issubset(binding):
                deadline = binding.get("review_deadline_utc_epoch")
                if isinstance(deadline, (int, float)) and not isinstance(deadline, bool):
                    from cloud_network_benchmark.terraform_plan import original_review_cutoff
                    cutoff = min(original_review_cutoff(request_path.parent, binding,
                        float(deadline)), hard_stop)
                    identity = (stat.st_mtime_ns, hashlib.sha256(payload).hexdigest())
                    if identity == last_identity:
                        return payload
                    last_identity, last_binding = identity, binding
                else:
                    cutoff = hard_stop
            else:
                cutoff = hard_stop
            if time.time() >= cutoff:
                if last_binding is not None:
                    return None
                raise TimeoutError("original review request did not become stable before its fixed cutoff")
            self.stop_event.wait(0.025)
        return None

    def _watch(self):
        while not self.stop_event.is_set():
            try:
                pending = list(self.private_root.rglob("review-request.json"))
                for request_path in pending:
                    resolved = request_path.resolve(strict=False)
                    with self.lock:
                        if resolved in self.processed:
                            continue
                    try:
                        if self._stable_request(request_path) is None:
                            with self.lock:
                                self.processed.add(resolved)
                            continue
                        with self.lock:
                            self.processed.add(resolved)
                        self._disclose_and_receipt(request_path)
                    except BaseException as exc:
                        with self.lock:
                            self.processed.add(resolved)
                            self.errors.append((str(request_path), exc))
            except BaseException as exc:
                with self.lock:
                    self.errors.append((str(self.private_root), exc))
            self.stop_event.wait(0.02)

    def _disclose_and_receipt(self, request_path):
        from cloud_network_benchmark import plan_disclosure, terraform_plan

        request_path = Path(request_path)
        if not request_path.resolve().is_relative_to(self.private_root):
            raise ValueError("review request is outside the actual stage-owned private roots")
        binding_bytes = request_path.read_bytes()
        binding = json.loads(binding_bytes)
        source_root = Path(binding["root"]).resolve(strict=True)
        if request_path.is_symlink() or not request_path.is_file():
            raise ValueError("synthetic reviewer accepts only an original regular request")
        deadline = binding.get("review_deadline_utc_epoch")
        if (isinstance(deadline, bool) or not isinstance(deadline, (int, float))
                or deadline <= 0):
            raise ValueError("original request has no finite source-bound review cutoff")
        original_cutoff = terraform_plan.original_review_cutoff(
            request_path.parent, binding, deadline)
        if original_cutoff > deadline:
            raise ValueError("computed original cutoff exceeds the producer's finite review deadline")
        label_material = binding_bytes + b"\0" + str(request_path.resolve()).encode("utf-8")
        label = hashlib.sha256(label_material).hexdigest()[:20]
        checkpoint = self.checkpoint_root / label
        output = io.StringIO()
        previous = getattr(self.stdout_router.local, "target", None)
        self.stdout_router.local.target = output
        try:
            published = plan_disclosure.disclose(
                roots=[request_path.parent.parent, source_root],
                checkpoint_root=checkpoint,
                deadline_epoch=original_cutoff,
                display_limit=32768,
                request_path=request_path,
            )
        finally:
            if previous is None:
                del self.stdout_router.local.target
            else:
                self.stdout_router.local.target = previous
        if len(published) != 1 or published[0].parent != checkpoint:
            raise ValueError("actual disclosure did not publish exactly one request checkpoint")
        manifest = json.loads((checkpoint / "manifest.json").read_text(encoding="utf-8"))
        if (manifest.get("format") != "cnb-plan-disclosure-v1"
                or len(manifest.get("entries", [])) != 1):
            raise ValueError("actual disclosure manifest is incomplete")
        entry = manifest["entries"][0]
        for relative, expected_hash in entry["file_sha256"].items():
            observed = (checkpoint / entry["entry_id"] / relative).read_bytes()
            if hashlib.sha256(observed).hexdigest() != expected_hash:
                raise ValueError(f"disclosed original plan artifact hash changed: {relative}")
        complete_path = checkpoint / entry["entry_id"] / "plan.complete.json"
        sections_path = checkpoint / entry["entry_id"] / "sections.json"
        if (not complete_path.is_file() or not sections_path.is_file()
                or json.loads(complete_path.read_bytes()) != json.loads((request_path.parent / "saved-plan.json").read_bytes())):
            raise ValueError("disclosure pages do not preserve the complete original saved JSON plan")
        page_text = output.getvalue()
        if not page_text or "human_review_required=true" not in page_text:
            raise ValueError("disclosure did not produce the full review pages and guarded receipt instruction")
        pages_path = checkpoint / entry["entry_id"] / "review-pages.txt"
        with pages_path.open("xb") as stream:
            os.chmod(pages_path, 0o600)
            stream.write(page_text.encode("utf-8"))
        request_hash = hashlib.sha256(binding_bytes).hexdigest()
        terraform_plan.record_operator_review(
            request_path.parent,
            request_hash,
            reviewer="SYNTHETIC OFFLINE REVIEWER (NOT A HUMAN APPROVAL)",
            inspection=("Offline fixture inspected the complete lossless disclosure pages, "
                "saved binary, JSON, variables, state and source bindings."),
            deadline_utc_epoch=original_cutoff,
        )
        with self.lock:
            self.disclosures.append({"request": str(request_path), "checkpoint": str(checkpoint),
                "request_sha256": request_hash, "pages_sha256": hashlib.sha256(
                    pages_path.read_bytes()).hexdigest(), "pages_bytes": pages_path.stat().st_size,
                "reviewer": "SYNTHETIC OFFLINE REVIEWER (NOT A HUMAN APPROVAL)"})

    def stop_and_join(self, timeout=30.0):
        self.stop_event.set()
        self.thread.join(timeout)
        if self.thread.is_alive():
            raise TimeoutError("plan reviewer did not stop within its bounded join")
        if self.errors:
            path, error = self.errors[0]
            raise AssertionError(f"actual disclosure/receipt failed for {path}: {type(error).__name__}: {error}")
        pending = [path for path in self.private_root.rglob("review-request.json")
            if not (path.parent / "accepted-review.json").is_file()]
        if pending:
            raise AssertionError(f"synthetic reviewer left original requests pending: {pending}")


def _synthetic_identity_command(argv, **kwargs):
    """Reply only to the exact read-only identity commands used by production."""
    command = tuple(argv)
    if command == ("aws", "sts", "get-caller-identity", "--output", "json", "--no-cli-pager"):
        return SimpleNamespace(returncode=0, stdout=json.dumps({"Account": "123456789012"}), stderr="")
    if command == ("az", "account", "show", "--output", "json", "--only-show-errors"):
        return SimpleNamespace(returncode=0, stdout=json.dumps({
            "id": "11111111-1111-1111-1111-111111111111",
            "tenantId": "22222222-2222-2222-2222-222222222222", "state": "Enabled"}), stderr="")
    if command == ("gcloud", "auth", "list", "--filter=status:ACTIVE", "--format=value(account)"):
        return SimpleNamespace(returncode=0, stdout="synthetic@example.invalid\n", stderr="")
    if command == ("gcloud", "config", "get-value", "project", "--quiet"):
        return SimpleNamespace(returncode=0, stdout="offline-project\n", stderr="")
    if command == ("gcloud", "auth", "application-default", "print-access-token"):
        assert kwargs.get("stdout") is subprocess.DEVNULL
        assert kwargs.get("stderr") is subprocess.DEVNULL
        return SimpleNamespace(returncode=0, stdout="", stderr="")
    raise AssertionError(f"unexpected identity command: {command!r}")


def test_reduced_operator_chain_generates_and_activates_typed_draft(
        repository_root, tmp_path, monkeypatch, request):
    from cloud_network_benchmark import campaign_inputs
    from cloud_network_benchmark.campaign import CampaignDriver
    from cloud_network_benchmark.campaign_draft import create_draft_bundle, load_and_verify_draft
    from cloud_network_benchmark.campaign_inputs import activate_prepared_stage, prepare_stage
    from cloud_network_benchmark.campaign_identity import check_provider_identities
    from cloud_network_benchmark.config import resolve_campaign
    from cloud_network_benchmark.financial_controls import financial_binding
    from cloud_network_benchmark.lifecycle_runtime import (
        build_admission_request, load_initialized_campaign, load_runtime_config)
    from cloud_network_benchmark.admission import load_cost_inputs
    from cloud_network_benchmark.validation_profiles import ACCEPTANCE_PROFILE, FAILURE_PROFILE
    from tests.integration.test_campaign_admission import Clock, operator_settings
    from tests.financial_helpers import synthetic_finances

    production_common_n = campaign_inputs._common_n
    production_binary_lookup = shutil.which
    settings = operator_settings(tmp_path, repository_root, monkeypatch)
    # Northern-region image selection is source-bound in this reduced profile;
    # use an explicitly synthetic canonical-shaped value at the input layer.
    settings["providers"]["aws"]["ami_ids"]["eu-north-1"] = "ami-0123456789abcdef0"
    # Capsules bind their manifests and result roots beneath the selected
    # repository root. Keep that complete tree in this private candidate and
    # scope cleanup to this unique test-owned subtree.
    isolated_results = Path(repository_root) / "results"
    if isolated_results.is_symlink() or not isolated_results.is_dir():
        raise AssertionError("isolated candidate results root must be an existing real repository directory")
    settings["results_root"] = str(isolated_results)
    from tests.support.reduced_archive_protocol import synthetic_archive_sink_settings
    settings["archive_sink"] = synthetic_archive_sink_settings(tmp_path / "synthetic-archive-inputs")
    settings["archive_sink"]["restore_path"] = str(tmp_path / "synthetic-archive-restore")
    Path(settings["archive_sink"]["restore_path"]).mkdir(mode=0o700)
    # Keep the synthetic quoted result-transfer envelope large enough for the
    # actual closed native archives while retaining the production byte debit.
    for category in settings["pricing"]["result_transfer"].values():
        for transfer in category.values():
            transfer["payload_limit_bytes"] = 16 * 1024 * 1024
            transfer["protocol_overhead_allowance_bytes"] = 1024 * 1024
    settings["providers"]["azure"]["subscription_id"] = "188a5848-76fb-4d57-8f2a-32c09284d486"
    # Azure's real runtime DTO parses SSH key material. Use a structurally
    # valid synthetic Ed25519 key for the local factory fixture.
    Path(settings["providers"]["azure"]["ssh_public_key_path"]).write_text(
        "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIE9F4huSqss0wx3n6f5+WJiEpePM3oXTbuOzBLRR5NCc offline-test-only\n")
    # Retain the real production common-N and identity validators. Only their
    # external identity command boundary and executable-presence lookup receive
    # deterministic local responses.
    monkeypatch.setattr(campaign_inputs, "_common_n", production_common_n)
    monkeypatch.setattr(campaign_inputs, "check_provider_identities",
        lambda providers: check_provider_identities(providers, run=_synthetic_identity_command))
    monkeypatch.setattr(campaign_inputs.shutil, "which",
        lambda name: f"/synthetic-offline-bin/{name}" if name in
            {"terraform", "aws", "az", "gcloud", "ssh", "scp"} else production_binary_lookup(name))
    for key in ("paid_acceptance", "calibration_authorized", "common_n",
                "diagnostic_day_authorized", "study_window"):
        settings["approvals"].pop(key, None)

    now = datetime.now(timezone.utc).replace(microsecond=0)
    lower = (now - timedelta(minutes=1)).isoformat()
    window = {"not_before_utc": lower,
        "not_after_utc": (now + timedelta(hours=5)).isoformat()}
    decision = tmp_path / "SYNTHETIC-WAIVER-DECISION.md"
    decision.write_text(
        "## 2026-11-23 Waive deliberate pre-DAY fault, expiry and recovery experiments\n"
        "SYNTHETIC OFFLINE TEST ONLY; V-A/Z/G-FAIL remain WAIVED_NOT_TESTED.\n")
    settings["stage_inputs"]["acceptance"].update(
        workflow_profile=ACCEPTANCE_PROFILE,
        failure_workflow_profile=FAILURE_PROFILE,
        execution_window_utc=window,
        dispatch_window_utc=dict(window),
        azure_auxiliary_cleanup=True,
        failure_waiver={"confirmed": True, "source": str(decision),
            "sha256": hashlib.sha256(decision.read_bytes()).hexdigest()})
    settings["stage_inputs"]["acceptance"].pop("allowance", None)
    from cloud_network_benchmark.validation_profiles import CALIBRATION_PROFILE
    settings["stage_inputs"]["calibration"].update(workflow_profile=CALIBRATION_PROFILE,
        execution_window_utc=window, dispatch_window_utc=dict(window), azure_auxiliary_cleanup=False)
    settings["stage_inputs"]["calibration"].pop("allowance", None)
    settings["approvals"]["calibration_authorized"] = {"confirmed": True,
        "source": str(decision), "fields": {"execution_window_utc": window,
            "dispatch_window_utc": dict(window)}}
    settings["approvals"]["paired_acceptance"]["fields"].update(
        execution_window_utc=window, dispatch_window_utc=dict(window))
    settings["pricing"]["workflow_profiles"] = [ACCEPTANCE_PROFILE, CALIBRATION_PROFILE]
    reserve_request = {"status": "pending", "currency": "EUR",
        "prior_release_eur": "29", "additional_release_eur": "8",
        "cumulative_release_eur": "37"}
    settings["pricing"]["pending_eur_reserve_release"] = reserve_request
    settings["pricing"]["funding"].update(primary="230", replacements="0", tax="20",
        uncertainty="25", total_limit="300", working_limit="250", protected_buffer="50")
    declared_funding = sum(Decimal(settings["pricing"]["funding"][key])
        for key in ("primary", "replacements", "tax", "uncertainty"))
    assert Decimal("250") < declared_funding <= Decimal("300")

    stage = prepare_stage({"stage": "acceptance"}, settings, repository_root,
        Path(settings["state_root"]), Path(settings["results_root"]), Clock(), draft=True)
    stage_results = Path(stage["results_root"])
    if stage_results.parent != isolated_results or stage_results.exists() is False:
        raise AssertionError("generated acceptance results must occupy one unique results/<stage-id> directory")
    request.addfinalizer(lambda: shutil.rmtree(stage_results, ignore_errors=True))
    generated = Path(stage["entries"][0]["config_path"]).parent
    request.addfinalizer(lambda: shutil.rmtree(generated, ignore_errors=True))
    assert stage["status"] == "DRAFT_UNAPPROVED"
    assert [entry["entry_id"] for entry in stage["entries"]] == ["V-S1", "V-S3"]
    assert stage["failure_waiver"]["not_tested"] == ["V-A-FAIL", "V-Z-FAIL", "V-G-FAIL"]

    # Resolve the actual six parent/provider bindings through the same runtime,
    # manifest, config and cost loaders used by financial preflight.
    bindings = []
    for entry in stage["entries"]:
        assert set(entry["run_ids"]) == {"aws", "azure", "gcp"}
        assert len(set(entry["run_ids"].values())) == 3
        runtime = load_runtime_config(Path(entry["runtime_config_path"]),
            selected_providers=set(entry["run_ids"]), allow_missing_approval_evidence=True)
        parent, children = load_initialized_campaign(Path(entry["parent_manifest_path"]),
            repository_root=repository_root, config_path=Path(entry["config_path"]))
        campaign, _ = resolve_campaign(Path(entry["config_path"]), repository_root)
        costs = load_cost_inputs(Path(entry["cost_inputs_path"]))
        admission, plan = build_admission_request(campaign=campaign, parent=parent,
            children=children, costs=costs, runtime=runtime,
            controller_session_id=stage["controller_session_id"])
        bindings.append((entry, financial_binding(plan, admission)))
        assert set(children) == {"aws", "azure", "gcp"}
        assert Path(entry["private_root"]).is_dir()

    assert len({row["campaign_id"] for _, row in bindings}) == 2
    assert sum(len(row["run_ids"]) for _, row in bindings) == 6
    assert len({str(Path(entry["private_root"])) for entry, _ in bindings}) == 2

    # Bind a typed, explicitly non-authorizing FinancialAuthorization fixture
    # to those real identities. Its checked price/FX/liability inputs are local
    # synthetic files; they do not represent a current quote or ledger state.
    auth_root = tmp_path / "synthetic-authority"
    auth_root.mkdir()
    _ledger, _plans, card, card_path = synthetic_finances(auth_root, count=2, paid=True)
    valid_until = now + timedelta(days=30)
    card.update(batch_id="reduced-acceptance-offline-v1", stage_id=stage["stage_id"],
        ledger_scope_id=stage["scope_id"], approved_by="SYNTHETIC-OFFLINE-TEST",
        approved_at=now.isoformat(), valid_from=(now - timedelta(minutes=1)).isoformat(),
        valid_until=valid_until.isoformat(), approved_run_count=6,
        entries=[value for _entry, value in bindings])
    card["caps"].update(project_eur="300", working_eur="287", protected_eur="13",
        personal_cash_eur="287", run_eur="8", batch_eur="16", stage_eur="16",
        providers_eur={provider: "8" for provider in ("aws", "azure", "gcp")})
    cost_file = Path(stage["cost_input_path"])
    from cloud_network_benchmark.admission import load_cost_inputs
    costs_hash = load_cost_inputs(cost_file).payload_sha256
    card["price"].update(path=str(cost_file), sha256=costs_hash,
        checked_at=now.isoformat(), valid_until=valid_until.isoformat())
    for key in ("fx", "external_liabilities"):
        reference = card[key]
        source_path = Path(reference["path"])
        source = json.loads(source_path.read_text())
        source.update(checked_at=now.isoformat(), valid_until=valid_until.isoformat())
        if key == "external_liabilities":
            source.update(ledger_scope_id=stage["scope_id"], currency="EUR",
                gross_eur="0", personal_cash_eur="0", excludes_this_ledger=True)
        source_path.write_text(json.dumps(source, sort_keys=True) + "\n")
        reference.update(sha256=hashlib.sha256(source_path.read_bytes()).hexdigest(),
            checked_at=now.isoformat(), valid_until=valid_until.isoformat())
    card_path.write_text(json.dumps(card, sort_keys=True) + "\n")
    prior_card = copy.deepcopy(card)
    prior_card["caps"].update(project_eur="300", working_eur="279", protected_eur="21",
        personal_cash_eur="279", batch_eur="279", stage_eur="279")
    prior_card["approved_at"] = (now - timedelta(days=10)).isoformat()
    prior_card["valid_from"] = (now - timedelta(days=10)).isoformat()
    prior_card["valid_until"] = (now - timedelta(days=1)).isoformat()
    prior_card_path = auth_root / "SYNTHETIC-pinned-prior-EUR29.json"
    prior_card_path.write_text(json.dumps(prior_card, sort_keys=True) + "\n")
    prior_hash = hashlib.sha256(prior_card_path.read_bytes()).hexdigest()
    # The exact pinned historical card is represented by an explicit test-only
    # typed fixture. Production still requires its compiled SHA-256 pin.
    monkeypatch.setattr(campaign_inputs, "_EUR29_AUTHORITY_SHA256", prior_hash)
    settings["approvals"]["prior_paid_authorization"] = {"confirmed": True,
        "source": str(prior_card_path), "sha256": prior_hash}
    settings["approvals"]["paid_authorization"] = {"confirmed": True,
        "source": str(card_path), "sha256": hashlib.sha256(card_path.read_bytes()).hexdigest()}

    # Preserve the canonical $300/$250/$50 raw USD labels while using the
    # source-bound synthetic EUR 37 cumulative card only for offline admission.
    # Launch readiness and live financial authority remain out of scope.
    bundle_path = Path(settings["state_root"]) / "operator-chain-draft.json"
    create_draft_bundle(plan={"batch_id": "reduced-acceptance-offline-v1"},
        stages=[stage], output_path=bundle_path,
        missing_prerequisites={stage["stage_id"]: ["paid_authorization"]},
        preparer=prepare_stage)
    draft = load_and_verify_draft(bundle_path)
    assert draft["stages"][0]["stage_id"] == stage["stage_id"]
    assert draft["stages"][0]["failure_waiver"]["not_tested"] == [
        "V-A-FAIL", "V-Z-FAIL", "V-G-FAIL"]
    cost_payload = json.loads(Path(stage["cost_input_path"]).read_text())
    assert cost_payload["normalization"]["reserve_release"] == reserve_request

    # The generated Azure cleanup cohort is a source-bound auxiliary input to
    # actual frozen activation. Its synthetic card authorizes only this offline
    # fixture and is not human or provider authority.
    aux_scope_ref = stage["azure_auxiliary_cleanup_scope"]
    aux_scope_path = Path(aux_scope_ref["path"])
    aux_scope = json.loads(aux_scope_path.read_text())
    aux_decision = tmp_path / "SYNTHETIC-AZURE-CLEANUP-DECISION.md"
    aux_decision.write_text("SYNTHETIC OFFLINE ONLY; no Azure cleanup authority.\n")
    aux_card_path = Path(stage["approval_refs"]["azure_shared_cleanup"])
    synthetic_now = datetime.now(timezone.utc).replace(microsecond=0)
    aux_card = {"schema_version": 1, "approval_status": "approved",
        "action": "delete_exact_auto_created_network_watcher_and_empty_parent",
        "profile": aux_scope["profile"], "cohort_id": aux_scope["cohort_id"],
        "subscription_id": aux_scope["subscription_id"], "region": aux_scope["region"],
        "resource_group_id": aux_scope["resource_group_id"], "watcher_id": aux_scope["watcher_id"],
        "cohort_manifest_path": str(aux_scope_path), "cohort_manifest_sha256": aux_scope_ref["sha256"],
        "owners": aux_scope["owners"],
        "expected_watcher_create_caller": "00000000-0000-0000-0000-000000000001",
        "expected_owned_write_caller": "00000000-0000-0000-0000-000000000002",
        "maximum_leaf_deletes": 1, "maximum_group_deletes": 1,
        "source_path": str(aux_scope_path), "source_sha256": aux_scope_ref["sha256"],
        "decision_source_path": str(aux_decision),
        "decision_source_sha256": hashlib.sha256(aux_decision.read_bytes()).hexdigest(),
        "approved_by": "SYNTHETIC OFFLINE ONLY", "approved_at_utc": synthetic_now.isoformat(),
        "not_before_utc": (synthetic_now - timedelta(minutes=1)).isoformat(),
        "not_after_utc": (synthetic_now + timedelta(days=1)).isoformat()}
    aux_card_path.parent.mkdir(parents=True, exist_ok=True)
    aux_card_path.write_text(json.dumps(aux_card, sort_keys=True) + "\n")

    # Enter the public frozen activation, actual supervisor/CLI code and the
    # reviewed per-provider command and Terraform-plan boundaries.
    import yaml
    from cloud_network_benchmark import lifecycle_adapters, lifecycle_cli
    from cloud_network_benchmark.admission import BudgetLedger
    from cloud_network_benchmark import gcs_archive
    from cloud_network_benchmark.plan_disclosure import _provider_mirror_snapshot
    from tests.support.reduced_command_router import ReducedCommandRouter
    from tests.support.reduced_plan_protocol import ReducedPlanProtocol
    from tests.unit.test_gcs_archive import FakeGCS
    from tests.integration import test_f09_multi_provider_factory as lifecycle_fixture
    from tests.integration import test_f09_aws_runtime_factory as aws_fixture

    archive_publish_errors = []
    original_publish_guest_archive_parts = gcs_archive.publish_guest_archive_parts

    def observe_archive_publish(*args, **kwargs):
        try:
            return original_publish_guest_archive_parts(*args, **kwargs)
        except BaseException as exc:
            archive_publish_errors.append({"error": repr(exc),
                "cause_chain": [{"type": type(item).__name__, "error": str(item)}
                    for item in _exception_chain(exc)],
                "traceback": "".join(traceback.format_exception(exc))})
            raise

    def _exception_chain(error):
        seen = set()
        while error is not None and id(error) not in seen:
            seen.add(id(error))
            yield error
            error = error.__cause__ or error.__context__

    monkeypatch.setattr(gcs_archive, "publish_guest_archive_parts", observe_archive_publish)

    # The two provider fixtures share one bounded high-water clock based on
    # the current instant. Provider/module-local clock adapters stay local.
    bounded_now = datetime.now(timezone.utc).replace(microsecond=0)
    monkeypatch.setattr(lifecycle_fixture, "NOW", bounded_now)
    monkeypatch.setattr(aws_fixture, "NOW", bounded_now)
    lifecycle_clock = lifecycle_fixture.Clock()
    real_monotonic = time.monotonic
    lifecycle_clock.utc_now = lambda: datetime.now(timezone.utc) + timedelta(seconds=lifecycle_clock._offset())
    lifecycle_clock.monotonic = lambda: real_monotonic() + lifecycle_clock._offset()
    original_clock_sleep = lifecycle_clock.sleep

    def bounded_clock_sleep(seconds):
        original_clock_sleep(seconds)
        # Give concurrent provider workers a scheduling opportunity while
        # preserving the fixture clock's original finite time advancement.
        time.sleep(min(max(float(seconds), 0.0), 0.005))

    lifecycle_clock.sleep = bounded_clock_sleep
    lifecycle_fixture._bridge_gcp_monotonic(monkeypatch, lifecycle_clock)

    # Observe the real stage-fit decision so a fixture timing failure includes
    # its exact inputs and reasons. This wrapper returns the original decision.
    fit_trace = []
    original_check_stage_fit = BudgetLedger.check_stage_fit

    def observe_stage_fit(ledger, reservation_id, *, stage, remaining):
        decision = original_check_stage_fit(ledger, reservation_id,
            stage=stage, remaining=remaining)
        fit_trace.append({"reservation_id": reservation_id, "stage": stage,
            "monotonic_now": ledger.monotonic_now(), "utc_now": ledger.utc_now().isoformat(),
            "remaining": dict(vars(remaining)), "eligible": decision.eligible,
            "reasons": list(decision.reason_codes),
            "effective_deadline": (None if decision.effective_deadline is None
                else decision.effective_deadline.isoformat())})
        return decision

    monkeypatch.setattr(BudgetLedger, "check_stage_fit", observe_stage_fit)

    transfer_debit_trace = []
    original_debit_result_transfer = BudgetLedger.debit_result_transfer

    def observe_transfer_debit(ledger, reservation_id, *, provider, attempt_id, maximum_payload_bytes):
        allowed = original_debit_result_transfer(ledger, reservation_id,
            provider=provider, attempt_id=attempt_id,
            maximum_payload_bytes=maximum_payload_bytes)
        if not allowed:
            state = ledger.inspect()
            record = state.get("reservations", {}).get(reservation_id, {})
            transfer = record.get("result_transfer", {}).get(str(getattr(provider, "value", provider)), {})
            from cloud_network_benchmark.financial_controls import (
                FinancialError, revalidate_financial_record)
            try:
                revalidate_financial_record(record, state=state, now=ledger.utc_now(),
                    end=datetime.fromisoformat(record["evidence"]["expires_at"]["value"]),
                    existing_retrieval=True)
                financial_recheck = {"status": "accepted"}
            except FinancialError as exc:
                financial_recheck = {"status": "rejected", "reason": exc.reason}
            transfer_debit_trace.append({"reservation_id": reservation_id,
                "provider": str(getattr(provider, "value", provider)),
                "attempt_id": attempt_id, "maximum_payload_bytes": maximum_payload_bytes,
                "execution_scope": record.get("execution_scope"),
                "session_matches": record.get("session") == ledger.controller_session_id,
                "lifecycle_terminal": record.get("lifecycle_terminal"),
                "terminal_released": record.get("terminal_released"),
                "financial_terminal_state": record.get("financial_terminal_state"),
                "provider_cleanup": record.get("provider_cleanup", {}).get(str(getattr(provider, "value", provider))),
                "budget_blocked": state.get("budget_blocked"),
                "cleanup_blocked": state.get("cleanup_blocked"),
                "financial_recheck": financial_recheck,
                "debited_bytes": transfer.get("debited_bytes"),
                "payload_limit_bytes": transfer.get("payload_limit_bytes"),
                "existing_attempt_bytes": transfer.get("attempts", {}).get(attempt_id)})
        return allowed

    monkeypatch.setattr(BudgetLedger, "debit_result_transfer", observe_transfer_debit)

    mirror_config = Path("/u/halle/brek/home_at/cnb/tools/provider-mirror-AwkH5Pia/terraform.rc")
    mirror_sha256 = "466d62b03f88f59cad0304a54e15ee420e85d62c15d33082ad25dbe6377e13f5"
    assert mirror_config.is_file() and not mirror_config.is_symlink()
    assert hashlib.sha256(mirror_config.read_bytes()).hexdigest() == mirror_sha256
    monkeypatch.setenv("TF_CLI_CONFIG_FILE", str(mirror_config))
    assert os.environ.get("TF_CLI_CONFIG_FILE") == str(mirror_config)
    mirror_snapshot = _provider_mirror_snapshot()
    assert mirror_snapshot is not None and mirror_snapshot[0] == str(mirror_config)
    assert mirror_snapshot[1] == mirror_sha256

    router = ReducedCommandRouter(remote_root=tmp_path / "native-guest-fixtures",
        repository_root=repository_root, clock=lifecycle_clock)
    router.install_hook_observers(monkeypatch, lifecycle_adapters)
    router.install_plan_protocol(ReducedPlanProtocol(repository_root))
    router.install_command_boundary(monkeypatch)
    fake_gcs = FakeGCS()
    original_runtime_loader = lifecycle_cli.load_runtime_config

    def runtime_loader(*args, **kwargs):
        runtime = original_runtime_loader(*args, **kwargs)
        # ``sleep`` is the documented excluded timing seam in the runtime
        # digest. Bind only the same bounded high-water clock used by the
        # actual lifecycle; leave every serialized DTO field untouched.
        runtime["sleep"] = lifecycle_clock.sleep
        return runtime

    monkeypatch.setattr(lifecycle_cli, "load_runtime_config", runtime_loader)
    import cloud_network_benchmark.gcs_archive as gcs_archive
    original_gcs_factory = gcs_archive.google_cloud_storage_backend

    def offline_gcs_factory(source_settings):
        if (source_settings.get("bucket") != settings["archive_sink"]["bucket"]
                or source_settings.get("controller_quota_evidence_sha256")
                    != settings["archive_sink"]["controller_quota_evidence_sha256"]):
            raise AssertionError("actual archive backend factory received changed source-bound settings")
        return fake_gcs

    monkeypatch.setattr(gcs_archive, "google_cloud_storage_backend", offline_gcs_factory)

    settings_path = tmp_path / "operator-settings.yaml"
    settings_path.write_text(yaml.safe_dump(settings, sort_keys=False), encoding="utf-8")
    routers = _install_cli_output_routers(monkeypatch)
    cli_boundary = _ActualCliPopenBoundary(*routers, run_clock=lifecycle_clock,
        original_popen=subprocess.Popen)
    monkeypatch.setattr(subprocess, "Popen", cli_boundary)
    actual_private_roots = Path(stage["state_root"]) / stage["stage_id"]
    reviewer = _OriginalPlanReviewBoundary(private_root=actual_private_roots,
        checkpoint_root=tmp_path / "original-plan-disclosures", stdout_router=routers[0])
    reviewer.start()
    activation_error = None
    result = None
    try:
        driver = CampaignDriver(clock=lifecycle_clock, sleep=lifecycle_clock.sleep)
        result = driver.activate_frozen_draft(bundle_path, stage_id=stage["stage_id"],
            settings_path=settings_path, execute=True, root=repository_root)
    except BaseException as exc:
        activation_error = exc
    join_error = None
    try:
        cli_boundary.join_all(timeout=150.0)
    except BaseException as exc:
        join_error = exc
    reviewer_join_error = None
    try:
        reviewer.stop_and_join(timeout=150.0)
    except BaseException as exc:
        reviewer_join_error = exc
    if reviewer.errors:
        failures = [{"request": path, "error": repr(exc),
            "traceback": "".join(traceback.format_exception(exc))}
            for path, exc in reviewer.errors]
        raise AssertionError("actual offline review watcher failed: "
            + json.dumps(failures, sort_keys=True)) from reviewer.errors[0][1]
    if reviewer_join_error is not None:
        raise reviewer_join_error
    if activation_error is not None:
        raise activation_error
    if join_error is not None:
        raise join_error
    print("ACTUAL_COMMAND_REQUESTS=" + json.dumps([row.durable_view() for row in router.requests],
        sort_keys=True, default=str))
    print("ACTUAL_COMMAND_ROUTES=" + json.dumps([{"request": row["request"].durable_view(),
        **{key: value for key, value in row.items() if key != "request"}
        } for row in router.routed_requests], sort_keys=True, default=str))
    print("ACTUAL_PROVIDER_PRIVATE_ROOTS=" + json.dumps({provider: [str(path)
        for path in roots] for provider, roots in router.runtime_roots.items()}, sort_keys=True))
    print("ACTUAL_BOUNDARY_ERRORS=" + json.dumps(router.boundary_errors, sort_keys=True))
    print("ACTUAL_BOUNDARY_OBSERVATIONS=" + json.dumps(router.boundary_observations, sort_keys=True))
    print("ACTUAL_ORIGINAL_PLAN_DISCLOSURES=" + json.dumps(reviewer.disclosures,
        sort_keys=True, default=str))
    print("ACTUAL_CLI_PROCESS_OUTPUTS=" + json.dumps([{"pid": process.pid,
        "returncode": process.returncode, "output": process.output}
        for process in cli_boundary.processes], sort_keys=True, default=str))
    print("ACTUAL_STAGE_FIT_TRACE=" + json.dumps(fit_trace, sort_keys=True, default=str))
    print("ACTUAL_TRANSFER_DEBIT_TRACE=" + json.dumps(transfer_debit_trace, sort_keys=True, default=str))
    print("ACTUAL_ARCHIVE_PUBLISH_ERRORS=" + json.dumps(archive_publish_errors, sort_keys=True))
    assert result["status"] == "dispatched"
    assert cli_boundary.processes, "public activation did not start the actual supervisor CLI"
    assert router.requests, "actual lifecycle CLI did not reach the command router"
    assert all(process.returncode is not None for process in cli_boundary.processes)
    assert Path(result["journal"]).is_file()
    journal = json.loads(Path(result["journal"]).read_text(encoding="utf-8"))
    expected_entries = {entry["entry_id"] for entry in stage["entries"]}
    observed_entries = journal.get("entries", {})
    if (set(observed_entries) != expected_entries
            or any(row.get("state") != "archive_pending_validation"
                for row in observed_entries.values())
            or any((row.get("result") or {}).get("cleanup_open") is not False
                for row in observed_entries.values())):
        raise AssertionError("actual paired lifecycle did not reach cleanup-closed archive-pending state: "
            + json.dumps(journal, sort_keys=True, default=str)
            + "\nactual boundary errors: " + json.dumps(router.boundary_errors, sort_keys=True)
            + "\nactual bounded-copy observations: "
            + json.dumps(router.boundary_observations, sort_keys=True)
            + "\nactual denied transfer debits: "
            + json.dumps(transfer_debit_trace, sort_keys=True, default=str)
            + "\nactual archive publish errors: "
            + json.dumps(archive_publish_errors, sort_keys=True))
    from cloud_network_benchmark.campaign import _last_json_object
    from tests.support.reduced_archive_protocol import replay_and_reconcile_actual_capsule

    archive_sink = settings["archive_sink"]
    replayed = {}
    for entry in stage["entries"]:
        journal_entry = observed_entries[entry["entry_id"]]
        lifecycle_log = Path(journal_entry["result"]["log"])
        lifecycle_report = _last_json_object(lifecycle_log)
        capsule = lifecycle_report.get("replay_capsule")
        if (lifecycle_report.get("status") != "archive_pending_validation"
                or not isinstance(capsule, dict) or capsule.get("status") != "archive_pending_replay"
                or set(capsule.get("providers", [])) != {"aws", "azure", "gcp"}):
            raise AssertionError(f"actual lifecycle did not persist its three-provider replay capsule: {lifecycle_report}")
        capsule_file = Path(capsule["path"])
        result_roots = [stage_results / "raw" / str(run_id)
            for run_id in entry["run_ids"].values()]
        for result_root in result_roots:
            export_source = Path(repository_root) / "results" / "analysis" / "archive-replays" / result_root.name
            assert not export_source.exists()
            request.addfinalizer(lambda target=export_source:
                shutil.rmtree(target, ignore_errors=True))
        entry_staging_root = Path(archive_sink["staging_directory"]) / entry["entry_id"]
        entry_materialization_root = Path(archive_sink["restore_path"]) / entry["entry_id"]
        entry_staging_root.mkdir(mode=0o700, parents=True, exist_ok=False)
        entry_materialization_root.mkdir(mode=0o700, parents=True, exist_ok=False)
        try:
            replayed[entry["entry_id"]] = replay_and_reconcile_actual_capsule(
                capsule_path=capsule_file, expected_capsule_sha256=capsule["sha256"],
                backend=fake_gcs, bucket=archive_sink["bucket"],
                archive_staging_root=entry_staging_root,
                materialization_root=entry_materialization_root,
                repository_root=repository_root,
                maximum_part_bytes=archive_sink["maximum_staging_bytes"],
                evaluated_at=lifecycle_clock.utc_now(), campaign_driver=driver,
                state_root=Path(stage["state_root"]), stage_id=stage["stage_id"],
                entry_id=entry["entry_id"])
        except BaseException:
            evidence = tmp_path / f"replay-failure-results-{entry['entry_id']}"
            shutil.copytree(stage_results, evidence)
            hashes = {}
            for artifact in sorted(evidence.rglob("*")):
                if artifact.is_file() and not artifact.is_symlink():
                    hashes[artifact.relative_to(evidence).as_posix()] = hashlib.sha256(artifact.read_bytes()).hexdigest()
            print("ACTUAL_REPLAY_FAILURE_RESULTS=" + json.dumps({"source": str(stage_results),
                "preserved": str(evidence), "sha256_by_file": hashes}, sort_keys=True))
            raise
    final_journal = json.loads(Path(result["journal"]).read_text(encoding="utf-8"))
    if (any(row.get("state") != "complete" for row in final_journal["entries"].values())
            or any((row.get("result") or {}).get("archive_validation") != "valid"
                for row in final_journal["entries"].values())):
        raise AssertionError("production replay/reconciliation did not close both acceptance entries: "
            + json.dumps(final_journal, sort_keys=True, default=str))

    # Build the conditional CAL admission request only from the closed,
    # replayed normal acceptance stage and its real source-bound outputs.
    acceptance_prepared_path = Path(stage["state_root"]) / stage["stage_id"] / "prepared-stage.json"
    assert acceptance_prepared_path.is_file() and not acceptance_prepared_path.is_symlink()
    acceptance_prepared_sha256 = hashlib.sha256(acceptance_prepared_path.read_bytes()).hexdigest()
    settings["approvals"]["paid_acceptance"] = {"confirmed": True,
        "source": str(acceptance_prepared_path), "sha256": acceptance_prepared_sha256}
    activated_stage = json.loads(acceptance_prepared_path.read_text(encoding="utf-8"))
    for key in ("f10_offline", "quota_review"):
        settings["approvals"][key] = {"confirmed": True,
            "source": activated_stage["approval_refs"][key], "fields": {}}
    calibration_stage = prepare_stage({"stage": "calibration"}, settings, repository_root,
        Path(settings["state_root"]), Path(settings["results_root"]), Clock(), draft=False)
    generated_calibration = Path(calibration_stage["entries"][0]["config_path"]).parent
    request.addfinalizer(lambda: shutil.rmtree(generated_calibration, ignore_errors=True))
    cal_entry = calibration_stage["entries"][0]
    cal_runtime = load_runtime_config(Path(cal_entry["runtime_config_path"]),
        selected_providers={"aws", "azure", "gcp"})
    cal_parent, cal_children = load_initialized_campaign(Path(cal_entry["parent_manifest_path"]),
        repository_root=repository_root, config_path=Path(cal_entry["config_path"]))
    cal_campaign, _ = resolve_campaign(Path(cal_entry["config_path"]), repository_root)
    cal_costs = load_cost_inputs(Path(cal_entry["cost_inputs_path"]))
    cal_admission, cal_plan = build_admission_request(campaign=cal_campaign,
        parent=cal_parent, children=cal_children, costs=cal_costs, runtime=cal_runtime,
        controller_session_id=calibration_stage["controller_session_id"])
    cal_evidence = cal_plan.prerequisites["paid_acceptance"]
    from tests.support.reduced_cal_gate import check_actual_calibration_acceptance_prerequisite
    cal_gate = check_actual_calibration_acceptance_prerequisite(evidence=cal_evidence,
        campaign=cal_campaign)
    assert cal_gate["accepted"] is True
    assert cal_gate["accepted_campaign_count"] == 2
    assert set(cal_gate["rejected_cases"]) >= {"missing", "pending_incomplete",
        "source_mismatch", "accepted_manifest_source_mismatch"}
    assert len(router.runtime_roots["aws"]) == 2
    assert len(router.runtime_roots["azure"]) == 2
    assert len(router.runtime_roots["gcp"]) == 2
    assert len({str(path) for values in router.runtime_roots.values() for path in values}) == 6
    routed_guest_roots = {(row["provider"], row["deployment_id"], root)
        for row in router.routed_requests if row["request"].action_id.startswith("f09-guest-")
        for root in row["guest_workspace_roots"]}
    expected_guest_roots = {(provider, str(leaf.deployment.deployment_id), str(path))
        for (provider, _), leaf in router._leaves.items()
        for responder in [router._remotes[(provider, leaf.deployment.run_id,
            leaf.deployment.deployment_id)]]
        for path in responder.workspace_roots.values()}
    assert routed_guest_roots == expected_guest_roots
    terraform_requests = [row for row in router.requests if "terraform" in row.argv[0]]
    assert terraform_requests
    assert all(row.environment.get("TF_CLI_CONFIG_FILE") == str(mirror_config)
        for row in terraform_requests)
    assert reviewer.disclosures
