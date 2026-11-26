"""Full-native offline transport for F09 guest archive creation and parts.

This response leaf inherits only the existing source command responder. The
production F09 retrieval callback, inventory, archive writer, staging rules,
EOF proof validation and closed archive validators remain in the call path.
"""
from __future__ import annotations

import json
import re
import shlex
import shutil
from pathlib import Path, PurePosixPath

from cloud_network_benchmark.contracts.execution import BOUNDED_COPY_EOF_MARKER, CommandResult

# Importing this class supplies source-shaped provider/guest command bodies
# only; its module-level pytest marks are not copied to this module or class.
from tests.integration.test_f09_aws_runtime_factory import FakePreparedAwsRunner


class ReducedGuestArchiveTransport(FakePreparedAwsRunner):
    """Extend the real full-native fixture with actual local guest archive work.

    ``workspace_roots`` and ``native_root`` are populated by the reduced
    command router after construction. The archive/helper trees are derived
    lazily beside those actual per-deployment roots, keeping the six provider
    and parent contexts isolated.
    """

    def __init__(self):
        super().__init__()
        self.archive_receive_ids: list[str] = []
        self._archive_roots: dict[str, Path] | None = None
        self._archive_helpers: dict[str, Path] | None = None

    def _archive_paths(self) -> tuple[dict[str, Path], dict[str, Path]]:
        if self._archive_roots is None or self._archive_helpers is None:
            if not self.workspace_roots or not self.deployment_id:
                raise ValueError("actual workspace roots and deployment identity must be bound before archive use")
            base = Path(self.workspace_roots["vm_a"]).resolve(strict=False).parent / "guest-archive-transport"
            self._archive_roots = {role: base / "archives" / role for role in ("vm_a", "vm_b")}
            self._archive_helpers = {role: base / "helpers" / role for role in ("vm_a", "vm_b")}
        return self._archive_roots, self._archive_helpers

    def _role_for_action(self, action_id: str) -> str:
        matches = [role for role in ("vm_a", "vm_b") if action_id.endswith("-" + role)
            or ("-" + role + "-") in action_id]
        if len(matches) != 1:
            raise ValueError(f"archive action does not identify exactly one guest role: {action_id}")
        return matches[0]

    def _mapped_guest_path(self, role: str, remote_path: str) -> Path:
        archives, helpers = self._archive_paths()
        if (not remote_path.startswith("/") or PurePosixPath(remote_path).as_posix() != remote_path
                or ".." in PurePosixPath(remote_path).parts):
            raise ValueError("guest archive path must be normalized and absolute")
        helper_prefix = f"/tmp/cnb-guest-archive-{self.deployment_id}-{role}"
        archive_prefix = f"/tmp/cnb-closed-{self.deployment_id}-{role}"
        remote = PurePosixPath(remote_path)
        for prefix, root in ((helper_prefix, helpers[role]), (archive_prefix, archives[role])):
            base = PurePosixPath(prefix)
            if remote == base or base in remote.parents:
                relative = remote.relative_to(base)
                target = root.joinpath(*relative.parts)
                if not target.resolve(strict=False).is_relative_to(root.resolve(strict=False)):
                    raise ValueError("mapped guest archive path escaped its role root")
                return target
        raise ValueError(f"unexpected archive guest path: {remote_path}")

    def _command_result(self, request, *, stdout: str = "") -> CommandResult:
        return CommandResult(action_id=request.action_id, outcome="succeeded",
            started_at=self._now(), finished_at=self._now(), duration_seconds=0,
            exit_code=0, stdout=stdout, stderr="")

    def _copy_to_guest(self, request, role: str) -> CommandResult:
        source = Path(request.argv[-2])
        target = request.argv[-1]
        vm = (self.connection.vm_a if role == "vm_a" else self.connection.vm_b)
        expected_target = f"{vm.user}@{vm.host}:"
        if not target.startswith(expected_target):
            raise ValueError("archive helper transfer target differs from the bound guest endpoint")
        destination = self._mapped_guest_path(role, target.split(":", 1)[1])
        if source.is_symlink() or not source.is_file():
            raise ValueError("archive helper source must be an actual regular file")
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists() or destination.is_symlink():
            raise FileExistsError("archive helper destination already exists")
        shutil.copyfile(source, destination)
        return self._command_result(request)

    def _create_guest_archive(self, request, role: str) -> CommandResult:
        from cloud_network_benchmark.guest_archive import create_guest_archive

        if not request.argv or Path(request.argv[0]).name != "ssh" or request.action_type != "ssh":
            raise ValueError("guest archive execution requires the actual SSH command shape")
        if (len(request.argv) < 2 or (request.argv[-2] != f"{self.connection.vm_a.user}@{self.connection.vm_a.host}"
                and request.argv[-2] != f"{self.connection.vm_b.user}@{self.connection.vm_b.host}")):
            raise ValueError("guest archive execution target is not one of the bound VM endpoints")
        vm = self.connection.vm_a if role == "vm_a" else self.connection.vm_b
        if request.argv[-2] != f"{vm.user}@{vm.host}":
            raise ValueError("guest archive action role differs from its actual VM endpoint")
        outer = shlex.split(request.argv[-1])
        if len(outer) != 3 or outer[:2] != ["sh", "-c"]:
            raise ValueError("guest archive command must use the production sh -c transport")
        args = shlex.split(outer[2])
        if (len(args) < 5 or args[0] != "env" or not args[1].startswith("PYTHONPATH=")
                or Path(args[2]).name != "python3" or args[3:5] != ["-m", "cloud_network_benchmark.guest_archive"]):
            raise ValueError("guest archive request differs from the bounded source helper invocation")

        def option(name: str) -> str:
            try:
                return args[args.index(name) + 1]
            except (ValueError, IndexError) as exc:
                raise ValueError(f"guest archive request is missing {name}") from exc

        source_path = option("--source")
        archive_path = option("--destination")
        inventory_path = self._mapped_guest_path(role, option("--inventory"))
        source = Path(self.workspace_roots[role]).resolve(strict=True)
        if source_path != "/var/lib/cnb/work":
            raise ValueError("guest archive source differs from the bound native workspace")
        if not inventory_path.is_file() or inventory_path.is_symlink():
            raise ValueError("guest archive inventory must be the actual uploaded closed inventory")
        destination = self._mapped_guest_path(role, archive_path)
        manifest = create_guest_archive(source, destination,
            maximum_part_bytes=int(option("--maximum-part-bytes")),
            maximum_archive_bytes=int(option("--maximum-archive-bytes")),
            minimum_free_bytes=int(option("--minimum-free-bytes")),
            expected_inventory=json.loads(inventory_path.read_text(encoding="utf-8")))
        return self._command_result(request, stdout=json.dumps(manifest, sort_keys=True))

    def run(self, request):
        action = request.action_id
        if not action.startswith("f09-guest-archive-"):
            return super().run(request)
        self.actions.append(action)
        actual_prefixes = (
            f"f09-guest-archive-mkdir-{self.deployment_id}-",
            f"f09-guest-archive-inventory-{self.deployment_id}-",
            f"f09-guest-archive-helper-{self.deployment_id}-",
            f"f09-guest-archive-{self.deployment_id}-",
        )
        if not action.startswith(actual_prefixes):
            raise ValueError("guest archive command belongs to another deployment")
        role = self._role_for_action(action)
        archives, helpers = self._archive_paths()
        if "-mkdir-" in action:
            if request.action_type != "ssh":
                raise ValueError("guest archive directory setup requires SSH")
            remote_script = shlex.split(request.argv[-1])
            script = remote_script[-1] if remote_script else ""
            helper_prefix = f"/tmp/cnb-guest-archive-{self.deployment_id}-{role}"
            archive_prefix = f"/tmp/cnb-closed-{self.deployment_id}-{role}"
            if helper_prefix not in script or archive_prefix not in script:
                raise ValueError("guest archive mkdir command lacks its exact role paths")
            if any(path.exists() or path.is_symlink() for path in (archives[role], helpers[role])):
                raise FileExistsError("guest archive roots must be new")
            helpers[role].mkdir(parents=True)
            archives[role].parent.mkdir(parents=True, exist_ok=True)
            return self._command_result(request)
        if request.action_type == "scp":
            return self._copy_to_guest(request, role)
        return self._create_guest_archive(request, role)

    def run_bounded_copy(self, request, destination, maximum_payload_bytes):
        if not request.action_id.startswith("f09-guest-part-"):
            return super().run_bounded_copy(request, destination, maximum_payload_bytes)
        action = request.action_id
        self.actions.append(action)
        self.archive_receive_ids.append(action)
        if (not action.startswith(f"f09-guest-part-{self.deployment_id}-")
                or request.action_type != "ssh" or request.argv[:2] != ["ssh", "-T"]
                or type(maximum_payload_bytes) is not int or maximum_payload_bytes <= 0):
            raise ValueError("bounded guest archive receive request is outside its exact deployment scope")
        role = self._role_for_action(action)
        vm = self.connection.vm_a if role == "vm_a" else self.connection.vm_b
        if request.argv[-2] != f"{vm.user}@{vm.host}":
            raise ValueError("bounded guest part endpoint differs from its actual role binding")
        remote_argv = shlex.split(request.argv[-1])
        if remote_argv[:2] != ["sh", "-c"] or len(remote_argv) != 3:
            raise ValueError("bounded guest part command must use the production sh -c script")
        script = remote_argv[2]
        match = re.search(r"exec 3< (.*?) \|\| exit 1;", script)
        if (match is None or BOUNDED_COPY_EOF_MARKER not in script
                or "os.read(3, 1)" not in script):
            raise ValueError("bounded guest part command lacks the actual EOF proof contract")
        bound = re.search(r"dd bs=65536 count=(\d+) iflag=count_bytes status=none", script)
        if bound is None or int(bound.group(1)) != maximum_payload_bytes:
            raise ValueError("bounded guest part script cap differs from the caller byte bound")
        guest_path = shlex.split(match.group(1))[0]
        source = self._mapped_guest_path(role, guest_path)
        root = self._archive_paths()[0][role].resolve(strict=True)
        if (not source.resolve(strict=True).is_relative_to(root)
                or source.is_symlink() or not source.is_file()):
            raise ValueError("bounded guest part source is outside the closed archive root")
        payload = source.read_bytes()
        if len(payload) != maximum_payload_bytes:
            return CommandResult(action_id=action, outcome="failed", started_at=self._now(),
                finished_at=self._now(), duration_seconds=0, exit_code=1,
                stdout="", stderr="guest archive part size differs from its frozen bound")
        target = Path(destination)
        with target.open("xb") as stream:
            stream.write(payload)
        return CommandResult(action_id=action, outcome="succeeded", started_at=self._now(),
            finished_at=self._now(), duration_seconds=0, exit_code=0, stdout="",
            stderr=BOUNDED_COPY_EOF_MARKER + "\n")
