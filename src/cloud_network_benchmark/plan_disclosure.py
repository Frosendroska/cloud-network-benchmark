"""Read-only, complete disclosure and immutable checkpoint for manual plan review.

This command does not create or approve operator receipts and does not call Terraform.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shlex
import sys
import time

from . import terraform_plan as tp


# SHA-256 of terraform_plan.py at integrated core baseline def3a5cd922ccf034166070ed9eb76817f2703e6.
PINNED_VALIDATOR_SHA256 = "45b2a0df8db281b1e7a7b0ebd7517982f1820c294f6b7ce767ea2f5bcab5675c"


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _validator_identity() -> dict:
    path = Path(tp.__file__).resolve(strict=True)
    sha = digest(path.read_bytes())
    if sha != PINNED_VALIDATOR_SHA256:
        raise tp.PlanReviewError("terraform_plan validator does not match frozen release SHA-256")
    return {"path": str(path), "sha256": sha}


def _regular(path: Path) -> bytes:
    if path.is_symlink() or not path.is_file():
        raise tp.PlanReviewError(f"required evidence is not a regular file: {path}")
    return path.read_bytes()


def _configured_provider_mirrors() -> list[Path]:
    """Read only filesystem-mirror paths from Terraform's explicit CLI config."""
    config = os.environ.get("TF_CLI_CONFIG_FILE")
    if not config:
        return []
    path = Path(config)
    if path.is_symlink() or not path.is_file():
        return []
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return []
    mirrors = []
    block = re.compile(r'(?ms)filesystem_mirror\s*\{([^{}]*)\}')
    path_value = re.compile(r'(?m)^\s*path\s*=\s*("(?:[^"\\]|\\.)*")\s*$')
    for match in block.finditer(text):
        value = path_value.search(match.group(1))
        if value is None:
            continue
        try:
            candidate = Path(json.loads(value.group(1)))
            if (candidate.is_absolute() and candidate.is_dir() and not candidate.is_symlink()
                    and candidate.resolve(strict=True) == candidate):
                mirrors.append(candidate)
        except (OSError, ValueError, TypeError):
            continue
    mirrors = sorted(set(mirrors))
    return mirrors if len(mirrors) == 1 else []


def _provider_mirror_snapshot() -> tuple[str, str, str] | None:
    config = os.environ.get("TF_CLI_CONFIG_FILE")
    if not config:
        return None
    path = Path(config)
    try:
        payload = path.read_bytes()
    except OSError:
        return None
    mirrors = _configured_provider_mirrors()
    if len(mirrors) != 1:
        return None
    return str(path.absolute()), digest(payload), str(mirrors[0])


def _terraform_provider_cache_link(path: Path, root: Path,
                                   mirror_snapshot: tuple[str, str, str] | None) -> bool:
    """Recognize only the source-owned review-workspace link to its configured mirror."""
    if not path.is_symlink():
        return False
    try:
        relative = path.relative_to(root)
    except ValueError:
        return False
    parts = relative.parts
    if (len(parts) < 7 or parts[-6] != "providers"
            or parts[-5] != "registry.terraform.io"):
        return False
    namespace, provider, version, platform = parts[-4:]
    if (not re.fullmatch(r"[A-Za-z0-9_-]+", namespace)
            or not re.fullmatch(r"[A-Za-z0-9_-]+", provider)
            or not re.fullmatch(r"[0-9]+(?:\.[0-9]+){2}(?:[-+][A-Za-z0-9.-]+)?", version)
            or not re.fullmatch(r"[A-Za-z0-9_]+", platform)):
        return False
    if not path.is_dir():
        return False
    data_index = len(parts) - 7
    data_name = parts[data_index]
    if provider == "aws":
        layout = (len(parts) == 10 and parts[0].startswith("cnb-")
                  and parts[2].endswith("-deployment")
                  and re.fullmatch(r"terraform-data-(?:safety|pair)-[0-9a-f]{8}", data_name) is not None)
    elif provider == "azurerm":
        layout = (len(parts) == 9 and parts[0].startswith("20")
                  and parts[1].endswith("-deployment")
                  and re.fullmatch(r"terraform-data-(?:expiry|pair)", data_name) is not None)
    elif provider == "google":
        layout = (len(parts) == 11 and re.fullmatch(r"[0-9a-f]{12}", parts[0]) is not None
                  and parts[2].endswith("-deployment") and parts[3] in {"protection", "pair"}
                  and re.fullmatch(r"terraform-data-(?:protection|pair)-[0-9a-f]{8}", data_name) is not None)
    else:
        layout = False
    if namespace != "hashicorp" or not layout:
        return False
    if mirror_snapshot is None:
        return False
    config_path, expected_config_hash, mirror_text = mirror_snapshot
    try:
        if (os.environ.get("TF_CLI_CONFIG_FILE") != config_path
                or digest(Path(config_path).read_bytes()) != expected_config_hash):
            return False
        expected = Path(mirror_text) / "registry.terraform.io" / namespace / provider / version / platform
        return (expected.is_dir() and os.readlink(path) == str(expected)
                and expected.resolve(strict=True) == path.resolve(strict=True))
    except OSError:
        return False


def _provider_cache_signature(path: Path) -> tuple:
    target = path.resolve(strict=True)
    stat = target.stat()
    return (os.readlink(path), str(target), stat.st_dev, stat.st_ino,
            stat.st_mode, stat.st_size, stat.st_mtime_ns)


def _walk_owned(root: Path, mirror_snapshot=None, cache_links: dict | None = None):
    """Walk an owned attempt root without following links, pruning only mirror aliases."""
    if mirror_snapshot is None:
        mirror_snapshot = _provider_mirror_snapshot()
    for current, dirs, files in os.walk(root, followlinks=False):
        directory = Path(current)
        kept = []
        for name in dirs:
            child = directory / name
            if child.is_symlink():
                if _terraform_provider_cache_link(child, root, mirror_snapshot):
                    if cache_links is not None:
                        cache_links[child] = _provider_cache_signature(child)
                    continue
                raise tp.PlanReviewError("owned roots contain an unrecognized symlink")
            kept.append(name)
        dirs[:] = kept
        for name in files:
            child = directory / name
            if child.is_symlink():
                if _terraform_provider_cache_link(child, root, mirror_snapshot):
                    if cache_links is not None:
                        cache_links[child] = _provider_cache_signature(child)
                    continue
                raise tp.PlanReviewError("owned roots contain an unrecognized symlink")
            yield child


