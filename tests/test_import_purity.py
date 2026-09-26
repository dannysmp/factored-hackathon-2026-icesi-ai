"""
Import Purity Tests
===================

Component: every module under ``app``. Hermetic: spawns a fresh interpreter per check.
Protects the rule "loading a module never reads configuration or opens resources" — the
engineering lesson that makes tests need neither network nor environment.

Two probes run at import time inside the child interpreter:
- an environment probe records every read of the service's own variables, so even a
  non-validating read (a value cached in a module constant) is caught;
- an audit hook records every attempt to open a ``.env`` file placed in the working directory.
"""

from __future__ import annotations

# Standard libraries
import json  # Child interpreter reports its observations as JSON
import os  # Build a controlled child environment
import subprocess  # Fresh interpreter so already-imported modules cannot mask a violation
import sys  # Interpreter path of the running environment
import textwrap  # Readable child-interpreter program
from pathlib import Path  # Repository root and a temp working directory

# Third-party libraries
import pytest  # Test runner and fixtures

_REPO_ROOT = Path(__file__).resolve().parents[1]

# Variables owned by app.config; an import that touches any of them is not pure.
_SERVICE_KEYS = [
    "APP_ENV",
    "LOG_LEVEL",
    "SERVICE_VERSION",
    "LLM_PROVIDER",
    "NLU_MODEL",
    "RENDER_MODEL",
    "ANTHROPIC_API_KEY",
]

# Program run in the child: install the probes, import the module, print what was observed.
_PROBE = textwrap.dedent(
    """
    import importlib, json, os, sys, sysconfig

    keys = set(json.loads(sys.argv[2]))
    owned_roots = tuple(json.loads(sys.argv[3]))
    stdlib_root = os.path.realpath(sysconfig.get_paths()["stdlib"])
    env_reads, env_files = [], []

    def inside(path, root):
        return path == root or path.startswith(root + os.sep)

    def is_transparent(filename):
        # Synthetic code (this probe) and the standard library never own a read themselves.
        if filename.startswith("<"):
            return True
        path = os.path.realpath(filename)
        return inside(path, stdlib_root) and "site-packages" not in path

    def called_from_owned_code():
        # Bulk reads by third-party libraries are legitimate; only the project's own code counts.
        # Transparent frames (for example copy.copy) are skipped: the caller behind them decides.
        frame = sys._getframe(2)
        while frame is not None and is_transparent(frame.f_code.co_filename):
            frame = frame.f_back
        if frame is None:
            return False
        path = os.path.realpath(frame.f_code.co_filename)
        return any(inside(path, root) for root in owned_roots)

    # Non-empty while a probed bulk method runs, so the calls it makes internally are not recounted.
    active = []

    def bulk(name):
        def method(self, *args, **kwargs):
            if active:
                return getattr(super(Probe, self), name)(*args, **kwargs)
            if called_from_owned_code():
                env_reads.append("*" + name)
            active.append(name)
            try:
                return getattr(super(Probe, self), name)(*args, **kwargs)
            finally:
                active.pop()
        return method

    class Probe(dict):
        def __getitem__(self, key):
            if key in keys:
                env_reads.append(key)
            return super().__getitem__(key)

        def get(self, key, default=None):
            if key in keys:
                env_reads.append(key)
            return super().get(key, default)

        def __contains__(self, key):
            if key in keys:
                env_reads.append(key)
            return super().__contains__(key)

        __iter__ = bulk("__iter__")
        keys = bulk("keys")
        values = bulk("values")
        items = bulk("items")
        copy = bulk("copy")
        __copy__ = bulk("copy")

    os.environ = Probe(os.environ)

    def hook(event, args):
        if event == "open":
            name = os.path.basename(str(args[0]))
            if name == ".env" or name.startswith(".env."):
                env_files.append(str(args[0]))

    sys.addaudithook(hook)
    importlib.import_module(sys.argv[1])
    print(json.dumps({"env_reads": sorted(set(env_reads)), "env_files": env_files}))
    """
)


