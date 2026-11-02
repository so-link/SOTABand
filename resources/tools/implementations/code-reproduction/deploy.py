"""Unattended deployment entry point for the academic GPU worker."""

from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import subprocess
import sys
import urllib.request
import venv
from pathlib import Path

try:
    import pwd
except ImportError:  # pragma: no cover - unavailable on Windows deployment checks
    pwd = None


HERE = Path(__file__).resolve().parent
PROJECT_ROOT = HERE.parents[3]
DEFAULT_IMAGE = "sotaband-academic-gpu:1.0"
LTSF_IMAGE = "sotaband-paper-ltsf-linear:1.0"


class DeploymentError(RuntimeError):
    pass


class Runner:
    def __init__(self, dry_run=False):
        self.dry_run = dry_run
        self.actions = []

    def run(self, args, *, timeout=1800, input_bytes=None):
        args = [str(value) for value in args]
        self.actions.append(args)
        if self.dry_run:
            return ""
        completed = subprocess.run(
            args,
            input=input_bytes,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=timeout,
            check=False,
        )
        output = completed.stdout.decode("utf-8", errors="replace")
        if completed.returncode:
            raise DeploymentError(f"Command failed ({completed.returncode}): {' '.join(args)}\n{output}")
        return output.strip()


def probe(args, timeout=60):
    try:
        completed = subprocess.run(
            args,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=timeout,
            check=False,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        return completed.returncode == 0, completed.stdout.strip()
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, str(exc)


def deployment_status(image=DEFAULT_IMAGE, verify_container=False):
    checks = []

    def add(name, ok, detail):
        checks.append({"name": name, "ok": bool(ok), "detail": str(detail)[-2000:]})

    add("python", sys.version_info >= (3, 12), platform.python_version())
    for executable in ("git", "docker", "nvidia-smi"):
        path = shutil.which(executable)
        add(executable, bool(path), path or "not found")
    if shutil.which("docker"):
        ok, output = probe(["docker", "info", "--format", "{{.ServerVersion}}"])
        add("docker_daemon", ok, output)
        ok, output = probe(["docker", "image", "inspect", image, "--format", "{{.Id}}"])
        add("baseline_image", ok, output)
        if verify_container and ok:
            ok, output = probe(
                ["docker", "run", "--rm", "--gpus", "all", image, "nvidia-smi"], timeout=180
            )
            add("gpu_container", ok, output)
    if shutil.which("nvidia-smi"):
        ok, output = probe(
            [
                "nvidia-smi",
                "--query-gpu=name,memory.total,driver_version",
                "--format=csv,noheader",
            ]
        )
        add("nvidia_driver", ok, output)
    return {"ready": all(item["ok"] for item in checks), "checks": checks}


def os_release():
    path = Path("/etc/os-release")
    if not path.is_file():
        return {}
    values = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            values[key] = value.strip().strip('"')
    return values


def install_host_packages(runner):
    if platform.system() != "Linux" or os.geteuid() != 0:
        raise DeploymentError("Host package installation requires root on Debian or Ubuntu Linux")
    distro = os_release().get("ID", "")
    if distro not in {"debian", "ubuntu"}:
        raise DeploymentError(f"Unsupported distribution for automatic host packages: {distro}")

    runner.run(["apt-get", "update"])
    runner.run(
        ["apt-get", "install", "-y", "docker.io", "curl", "ca-certificates", "gnupg"]
    )
    runner.run(["systemctl", "enable", "--now", "docker"])
    if not runner.dry_run and not shutil.which("nvidia-smi"):
        raise DeploymentError(
            "NVIDIA driver is absent. Install the vendor/cloud driver and reboot, then rerun deployment"
        )
    if runner.dry_run:
        runner.actions.append(["download", "NVIDIA Container Toolkit signing key and apt source"])
    else:
        key = urllib.request.urlopen(
            "https://nvidia.github.io/libnvidia-container/gpgkey", timeout=60
        ).read()
        runner.run(
            [
                "gpg",
                "--dearmor",
                "--yes",
                "--output",
                "/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg",
            ],
            input_bytes=key,
        )
        source = urllib.request.urlopen(
            "https://nvidia.github.io/libnvidia-container/stable/deb/nvidia-container-toolkit.list",
            timeout=60,
        ).read().decode("utf-8")
        source = source.replace(
            "deb https://",
            "deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://",
        )
        Path("/etc/apt/sources.list.d/nvidia-container-toolkit.list").write_text(
            source, encoding="utf-8"
        )
    runner.run(["apt-get", "update"])
    runner.run(["apt-get", "install", "-y", "nvidia-container-toolkit"])
    runner.run(["nvidia-ctk", "runtime", "configure", "--runtime=docker"])
    runner.run(["systemctl", "restart", "docker"])


def create_environment(project_root, environment, runner):
    requirements = HERE / "requirements.txt"
    if not requirements.is_file() or not (project_root / "resources/tools/_academic").is_dir():
        raise DeploymentError("Project root or academic-tool requirements are missing")
    if runner.dry_run:
        runner.actions.append([sys.executable, "-m", "venv", str(environment)])
    elif not environment.exists():
        venv.EnvBuilder(with_pip=True, system_site_packages=True).create(environment)
    python = environment / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    runner.run([python, "-m", "pip", "install", "--upgrade", "pip"])
    runner.run([python, "-m", "pip", "install", "-r", requirements])
    return python


def build_images(runner, baseline_image, with_ltsf_linear):
    runner.run(["docker", "build", "-t", baseline_image, HERE])
    if with_ltsf_linear:
        runner.run(
            [
                "docker",
                "build",
                "--build-arg",
                f"BASE_IMAGE={baseline_image}",
                "-f",
                HERE / "images/ltsf-linear.Dockerfile",
                "-t",
                LTSF_IMAGE,
                HERE,
            ]
        )


def systemd_unit(project_root, python, service_user, data_root, user_id):
    def quoted(value):
        value = str(value)
        if "\n" in value or "\r" in value:
            raise DeploymentError("systemd values must not contain line breaks")
        return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'

    return "\n".join(
        [
            "[Unit]",
            "Description=SOTABand academic GPU worker",
            "After=docker.service network-online.target",
            "Requires=docker.service",
            "",
            "[Service]",
            "Type=simple",
            f"User={service_user}",
            "SupplementaryGroups=docker",
            f"WorkingDirectory={quoted(project_root)}",
            f"Environment={quoted('SOTABAND_USER_ID=' + user_id)}",
            f"Environment={quoted('SOTABAND_ACADEMIC_DATA=' + str(data_root))}",
            "Environment=PYTHONUNBUFFERED=1",
            f"ExecStart={quoted(python)} -m resources.tools._academic.worker",
            "Restart=on-failure",
            "RestartSec=5",
            "TimeoutStopSec=90",
            "",
            "[Install]",
            "WantedBy=multi-user.target",
            "",
        ]
    )


def install_service(project_root, python, service_user, data_root, user_id, runner):
    if platform.system() != "Linux" or pwd is None or os.geteuid() != 0:
        raise DeploymentError("systemd installation requires root on Linux")
    account = pwd.getpwnam(service_user)
    data_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chown(data_root, account.pw_uid, account.pw_gid)
    unit = systemd_unit(project_root, python, service_user, data_root, user_id)
    path = Path("/etc/systemd/system/sotaband-academic-worker.service")
    if runner.dry_run:
        runner.actions.append(["write", str(path)])
    else:
        path.write_text(unit, encoding="utf-8")
    runner.run(["systemctl", "daemon-reload"])
    runner.run(["systemctl", "enable", "--now", path.stem])


def parser():
    root = argparse.ArgumentParser(description=__doc__)
    commands = root.add_subparsers(dest="action", required=True)
    check = commands.add_parser("check")
    check.add_argument("--image", default=DEFAULT_IMAGE)
    check.add_argument("--verify-container", action="store_true")

    install = commands.add_parser("install")
    install.add_argument("--project-root", type=Path, default=PROJECT_ROOT)
    install.add_argument("--environment", type=Path, default=HERE / ".venv")
    install.add_argument("--data-root", type=Path, default=Path("/var/lib/sotaband/academic"))
    install.add_argument("--service-user", default=os.environ.get("SUDO_USER") or "sotaband")
    install.add_argument("--user-id", default="server")
    install.add_argument("--image", default=DEFAULT_IMAGE)
    install.add_argument("--install-host-packages", action="store_true")
    install.add_argument("--with-ltsf-linear", action="store_true")
    install.add_argument("--systemd", action="store_true")
    install.add_argument("--skip-venv", action="store_true")
    install.add_argument("--skip-images", action="store_true")
    install.add_argument("--skip-gpu-check", action="store_true")
    install.add_argument("--dry-run", action="store_true")
    return root


def main(argv=None):
    args = parser().parse_args(argv)
    if args.action == "check":
        status = deployment_status(args.image, args.verify_container)
        print(json.dumps(status, ensure_ascii=False, indent=2))
        return 0 if status["ready"] else 1

    project_root = args.project_root.resolve()
    environment = args.environment.resolve()
    data_root = args.data_root.resolve()
    runner = Runner(args.dry_run)
    try:
        if args.install_host_packages:
            install_host_packages(runner)
        python = environment / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        if not args.skip_venv:
            python = create_environment(project_root, environment, runner)
        if not args.skip_images:
            build_images(runner, args.image, args.with_ltsf_linear)
        if not args.skip_gpu_check:
            runner.run(
                ["docker", "run", "--rm", "--gpus", "all", args.image, "nvidia-smi"],
                timeout=180,
            )
        if args.systemd:
            install_service(
                project_root, python, args.service_user, data_root, args.user_id, runner
            )
        print(
            json.dumps(
                {
                    "status": "dry_run" if args.dry_run else "installed",
                    "project_root": str(project_root),
                    "environment": str(environment),
                    "data_root": str(data_root),
                    "baseline_image": args.image,
                    "paper_image": LTSF_IMAGE if args.with_ltsf_linear else None,
                    "systemd": args.systemd,
                    "actions": runner.actions,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    except (DeploymentError, OSError, KeyError) as exc:
        print(json.dumps({"status": "failed", "error": str(exc)}, ensure_ascii=False, indent=2))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