def _assert_pending_original(path: Path, request_bytes: bytes) -> None:
    if _regular(path) != request_bytes:
        raise tp.PlanReviewError("original review request changed during disclosure")
    if any((path.parent / name).exists() or (path.parent / name).is_symlink()
           for name in ("operator-review.json", "accepted-review.json",
                        "review-cancelled.json", "failure.json")):
        raise tp.PlanReviewError("original review request is no longer pending")


def _request_inventory(roots: list[Path], mirror_snapshot=None,
                       cache_links: dict | None = None) -> dict[Path, str]:
    inventory = {}
    for root in roots:
        if not root.exists():
            continue
        for path in _walk_owned(root, mirror_snapshot, cache_links):
            if path.name == "review-request.json":
                inventory[path] = digest(_regular(path))
    return inventory


def _cache_links_are_append_only(previous: dict[Path, tuple], current: dict[Path, tuple]) -> bool:
    return (previous.keys() <= current.keys()
            and all(current[path] == signature for path, signature in previous.items()))


def _assert_request_inventory(roots: list[Path], expected: dict[Path, str],
                              mirror_snapshot=None, expected_cache_links=None) -> dict[Path, tuple]:
    cache_links = {}
    if _request_inventory(roots, mirror_snapshot, cache_links) != expected:
        raise tp.PlanReviewError("original request set changed during disclosure")
    if (expected_cache_links is not None
            and not _cache_links_are_append_only(expected_cache_links, cache_links)):
        raise tp.PlanReviewError("Terraform provider cache links changed during disclosure")
    return cache_links


def _safe_root(path: Path) -> Path:
    absolute = path.absolute()
    if path.is_symlink() or absolute != absolute.resolve(strict=False):
        raise tp.PlanReviewError("owned root must not traverse symlinks")
    if absolute.exists() and not absolute.is_dir():
        raise tp.PlanReviewError("owned root must be a directory when present")
    ancestor = absolute
    while not ancestor.exists():
        ancestor = ancestor.parent
    if ancestor.is_symlink() or not ancestor.is_dir():
        raise tp.PlanReviewError("owned root must descend from a real directory")
    return absolute


def _private_destination(path: Path) -> Path:
    path = path.absolute()
    if path.exists() or path.is_symlink():
        raise tp.PlanReviewError("disclosure destination already exists")
    parent = path.parent
    if parent.is_symlink() or parent.resolve(strict=True) != parent or not parent.is_dir():
        raise tp.PlanReviewError("disclosure parent must be an existing non-symlink directory")
    if parent.stat().st_mode & 0o077:
        raise tp.PlanReviewError("disclosure parent must have private permissions")
    os.mkdir(path, 0o700)
    return path


def _source_inventory(root: Path, mirror_snapshot=None) -> dict:
    files = sorted(p for p in _walk_owned(root, mirror_snapshot) if p.is_file()
                   and (p.suffix in {".tf", ".py", ".ps1"} or p.name == ".terraform.lock.hcl")
                   and ".terraform" not in p.parts)
    return {str(p.relative_to(root)): digest(_regular(p)) for p in files}


def _bounded_pages(section: dict, payload: bytes, limit: int) -> list[tuple[str, str]]:
    """Split readable UTF-8 text without cutting a code point; page metadata stays in budget."""
    text = payload.decode("utf-8")
    size = max(1, limit - 768)
    while True:
        chunks, current, used = [], [], 0
        for char in text:
            encoded_size = len(char.encode("utf-8"))
            if used and used + encoded_size > size:
                chunks.append("".join(current)); current, used = [], 0
            current.append(char); used += encoded_size
        if current: chunks.append("".join(current))
        if not chunks: raise tp.PlanReviewError("empty section is not disclosable")
        rows = []
        valid = True
        for index, chunk in enumerate(chunks, 1):
            header = (f"\n===== {section['section']} | page {index}/{len(chunks)} | "
                      f"section_sha256={section['sha256']} | payload_sha256={digest(payload)} =====\n")
            if len(header.encode()) + len(chunk.encode()) > limit:
                valid = False; break
            rows.append((header, chunk))
        if valid: return rows
        if size <= 1: raise tp.PlanReviewError("display limit cannot fit readable disclosure header")
        size = max(1, size - 256)


def _atomic_new(path: Path, payload: bytes) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(payload); f.flush(); os.fsync(f.fileno())


def _sections(plan: dict) -> list[dict]:
    """Retain every field, grouping resource values by address for readable review."""
    sections = []
    for key in sorted(plan):
        value = plan[key]
        if key == "resource_changes" and isinstance(value, list):
            for i, item in enumerate(value):
                body = item
                raw = (json.dumps(body, sort_keys=True, indent=2, ensure_ascii=False) + "\n").encode()
                sections.append({"section": f"resource_changes/{i:06d}/{item.get('address','unknown')}",
                                 "sha256": digest(raw), "json": body})
        else:
            raw = (json.dumps(value, sort_keys=True, indent=2, ensure_ascii=False) + "\n").encode()
            sections.append({"section": key, "sha256": digest(raw), "json": value})
    return sections


_CREDENTIAL_VALUE_PATTERNS = (
    re.compile(r"-----BEGIN (?:[A-Z0-9 ]+ )?PRIVATE KEY-----"),
    re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b"),
    re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]{8,}"),
    re.compile(r"(?i)\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})\b"),
    re.compile(r"(?i)(?:^|[?&\s])sig=[A-Za-z0-9%+/=_-]{12,}"),
)


def _has_material(value):
    """Whether a plan value contains something beyond Terraform's empty placeholders."""
    if value is None or value is False or value == "":
        return False
    if isinstance(value, dict):
        return any(_has_material(item) for item in value.values())
    if isinstance(value, list):
        return any(_has_material(item) for item in value)
    return True