def _import_in_fresh_interpreter(
    module: str, workdir: Path, extra_env: dict[str, str]
) -> dict[str, list[str]]:
    """Import ``module`` in a clean child process and return the probes' observations."""
    env = {
        **{k: v for k, v in os.environ.items() if k in {"PATH", "HOME", "SYSTEMROOT"}},
        "PYTHONPATH": str(_REPO_ROOT),
        **extra_env,
    }
    result = subprocess.run(  # noqa: S603 - fixed argv, no shell, trusted interpreter path
        [
            sys.executable,
            "-c",
            _PROBE,
            module,
            json.dumps(_SERVICE_KEYS),
            # Code under these roots is "ours": the project package and the scratch working dir.
            json.dumps([str((_REPO_ROOT / "app").resolve()), str(workdir.resolve())]),
        ],
        cwd=workdir,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    observed: dict[str, list[str]] = json.loads(result.stdout.strip().splitlines()[-1])
    return observed


@pytest.mark.parametrize("module", ["app", "app.config", "app.main"])
def test_import_succeeds_with_invalid_configuration(module: str, tmp_path: Path) -> None:
    """Importing must not validate settings: invalid env values only fail in load_settings()."""
    observed = _import_in_fresh_interpreter(
        module, tmp_path, {"LOG_LEVEL": "definitely-not-a-level", "NLU_MODEL": "not-a-model"}
    )

    assert observed["env_reads"] == []


@pytest.mark.parametrize("module", ["app", "app.config", "app.main"])
def test_import_never_reads_service_variables_or_env_file(module: str, tmp_path: Path) -> None:
    """With *valid* values and a real .env present, import still reads none of them."""
    (tmp_path / ".env").write_text("SERVICE_VERSION=from-file\n")

    observed = _import_in_fresh_interpreter(
        module, tmp_path, {"LOG_LEVEL": "INFO", "APP_ENV": "local", "ANTHROPIC_API_KEY": "x"}
    )

    assert observed == {"env_reads": [], "env_files": []}


def test_probe_detects_a_non_validating_environment_read(tmp_path: Path) -> None:
    """Revert check: a module that only caches a service variable is caught by the probe."""
    package = tmp_path / "impure"
    package.mkdir()
    (package / "__init__.py").write_text("import os\nCACHED = os.environ.get('LOG_LEVEL')\n")

    observed = _import_in_fresh_interpreter(
        "impure", tmp_path, {"PYTHONPATH": f"{tmp_path}:{_REPO_ROOT}"}
    )

    assert observed["env_reads"] == ["LOG_LEVEL"]


def test_probe_detects_a_dotenv_file_read(tmp_path: Path) -> None:
    """Revert check: a module that opens a dotenv file at import is caught by the audit hook."""
    (tmp_path / ".env").write_text("SERVICE_VERSION=x\n")
    package = tmp_path / "impure_file"
    package.mkdir()
    (package / "__init__.py").write_text("DATA = open('.env').read()  # noqa: SIM115\n")

    observed = _import_in_fresh_interpreter(
        "impure_file", tmp_path, {"PYTHONPATH": f"{tmp_path}:{_REPO_ROOT}"}
    )

    assert observed["env_files"] == [".env"]


@pytest.mark.parametrize(
    ("statement", "expected"),
    [
        ("SNAPSHOT = dict(os.environ)", "*keys"),
        ("NAMES = list(os.environ)", "*__iter__"),
        ("PAIRS = list(os.environ.items())", "*items"),
        ("VALUES = list(os.environ.values())", "*values"),
        ("COPY = os.environ.copy()", "*copy"),
        ("COPY = copy.copy(os.environ)", "*copy"),
    ],
    ids=["dict-copy", "iteration", "items", "values", "method-copy", "copy-module"],
)
def test_probe_detects_bulk_environment_reads(
    tmp_path: Path, statement: str, expected: str
) -> None:
    """Revert check: reading the whole environment at import is caught, whatever the spelling."""
    package = tmp_path / "impure_bulk"
    package.mkdir()
    (package / "__init__.py").write_text(f"import copy\nimport os\n{statement}\n")

    observed = _import_in_fresh_interpreter(
        "impure_bulk", tmp_path, {"PYTHONPATH": f"{tmp_path}:{_REPO_ROOT}"}
    )

    assert observed["env_reads"] == [expected]


@pytest.mark.parametrize("directory", ["vendor", "work_sibling"])
def test_probe_ignores_bulk_reads_from_code_outside_the_owned_roots(
    tmp_path: Path, directory: str
) -> None:
    """A bulk read by third-party code is not the project's read, even beside the work dir.

    The ``work_sibling`` case sits next to ``work`` and shares its name as a prefix, which a
    plain string prefix comparison would wrongly treat as project code.
    """
    workdir = tmp_path / "work"
    workdir.mkdir()
    library = tmp_path / directory / "third_party_lib"
    library.mkdir(parents=True)
    (library / "__init__.py").write_text("import os\nSNAPSHOT = dict(os.environ)\n")

    observed = _import_in_fresh_interpreter(
        "third_party_lib", workdir, {"PYTHONPATH": f"{tmp_path / directory}:{_REPO_ROOT}"}
    )

    assert observed["env_reads"] == []


def test_probe_detects_dotenv_variants(tmp_path: Path) -> None:
    """Revert check: environment-specific dotenv files such as .env.local are covered too."""
    (tmp_path / ".env.local").write_text("SERVICE_VERSION=x\n")
    package = tmp_path / "impure_variant"
    package.mkdir()
    (package / "__init__.py").write_text("DATA = open('.env.local').read()  # noqa: SIM115\n")

    observed = _import_in_fresh_interpreter(
        "impure_variant", tmp_path, {"PYTHONPATH": f"{tmp_path}:{_REPO_ROOT}"}
    )

    assert observed["env_files"] == [".env.local"]
