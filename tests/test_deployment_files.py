"""Regression checks for the Railway builder failure and equivalent Codex build."""

import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def instructions(path):
    return [
        re.sub(r"\s+", " ", line.strip())
        for line in path.read_text().replace("\\\n", " ").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]


def test_railway_selects_a_dockerfile_without_unsupported_mounts():
    config = tomllib.loads((ROOT / "railway.toml").read_text())
    assert config["build"]["dockerfilePath"] == "Dockerfile"
    for instruction in instructions(ROOT / "Dockerfile"):
        for mount in re.findall(r"--mount=([^\s]+)", instruction):
            assert "type=cache" in mount.split(","), "Railway rejects non-cache mounts"


def test_codex_build_has_identical_steps_except_temporary_ca_mount():
    local = instructions(ROOT / "Dockerfile.codex")
    normalized = []
    trusted_run = (
        "RUN --mount=type=secret,id=build_ca,required=true "
        "PIP_CERT=/run/secrets/build_ca SSL_CERT_FILE=/run/secrets/build_ca "
    )
    for instruction in local:
        if instruction.startswith(trusted_run):
            instruction = "RUN " + instruction[len(trusted_run) :]
        normalized.append(instruction)
    assert normalized == instructions(ROOT / "Dockerfile")