def _valid_public_ssh_key(value):
    if not isinstance(value, str):
        return False
    fields = value.strip().split()
    if len(fields) < 2 or fields[0] not in {
            "ssh-ed25519", "ssh-rsa", "ecdsa-sha2-nistp256", "ecdsa-sha2-nistp384",
            "ecdsa-sha2-nistp521"}:
        return False
    try:
        blob = base64.b64decode(fields[1], validate=True)
    except (ValueError, base64.binascii.Error):
        return False
    offset = 0

    def take_string():
        nonlocal offset
        if offset + 4 > len(blob):
            raise ValueError
        size = int.from_bytes(blob[offset:offset + 4], "big")
        offset += 4
        if size == 0 or offset + size > len(blob):
            raise ValueError
        result = blob[offset:offset + size]
        offset += size
        return result

    try:
        algorithm = take_string().decode("ascii")
        if algorithm != fields[0]:
            return False
        if algorithm == "ssh-ed25519":
            valid = len(take_string()) == 32
        elif algorithm == "ssh-rsa":
            valid = bool(take_string()) and bool(take_string())
        else:
            curve = take_string().decode("ascii")
            point = take_string()
            expected_curve = algorithm.removeprefix("ecdsa-sha2-")
            expected_point_size = {"nistp256": 65, "nistp384": 97, "nistp521": 133}[expected_curve]
            valid = curve == expected_curve and len(point) == expected_point_size and point[:1] == b"\x04"
        return valid and offset == len(blob)
    except (ValueError, UnicodeDecodeError):
        return False


def _is_azure_public_ssh_marker_path(path):
    if len(path) == 6 and path[0] == "planned_values" and path[1:3] == ("root_module", "resources"):
        return path[3].isdigit() and path[4:] == ("sensitive_values", "admin_ssh_key")
    return (len(path) == 5 and path[0] == "resource_changes" and path[1].isdigit()
            and path[2:] == ("change", "after_sensitive", "admin_ssh_key"))


def _is_approved_azure_public_ssh_block(value, path, variables):
    if not _is_azure_public_ssh_marker_path(path) or not isinstance(variables, dict):
        return False
    blocks = value if isinstance(value, list) else [value]
    if len(blocks) != 1 or not isinstance(blocks[0], dict) or set(blocks[0]) != {"username", "public_key"}:
        return False
    block = blocks[0]
    public_key = variables.get("ssh_public_key")
    return (block.get("username") == variables.get("admin_username")
            and block.get("public_key") == public_key
            and _valid_public_ssh_key(public_key))


def _is_gcp_public_ssh_marker_path(path):
    if len(path) == 7 and path[0] == "planned_values" and path[1:3] == ("root_module", "resources"):
        return path[3].isdigit() and path[4:] == ("sensitive_values", "metadata", "ssh-keys")
    return (len(path) == 6 and path[0] == "resource_changes" and path[1].isdigit()
            and path[2:] == ("change", "after_sensitive", "metadata", "ssh-keys"))


def _source_gcp_public_ssh_template(source_root):
    """Return the one fixed public-key metadata template from trusted GCP protection source."""
    if source_root is None or source_root.name != "protection" or source_root.parent.name != "gcp":
        return None
    source_path = source_root / "main.tf"
    if source_path.is_symlink() or not source_path.is_file():
        return None
    try:
        source = source_path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return None
    # Keep the exception tied to the exact fixed source expression. Any source edit
    # changes the already-validated source inventory and must also retain this form.
    assignments = re.findall(r"(?m)^\s*ssh-keys\s*=\s*\"([^\"]*)\"\s*$", source)
    if assignments != ["benchmark:${var.public_key}"]:
        return None
    return "benchmark:{}"


def _is_approved_gcp_public_ssh_value(value, path, variables, source_root):
    if (not _is_gcp_public_ssh_marker_path(path) or not isinstance(variables, dict)
            or not isinstance(value, str)):
        return False
    template = _source_gcp_public_ssh_template(source_root)
    public_key = variables.get("public_key")
    return (template is not None and _valid_public_ssh_key(public_key)
            and value == template.format(public_key))


def _is_aws_public_user_data_marker_path(path):
    if len(path) == 6 and path[0] == "planned_values" and path[1:3] == ("root_module", "resources"):
        return path[3].isdigit() and path[4:] == ("sensitive_values", "user_data")
    return (len(path) == 5 and path[0] == "resource_changes" and path[1].isdigit()
            and path[2:] == ("change", "after_sensitive", "user_data"))


def _source_aws_public_user_data(source_root):
    """Check AWS pair source maps instance user_data directly to its sensitive input."""
    if source_root is None or source_root.name != "pair" or source_root.parent.name != "aws":
        return False
    main_path, variables_path = source_root / "main.tf", source_root / "variables.tf"
    if any(p.is_symlink() or not p.is_file() for p in (main_path, variables_path)):
        return False
    try:
        main = main_path.read_text(encoding="utf-8")
        variables = variables_path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return False
    assignments = re.findall(r"(?m)^\s*user_data\s*=\s*var\.host_key_bootstrap\s*$", main)
    declarations = re.findall(
        r'(?s)variable\s+"host_key_bootstrap"\s*\{(?P<body>.*?)\}', variables)
    return (len(assignments) == 3 and len(declarations) == 1
            and re.search(r"(?m)^\s*sensitive\s*=\s*true\s*$", declarations[0]) is not None)


def _is_approved_aws_public_user_data(value, path, variables, source_root):
    if (not _is_aws_public_user_data_marker_path(path) or not isinstance(value, str)
            or not isinstance(variables, dict) or not _source_aws_public_user_data(source_root)
            or value != variables.get("host_key_bootstrap")):
        return False
    # This is the fixed provider-generated cloud-init template. Its only substitution is
    # one structurally valid OpenSSH public key; arbitrary bootstrap and credential data fail.
    try:
        import yaml
    except ImportError:
        return False
    try:
        document = yaml.safe_load(value)
    except (ValueError, yaml.YAMLError):
        return False
    if not isinstance(document, dict) or set(document) != {"ssh_authorized_keys", "runcmd"}:
        return False
    keys = document.get("ssh_authorized_keys")
    expected_command = (
        "boot_id=$(cat /proc/sys/kernel/random/boot_id); "
        "fingerprint=$(ssh-keygen -E sha256 -lf /etc/ssh/ssh_host_ed25519_key.pub | awk '{print $2}'); "
        "printf 'CNB_HOSTKEY_BOOT_ID=%s\\nCNB_ED25519_HOST_FINGERPRINT=%s\\n' \"$boot_id\" \"$fingerprint\""
    )
    if (not isinstance(keys, list) or len(keys) != 1 or not _valid_public_ssh_key(keys[0])
            or document.get("runcmd") != [["bash", "-lc", expected_command]]):
        return False
    expected = "#cloud-config\n" + yaml.safe_dump(
        {"ssh_authorized_keys": keys, "runcmd": [["bash", "-lc", expected_command]]},
        sort_keys=False)
    return value == expected


def _is_azure_custom_data_marker_path(path):
    if len(path) == 6 and path[0] == "planned_values" and path[1:3] == ("root_module", "resources"):
        return path[3].isdigit() and path[4:] == ("sensitive_values", "custom_data")
    return (len(path) == 5 and path[0] == "resource_changes" and path[1].isdigit()
            and path[2:] == ("change", "after_sensitive", "custom_data"))


def _source_azure_custom_data(source_root):
    source_path = source_root / "main.tf"
    if source_path.is_symlink() or not source_path.is_file():
        return None
    try:
        source = source_path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return None
    pattern = re.compile(
        r"(?m)^(?P<indent>[ \t]*)custom_data\s*=\s*base64encode\(<<-CLOUD\n"
        r"(?P<body>.*?)(?P=indent)CLOUD\n(?P=indent)\)", re.S)
    matches = list(pattern.finditer(source))
    if len(matches) != 1:
        return None
    lines = matches[0].group("body").splitlines(keepends=True)
    margins = [len(line) - len(line.lstrip(" \t")) for line in lines if line.strip()]
    if not margins:
        return None
    margin = min(margins)
    content = "".join(line[margin:] if line.strip() else line for line in lines)
    # Dynamic Terraform interpolation is outside this fixed public-template exception.
    if "${" in content or "%{" in content:
        return None
    return base64.b64encode(content.encode("utf-8")).decode("ascii")


def _marked_material(markers, values, path=(), *, provider=None, variables=None,
                     source_root=None, trusted_source=False):
    """Match Terraform's boolean sensitivity mask to its value tree without printing it."""
    if markers is True:
        if not _has_material(values):
            return False
        if provider == "azure" and trusted_source:
            if _is_approved_azure_public_ssh_block(values, path, variables):
                return False
            if (_is_azure_custom_data_marker_path(path) and isinstance(values, str)
                    and _source_azure_custom_data(source_root) == values):
                return False
        if (provider == "gcp" and trusted_source
                and _is_approved_gcp_public_ssh_value(values, path, variables, source_root)):
            return False
        if (provider == "aws" and trusted_source
                and _is_approved_aws_public_user_data(values, path, variables, source_root)):
            return False
        return True
    if markers is not False and not isinstance(markers, (dict, list)):
        # Malformed marker leaves fail closed.
        return True
    if isinstance(markers, dict):
        if not isinstance(values, dict):
            values = {}
        for key, marker in markers.items():
            value = values.get(key)
            if marker is True:
                if _has_material(value):
                    marker_path = path + (str(key),)
                    ssh_public = (provider == "azure" and trusted_source
                                  and _is_approved_azure_public_ssh_block(value, marker_path, variables))
                    public_custom_data = (provider == "azure" and trusted_source
                                          and _is_azure_custom_data_marker_path(marker_path)
                                          and isinstance(value, str)
                                          and _source_azure_custom_data(source_root) == value)
                    gcp_public_ssh = (provider == "gcp" and trusted_source
                                      and _is_approved_gcp_public_ssh_value(
                                          value, marker_path, variables, source_root))
                    aws_public_user_data = (provider == "aws" and trusted_source
                                            and _is_approved_aws_public_user_data(
                                                value, marker_path, variables, source_root))
                    if not (ssh_public or public_custom_data or gcp_public_ssh or aws_public_user_data):
                        return True
            elif marker is False:
                continue
            elif isinstance(marker, (dict, list)):
                if _marked_material(marker, value, path + (str(key),), provider=provider,
                                    variables=variables, source_root=source_root,
                                    trusted_source=trusted_source):
                    return True
            else:
                return True
        return False
    if isinstance(markers, list):
        if not isinstance(values, list):
            values = []
        for index, marker in enumerate(markers):
            value = values[index] if index < len(values) else None
            if marker is True:
                if _has_material(value):
                    marker_path = path + (str(index),)
                    ssh_public = (provider == "azure" and trusted_source
                                  and _is_approved_azure_public_ssh_block(value, marker_path, variables))
                    public_custom_data = (provider == "azure" and trusted_source
                                          and _is_azure_custom_data_marker_path(marker_path)
                                          and isinstance(value, str)
                                          and _source_azure_custom_data(source_root) == value)
                    gcp_public_ssh = (provider == "gcp" and trusted_source
                                      and _is_approved_gcp_public_ssh_value(
                                          value, marker_path, variables, source_root))
                    aws_public_user_data = (provider == "aws" and trusted_source
                                            and _is_approved_aws_public_user_data(
                                                value, marker_path, variables, source_root))
                    if not (ssh_public or public_custom_data or gcp_public_ssh or aws_public_user_data):
                        return True
            elif marker is False:
                continue
            elif isinstance(marker, (dict, list)):
                if _marked_material(marker, value, path + (str(index),), provider=provider,
                                    variables=variables, source_root=source_root,
                                    trusted_source=trusted_source):
                    return True
            else:
                return True
        return False
    return False


def _credential_key_has_material(value):
    if isinstance(value, dict):
        return any(_credential_key_has_material(item) for item in value.values())
    if isinstance(value, list):
        return any(_credential_key_has_material(item) for item in value)
    return _has_material(value)


def _aws_public_http_tokens_path(path):
    if len(path) == 8 and path[0] == "planned_values" and path[1:3] == ("root_module", "resources"):
        return path[3].isdigit() and path[4:] == ("values", "metadata_options", "0", "http_tokens")
    if len(path) == 7 and path[0] == "resource_changes" and path[1].isdigit():
        return path[2:] == ("change", "after", "metadata_options", "0", "http_tokens")
    if len(path) == 8 and path[0] == "configuration" and path[1:3] == ("root_module", "resources"):
        return path[3].isdigit() and path[4:] == ("expressions", "metadata_options", "0", "http_tokens")
    return False


def _sensitive(value, key="", path=(), provider=None, variables=None,
               source_root=None, trusted_source=False):
    forbidden = ("password", "secret", "private_key", "privatekey", "access_key", "credential", "token")
    if key == "disable_password_authentication":
        if isinstance(value, bool):
            return False
        if (isinstance(value, dict) and set(value) == {"expression"}
                and isinstance(value["expression"], dict)
                and set(value["expression"]) == {"constant_value"}
                and isinstance(value["expression"]["constant_value"], bool)):
            return False
        if (isinstance(value, dict) and set(value) == {"constant_value"}
                and isinstance(value["constant_value"], bool)):
            return False
    if provider == "aws" and trusted_source and key == "http_tokens" and _aws_public_http_tokens_path(path):
        if value == "required" or (isinstance(value, dict) and set(value) == {"constant_value"}
                                    and value["constant_value"] == "required"):
            return False
    if any(word in key.lower() for word in forbidden) and _credential_key_has_material(value):
        return True
    if isinstance(value, str) and any(pattern.search(value) for pattern in _CREDENTIAL_VALUE_PATTERNS):
        return True
    if isinstance(value, dict):
        # Terraform emits nonempty sensitivity masks for values trees even when all
        # marked leaves are null, empty, or omitted/unknown. Inspect marker leaves
        # against their sibling values, retaining the full plan either way.
        for marker_key, value_key in (
            ("before_sensitive", "before"),
            ("after_sensitive", "after"),
            ("sensitive_values", "values"),
        ):
            if marker_key in value and _marked_material(
                    value[marker_key], value.get(value_key), path + (marker_key,),
                    provider=provider, variables=variables, source_root=source_root,
                    trusted_source=trusted_source):
                return True
        # A standalone `sensitive: true` is schema metadata unless an actual
        # sibling value is present (for example planned output values).
        if value.get("sensitive") is True and _has_material(value.get("value")):
            return True
        if value.get("sensitive") not in (None, True, False):
            return True
        # Terraform's root output_sensitive map is a mask over named output values,
        # separate from output_changes.change.after_sensitive. Pair it with the
        # corresponding planned output or change value; an unpaired true marker is
        # ambiguous and therefore blocks disclosure.
        if "output_sensitive" in value:
            masks = value["output_sensitive"]
            if not isinstance(masks, dict):
                return True
            planned_outputs = value.get("planned_values", {}).get("outputs", {})
            output_changes = value.get("output_changes", {})
            for name, marker in masks.items():
                if marker is False:
                    continue
                if marker is not True:
                    return True
                change = output_changes.get(name) if isinstance(output_changes, dict) else None
                change_body = change.get("change") if isinstance(change, dict) else None
                if isinstance(change_body, dict) and "after" in change_body:
                    output_value = change_body["after"]
                else:
                    planned = planned_outputs.get(name) if isinstance(planned_outputs, dict) else None
                    if not isinstance(planned, dict) or "value" not in planned:
                        return True
                    output_value = planned["value"]
                if _has_material(output_value):
                    return True
        marker_keys = {
            "before_sensitive", "after_sensitive", "sensitive_values",
            "before_unknown", "after_unknown", "output_sensitive",
        }
        return any(_sensitive(v, str(k), path + (str(k),), provider, variables, source_root, trusted_source)
                   for k, v in value.items() if k not in marker_keys)
    if isinstance(value, list):
        return any(_sensitive(v, key, path + (str(index),), provider, variables, source_root, trusted_source)
                   for index, v in enumerate(value))
    return False


def disclose(roots: list[Path], checkpoint_root: Path, deadline_epoch: float,
             display_limit: int = 32768, *, request_path: Path | None = None,
             mirror_snapshot=None, cache_links: dict | None = None) -> list[Path]:
    _validator_identity()
    if isinstance(deadline_epoch, bool) or not isinstance(deadline_epoch, (int, float)) or not math.isfinite(deadline_epoch):
        raise tp.PlanReviewError("original plan-review deadline must be a finite epoch timestamp")
    owned = [_safe_root(p) for p in roots]
    if mirror_snapshot is None:
        mirror_snapshot = _provider_mirror_snapshot()
    observed_cache_links = {}
    request_inventory = (_request_inventory(owned, mirror_snapshot, observed_cache_links)
                         if request_path is not None else None)
    if (cache_links is not None
            and not _cache_links_are_append_only(cache_links, observed_cache_links)):
        raise tp.PlanReviewError("Terraform provider cache links changed before disclosure")
    expected_cache_links = dict(observed_cache_links)
    checkpoint_root = checkpoint_root.absolute()
    if checkpoint_root.exists() or checkpoint_root.is_symlink():
        raise tp.PlanReviewError("checkpoint destination must not already exist")
    if not checkpoint_root.parent.is_dir() or checkpoint_root.parent.is_symlink() or checkpoint_root.parent.resolve() != checkpoint_root.parent:
        raise tp.PlanReviewError("checkpoint parent must be an existing real directory")
    requests = []
    if request_path is not None:
        request_path = request_path.absolute()
        if request_path.name != "review-request.json" or request_path.is_symlink():
            raise tp.PlanReviewError("exact request must be an original regular review-request.json")
        if any(p.is_symlink() for p in (request_path, *request_path.parents)):
            raise tp.PlanReviewError("exact request traverses a symlink")
        if not any(request_path.is_relative_to(root) for root in owned):
            raise tp.PlanReviewError("exact original request is outside the owned roots")
        if not request_path.is_file():
            raise tp.PlanReviewError("exact original request is not a regular file")
        requests = [request_path]
        if request_path not in request_inventory:
            raise tp.PlanReviewError("exact original request is outside the current request inventory")
        for other in request_inventory:
            if other == request_path:
                continue
            if not any((other.parent / marker).exists() or (other.parent / marker).is_symlink()
                       for marker in ("accepted-review.json", "review-cancelled.json", "failure.json")):
                raise tp.PlanReviewError("another original request is still pending or unresolved")
    else:
        for root in owned:
            for path in _walk_owned(root, mirror_snapshot, observed_cache_links):
                if path.name != "review-request.json":
                    continue
                requests.append(path)
        requests = sorted(set(requests))
    if not requests:
        raise tp.PlanReviewError("no pending review requests found in explicitly owned roots")
    # Validate every candidate before creating any checkpoint or disclosure.
    prepared = []
    for req_path in requests:
        if any((req_path.parent / name).exists() or (req_path.parent / name).is_symlink()
               for name in ("operator-review.json", "accepted-review.json",
                            "review-cancelled.json", "failure.json")):
            raise tp.PlanReviewError("review request already has a receipt; it is not pending")
        request_bytes = _regular(req_path)
        binding = json.loads(request_bytes)
        directory = req_path.parent
        plan_bytes = _regular(directory / "saved-plan.json")
        binary_bytes = _regular(directory / "saved.tfplan")
        if digest(plan_bytes) != binding.get("json_sha256") or digest(binary_bytes) != binding.get("plan_sha256"):
            raise tp.PlanReviewError("saved plan bytes do not match request binding")
        # The current request schema binds the variable hash, but not its path. Resolve within
        # explicitly owned roots and fail closed unless that hash identifies exactly one file.
        vars_candidates = [p for root in owned if root.exists()
                           for p in _walk_owned(root, mirror_snapshot, observed_cache_links)
                           if p.name.endswith((".tfvars.json", ".tfvars")) or p.name == "vars.json"]
        vars_candidates = [p for p in vars_candidates if digest(_regular(p)) == binding.get("variables_sha256")]
        if len(vars_candidates) != 1:
            raise tp.PlanReviewError("could not uniquely bind original variable file within owned roots")
        variables_path = vars_candidates[0]
        root_path = Path(binding["root"])
        if root_path.is_symlink() or root_path.absolute() != root_path.resolve(strict=True):
            raise tp.PlanReviewError("Terraform root traverses a symlink")
        state_path = Path(binding["state"])
        source_inventory = _source_inventory(root_path, mirror_snapshot)
        if digest(tp._json(source_inventory)) != binding.get("source_sha256"):
            raise tp.PlanReviewError("Terraform source changed since plan review request")
        state_bytes = _regular(state_path) if state_path.exists() or state_path.is_symlink() else None
        if (digest(state_bytes) if state_bytes is not None else None) != binding.get("state_sha256"):
            raise tp.PlanReviewError("state changed since plan review request")
        variables = json.loads(_regular(variables_path))
        plan = json.loads(plan_bytes)
        provider = root_path.parent.name
        computed_scope = tp.validate_scope(plan, provider, root_path, variables)
        if binding.get("scope") != computed_scope:
            raise tp.PlanReviewError("validated plan scope differs from the immutable review request")
        if _sensitive(plan, provider=provider, variables=variables, source_root=root_path,
                      trusted_source=True):
            raise tp.PlanReviewError("plan contains sensitive-marked or credential-like fields; disclosure withheld")
        canonical = (json.dumps(plan, sort_keys=True, indent=2, ensure_ascii=False) + "\n").encode()
        if json.loads(canonical) != plan:
            raise tp.PlanReviewError("lossless plan serialization failed")
        sections = _sections(plan)
        reconstructed = {s["section"]: s["json"] for s in sections if not s["section"].startswith("resource_changes/")}
        reconstructed["resource_changes"] = [s["json"] for s in sections if s["section"].startswith("resource_changes/")]
        if json.dumps(reconstructed, sort_keys=True, separators=(",", ":")) != json.dumps(plan, sort_keys=True, separators=(",", ":")):
            raise tp.PlanReviewError("sectional disclosure does not reconstruct original plan")
        if time.time() >= deadline_epoch:
            raise tp.PlanReviewError("original plan-review deadline has elapsed")
        prepared.append((req_path, request_bytes, binding, plan_bytes, binary_bytes, variables_path, state_path,
                         state_bytes, root_path, source_inventory, canonical, sections))
    if request_inventory is not None:
        expected_cache_links = _assert_request_inventory(
            owned, request_inventory, mirror_snapshot, expected_cache_links)
    # Atomic root creation; no existing target can be overwritten.
    if display_limit < 1024:
        raise tp.PlanReviewError("display limit must be at least 1024 bytes")
    os.mkdir(checkpoint_root, 0o700)
    published = []
    try:
        manifest = {"format": "cnb-plan-disclosure-v1", "created_utc_epoch": time.time(), "deadline_utc_epoch": deadline_epoch, "entries": []}
        staged_output = []
        for req_path, req_bytes, binding, plan_bytes, binary_bytes, var_path, state_path, state_bytes, root_path, source_inventory, canonical, sections in prepared:
            entry_id = str(binding["action_id"])
            if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", entry_id):
                raise tp.PlanReviewError("unsafe or missing action id in review request")
            if any(e["entry_id"] == entry_id for e in manifest["entries"]):
                raise tp.PlanReviewError("duplicate action id across discovered review requests")
            dest = checkpoint_root / entry_id
            os.mkdir(dest, 0o700)
            source_files = {"review-request.json": req_bytes, "saved-plan.json": plan_bytes,
                            "saved.tfplan": binary_bytes, "variables" + var_path.suffix: _regular(var_path)}
            if state_bytes is not None: source_files["state"] = state_bytes
            for name, payload in source_files.items(): _atomic_new(dest / name, payload)
            _atomic_new(dest / "plan.complete.json", canonical)
            _atomic_new(dest / "sections.json", (json.dumps(sections, sort_keys=True, indent=2, ensure_ascii=False) + "\n").encode())
            rows = {name: digest(payload) for name, payload in source_files.items()}
            rows.update({"plan.complete.json": digest(canonical), "sections.json": digest((json.dumps(sections, sort_keys=True, indent=2, ensure_ascii=False) + "\n").encode())})
            entry = {"entry_id": entry_id, "provider": Path(binding["root"]).parent.name,
                     "action_id": binding["action_id"], "request_sha256": digest(req_bytes),
                     "root": binding["root"], "state": binding["state"], "review_directory": str(req_path.parent), "variables_source": str(var_path),
                     "state_source": str(state_path), "state_sha256": digest(state_bytes) if state_bytes is not None else None,
                     "source_inventory": source_inventory, "source_sha256": digest(tp._json(source_inventory)),
                     "file_sha256": rows, "plan_sha256": digest(plan_bytes), "section_count": len(sections),
                     "plan_bytes": len(canonical), "lossless": True}
            manifest["entries"].append(entry); published.append(dest)
            staged_output.append(({k: v for k, v in entry.items() if k != "source_inventory"}, sections))
        for req_path, req_bytes, binding, plan_bytes, binary_bytes, var_path, state_path, state_bytes, root_path, source_inventory, canonical, sections in prepared:
            if time.time() >= deadline_epoch:
                raise tp.PlanReviewError("original plan-review deadline elapsed before checkpoint publication")
            _assert_pending_original(req_path, req_bytes)
            if request_inventory is not None:
                expected_cache_links = _assert_request_inventory(
                    owned, request_inventory, mirror_snapshot, expected_cache_links)
            if digest(_regular(req_path.parent / "saved-plan.json")) != binding["json_sha256"] or digest(_regular(req_path.parent / "saved.tfplan")) != binding["plan_sha256"]:
                raise tp.PlanReviewError("saved plan changed before checkpoint publication")
            if (digest(_regular(var_path)) != binding["variables_sha256"]
                    or _source_inventory(root_path, mirror_snapshot) != source_inventory):
                raise tp.PlanReviewError("variables or source changed before checkpoint publication")
            current_state = _regular(state_path) if state_path.exists() or state_path.is_symlink() else None
            if current_state != state_bytes: raise tp.PlanReviewError("state changed before checkpoint publication")
        manifest_bytes = (json.dumps(manifest, sort_keys=True, indent=2) + "\n").encode()
        _atomic_new(checkpoint_root / "manifest.json", manifest_bytes)
        manifest_hash = digest(manifest_bytes)
        dirfd = os.open(checkpoint_root, os.O_RDONLY)
        try: os.fsync(dirfd)
        finally: os.close(dirfd)
        print(f"checkpoint={checkpoint_root} manifest_sha256={manifest_hash}")
        for entry, sections in staged_output:
            print(json.dumps({"entry": entry}, sort_keys=True, indent=2, ensure_ascii=False))
            for section in sections:
                payload = (json.dumps(section["json"], sort_keys=True, indent=2, ensure_ascii=False) + "\n").encode()
                pages = _bounded_pages(section, payload, display_limit)
                reconstructed = "".join(chunk for _, chunk in pages).encode("utf-8")
                if reconstructed != payload or json.loads(reconstructed) != section["json"]:
                    raise tp.PlanReviewError("human-readable pages failed lossless reconstruction")
                for header, chunk in pages:
                    sys.stdout.write(header); sys.stdout.write(chunk); sys.stdout.flush()
        for row in manifest["entries"]:
            source = next(item for item in prepared if item[0].parent == Path(row["review_directory"]))
            _assert_pending_original(source[0], source[1])
            if request_inventory is not None:
                expected_cache_links = _assert_request_inventory(
                    owned, request_inventory, mirror_snapshot, expected_cache_links)
            if time.time() >= deadline_epoch:
                raise tp.PlanReviewError("original plan-review deadline elapsed before receipt command publication")
            print("human_review_required=true deadline_utc_epoch=" + str(deadline_epoch))
            print(shlex.join(["python", "-m", "cloud_network_benchmark.terraform_plan", row["review_directory"],
                              "--request-sha256", row["request_sha256"], "--reviewer", "<human reviewer>",
                              "--inspection", "Completed full payload and comparison; checkpoint " + manifest_hash]))
        return published
    except BaseException:
        # Preserve any partial checkpoint for forensic inspection; never overwrite or erase it.
        raise


def verify_archived(archive_dirs: list[Path], disclosure_root: Path, source_roots: list[Path]) -> dict:
    """Offline historical-plan disclosure; never checks state freshness or creates a receipt."""
    validator = _validator_identity()
    archives = [_safe_root(path) for path in archive_dirs]
    if not archives or len(set(archives)) != len(archives):
        raise tp.PlanReviewError("supply one or more unique explicit archived checkpoint directories")
    current_roots = [_safe_root(path) for path in source_roots]
    if not current_roots:
        raise tp.PlanReviewError("archived verification requires explicit frozen source roots")
    roots_by_identity = {}
    for root in current_roots:
        identity = (root.parent.name, root.name)
        if identity in roots_by_identity:
            raise tp.PlanReviewError("duplicate current source root for provider/root identity")
        roots_by_identity[identity] = root
    prepared = []
    seen_actions = set()
    for directory in archives:
        req_path = directory / "review-request.json"
        plan_path = directory / "saved-plan.json"
        binary_path = directory / "saved.tfplan"
        request_bytes, plan_bytes, binary_bytes = _regular(req_path), _regular(plan_path), _regular(binary_path)
        binding = json.loads(request_bytes)
        if digest(plan_bytes) != binding.get("json_sha256"):
            raise tp.PlanReviewError("archived plan JSON differs from the original request")
        if digest(binary_bytes) != binding.get("plan_sha256"):
            raise tp.PlanReviewError("archived binary plan differs from the original request")
        # Only conventional Terraform variable filenames are eligible. The request's
        # variables_sha256 must still identify exactly one regular, non-symlink file.
        names = ("*.auto.tfvars.json", "*.tfvars.json", "*.tfvars", "vars.json")
        # Some producer checkpoints keep their original vars.json beside the
        # review directory. Inspect only that immediate sibling and still require
        # one exact request-hash match; do not search arbitrary JSON files.
        var_candidates = sorted({p for base in (directory, directory.parent)
                                 for name in names for p in base.glob(name)})
        matches = [path for path in var_candidates if not path.is_symlink()
                   and digest(_regular(path)) == binding.get("variables_sha256")]
        if len(matches) != 1:
            raise tp.PlanReviewError("archived variables must have exactly one request-hash match")
        variables_path = matches[0]
        variables_bytes = _regular(variables_path)
        archived_root = Path(binding["root"])
        provider = archived_root.parent.name
        root_path = roots_by_identity.get((provider, archived_root.name))
        if root_path is None:
            raise tp.PlanReviewError("no explicit frozen source root matches archived provider/root identity")
        plan = json.loads(plan_bytes)
        scope = tp.validate_scope(plan, provider, root_path, json.loads(variables_bytes))
        if binding.get("scope") != scope:
            raise tp.PlanReviewError("archived validated scope differs from the saved review request")
        source_digest = tp.source_hash(root_path)
        source_matches = source_digest == binding.get("source_sha256")
        archived_variables = json.loads(variables_bytes)
        if _sensitive(plan, provider=provider, variables=archived_variables, source_root=root_path,
                      trusted_source=source_matches):
            raise tp.PlanReviewError("plan contains sensitive markers or credential-like material; archived disclosure withheld")
        action_id = str(binding.get("action_id", ""))
        if not action_id or action_id in seen_actions:
            raise tp.PlanReviewError("archived action id is missing or duplicated")
        seen_actions.add(action_id)
        canonical = (json.dumps(plan, sort_keys=True, indent=2, ensure_ascii=False) + "\n").encode()
        sections = _sections(plan)
        reconstructed = {s["section"]: s["json"] for s in sections if not s["section"].startswith("resource_changes/")}
        reconstructed["resource_changes"] = [s["json"] for s in sections if s["section"].startswith("resource_changes/")]
        if json.dumps(reconstructed, sort_keys=True, separators=(",", ":")) != json.dumps(plan, sort_keys=True, separators=(",", ":")):
            raise tp.PlanReviewError("archived sectional disclosure does not reconstruct the full plan")
        prepared.append({"directory": directory, "request_path": req_path, "request_bytes": request_bytes,
                         "plan_path": plan_path, "plan_bytes": plan_bytes, "binary_path": binary_path,
                         "binary_bytes": binary_bytes, "variables_path": variables_path,
                         "variables_bytes": variables_bytes, "root": root_path, "source_sha256": source_digest,
                         "binding": binding, "provider": provider, "canonical": canonical,
                         "sections": sections, "scope": scope})
    destination = _private_destination(disclosure_root)
    entries = []
    try:
        for index, item in enumerate(prepared, 1):
            # Verify all archive inputs remain unchanged immediately before writing disclosures.
            if (_regular(item["request_path"]) != item["request_bytes"]
                    or _regular(item["plan_path"]) != item["plan_bytes"]
                    or _regular(item["binary_path"]) != item["binary_bytes"]
                    or _regular(item["variables_path"]) != item["variables_bytes"]
                    or tp.source_hash(item["root"]) != item["source_sha256"]):
                raise tp.PlanReviewError("archived input changed during verification")
            ident = hashlib.sha256(item["binding"]["action_id"].encode()).hexdigest()[:12]
            entry_dir = destination / f"archive-{index:02d}-{ident}"
            os.mkdir(entry_dir, 0o700)
            sections_bytes = (json.dumps(item["sections"], sort_keys=True, indent=2, ensure_ascii=False) + "\n").encode()
            _atomic_new(entry_dir / "plan.complete.json", item["canonical"])
            _atomic_new(entry_dir / "sections.json", sections_bytes)
            summary = {
                "classification": "archived_stale_non_actionable",
                "active_gate": False,
                "receipt_created": False,
                "dispatch_authorized": False,
                "state_freshness_checked": False,
                "archived_state_currentness": "not_checked",
                "deadline_required_or_inferred": False,
                "action_id": item["binding"]["action_id"],
                "provider": item["provider"],
                "request_sha256": digest(item["request_bytes"]),
                "plan_json_sha256": digest(item["plan_bytes"]),
                "plan_binary_sha256": digest(item["binary_bytes"]),
                "variables_sha256": digest(item["variables_bytes"]),
                "archived_request_source_sha256": item["binding"].get("source_sha256"),
                "current_source_sha256": item["source_sha256"],
                "source_matches_archived_request": item["source_sha256"] == item["binding"].get("source_sha256"),
                "validator": validator,
                "section_count": len(item["sections"]),
                "section_sha256": [section["sha256"] for section in item["sections"]],
                "plan_bytes": len(item["canonical"]),
                "resource_change_count": len(json.loads(item["plan_bytes"]).get("resource_changes", [])),
                "disclosure_sha256": digest(item["canonical"]),
                "sections_file_sha256": digest(sections_bytes),
            }
            summary_bytes = (json.dumps(summary, sort_keys=True, indent=2) + "\n").encode()
            _atomic_new(entry_dir / "summary.json", summary_bytes)
            entries.append({"summary": summary, "summary_sha256": digest(summary_bytes)})
        manifest = {"format": "cnb-archived-plan-verification-v1", "validator": validator,
                    "classification": "archived_stale_non_actionable", "entries": entries}
        manifest_bytes = (json.dumps(manifest, sort_keys=True, indent=2) + "\n").encode()
        _atomic_new(destination / "summary-manifest.json", manifest_bytes)
        fd = os.open(destination, os.O_RDONLY)
        try: os.fsync(fd)
        finally: os.close(fd)
        result = {"classification": manifest["classification"], "entry_count": len(entries),
                  "manifest_sha256": digest(manifest_bytes),
                  "section_count": sum(row["summary"]["section_count"] for row in entries),
                  "resource_change_count": sum(row["summary"]["resource_change_count"] for row in entries)}
        print(json.dumps(result, sort_keys=True))
        return result
    except BaseException:
        # Preserve partial private evidence; never delete or overwrite it.
        raise


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Disclose and checkpoint pending saved-plan requests; never approves or applies")
    parser.add_argument("--owned-root", action="append", type=Path)
    parser.add_argument("--request", type=Path,
                        help="exact original pending request; required for one-request disclosure")
    parser.add_argument("--checkpoint", type=Path)
    parser.add_argument("--deadline-epoch", type=float, help="original live-gate deadline; never extends it")
    parser.add_argument("--archived-dir", action="append", type=Path,
                        help="explicit historical checkpoint directory; never produces a receipt")
    parser.add_argument("--source-root", action="append", type=Path,
                        help="explicit frozen provider Terraform root for archived scope validation")
    parser.add_argument("--archived-disclosure", type=Path,
                        help="new protected output directory for archived-only disclosure")
    parser.add_argument("--display-limit", type=int, default=32768)
    args = parser.parse_args(argv)
    if args.archived_dir:
        if (args.owned_root or args.checkpoint or args.deadline_epoch is not None
                or args.archived_disclosure is None or not args.source_root):
            parser.error("archived mode requires explicit --archived-dir, --source-root, and --archived-disclosure; it takes no deadline")
        verify_archived(args.archived_dir, args.archived_disclosure, args.source_root)
    else:
        if (not args.owned_root or args.checkpoint is None or args.deadline_epoch is None
                or args.archived_disclosure is not None or args.source_root):
            parser.error("live mode requires --owned-root, --checkpoint, and the original --deadline-epoch")
        disclose(args.owned_root, args.checkpoint, args.deadline_epoch, args.display_limit,
                 request_path=args.request)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
