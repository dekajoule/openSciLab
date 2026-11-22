"""The layers of ARCHITECTURE.md hold (docs/adr/0016-architecture-rules-are-tests.md).

The imports of the source are read with ``ast`` (nothing is imported): which package may import
which, at the top of a module (*module level*) or inside a function (*lazy*, for what is only needed
when it is used). Exceptions are listed below with their reason; an exception that is no longer
needed fails as well, so the lists only shrink.

A failure names the file, the line and the rule. To fix it, move the code to the layer it belongs to
or ask for a facet, a registry or ``ui/shell/services.py`` instead - see ARCHITECTURE.md, *Where a
new thing goes*. Only when the dependency is right after all: add it to an exception list with the
reason, in the same pull request as an ADR when it changes a rule.
"""

from __future__ import annotations

import ast
import pathlib
from dataclasses import dataclass
from typing import Iterator, Optional

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
PACKAGE = ROOT / "openscilab"

#: the layers without user interface: they never import Qt, ``openscilab.ui`` or ``openscilab.app``
HEADLESS = ("core", "driver", "sigrok", "lab", "circuits", "sdl", "api", "cli")

#: what a layer may import at module level (besides itself and the standard and third party
#: libraries); a prefix allows everything below it
MODULE_LEVEL = {
    "core": ("driver.models", "driver.base", "driver.software_trigger", "sigrok", "sdl"),
    "driver": ("core",),
    "sigrok": ("core", "driver"),
    "sdl": ("core",),
    "lab": ("core", "driver", "sigrok", "sdl"),
    "circuits": ("core", "driver"),
    "api": ("core", "driver", "sigrok", "sdl"),
    "cli": ("core", "driver", "sigrok", "sdl", "lab", "circuits", "plugins"),
    "plugins": ("core", "driver", "sigrok", "lab", "circuits"),
}

#: lazy imports across layers of the headless code: (file, imported package) -> why
LAZY_EXCEPTIONS = {
    ("api.py", "lab"): "the API loads the lab when a script uses it (tests/test_startup.py)",
    ("lab/nodes/report.py", "ui"): "Qt lays out a PDF report (ui/report_pdf.py), only when a PDF is written",
    ("driver/process/host.py", "lab"): "a device process opens its instrument as a flow does (lab/engine/devices.py)",
    ("driver/simulated/circuit.py", "circuits"): "a simulator plays the nets of a circuit",
    ("driver/simulated/scenarios.py", "circuits"): "a simulator plays the nets of a circuit",
    ("circuits/source.py", "lab"): "a circuit finds the project it belongs to",
}

#: the drivers of real devices: their code is used by their own folder, their plugin and their user
#: interface in ui/devices/ - everything else asks for facets (docs/adr/0003)
DEVICE_PACKAGES = ("pico", "arduino", "dslogic", "rigoldho", "htool")
DEVICE_USERS = ("driver/", "plugins/", "ui/devices/")
#: (file, device package) -> why the file needs the driver of that device
DEVICE_EXCEPTIONS = {
    ("app.py", "dslogic"): "the smoke test of a packaged build checks that libusb is there",
    ("cli.py", "dslogic"): "the command line explains how to get a missing FPGA bitstream",
    ("ui/dialogs/device_dialogs.py", "pico"): "the network and multi device dialogs of Pico boards",
    ("ui/dialogs/firmware_dialog.py", "pico"): "installs the Pico firmware (UF2)",
    ("ui/documents/hardware.py", "pico"): "*Boards and firmware*: Pico boards in bootloader mode",
    ("ui/documents/device.py", "rigoldho"): "installs the bridge app on a Rigol oscilloscope",
}

#: the main window is built by the application and its own package; documents and plugins get
#: ``ui/shell/services.py`` (docs/adr/0014)
MAIN_WINDOW = "ui.shell.main_window"
MAIN_WINDOW_USERS = ("app.py", "ui/shell/")

#: modules longer than this are hotspots (ARCHITECTURE.md): listed in LARGE_MODULES with the most
#: lines they may have, so they do not grow further; a module that got shorter lowers its limit
LINE_LIMIT = 1500
LARGE_MODULES = {
    "ui/documents/device.py": 2050,
    "ui/dialogs/capture_dialog.py": 1800,
    "driver/pico/analyzer.py": 1750,
    "ui/circuits/document.py": 1750,
    "ui/documents/panel.py": 1600,
    "ui/documents/flow.py": 1600,
}


@dataclass(frozen=True)
class Import:
    file: str  #: relative to openscilab/, with "/"
    line: int
    module: str  #: absolute: "openscilab.core.hub", "PySide6.QtCore", "numpy"
    function: Optional[str]  #: the outermost function it is in; None at module level (also in a class)

    @property
    def lazy(self) -> bool:
        return self.function is not None

    @property
    def layer(self) -> str:
        """The part of openscilab it imports ("core", "ui", "app", ...); "" outside openscilab."""
        parts = self.module.split(".")
        return parts[1] if parts[0] == "openscilab" and len(parts) > 1 else ""

    def __str__(self) -> str:
        where = f" (in {self.function})" if self.function else ""
        return f"openscilab/{self.file}:{self.line} imports {self.module}{where}"


def _source_files(root: pathlib.Path = PACKAGE) -> Iterator[pathlib.Path]:
    for path in sorted(root.rglob("*.py")):
        if "__pycache__" not in path.parts and "vendor" not in path.relative_to(root).parts[:1]:
            yield path


def _layer_of(file: str) -> str:
    return file.split("/", 1)[0].removesuffix(".py")


def _imports(path: pathlib.Path, root: pathlib.Path = PACKAGE) -> list[Import]:
    file = path.relative_to(root).as_posix()
    package = [root.name, *path.relative_to(root).parent.parts]
    found: list[Import] = []

    def visit(node: ast.AST, function: Optional[str]) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.If) and "TYPE_CHECKING" in ast.unparse(child.test):
                continue  # (only for the type checker)
            inner = function
            if function is None and isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                inner = child.name
            if isinstance(child, ast.Import):
                found.extend(Import(file, child.lineno, alias.name, function) for alias in child.names)
            elif isinstance(child, ast.ImportFrom):
                if child.level:
                    base = package[: len(package) - (child.level - 1)]
                    module = ".".join(base + ([child.module] if child.module else []))
                else:
                    module = child.module or ""
                # "from .. import ui" imports openscilab.ui: name the module where it is one
                for alias in child.names:
                    sub = f"{module}.{alias.name}"
                    is_module = (ROOT / pathlib.Path(*sub.split("."))).is_dir() or \
                        (ROOT / pathlib.Path(*sub.split("."))).with_suffix(".py").is_file()
                    found.append(Import(file, child.lineno, sub if is_module else module, function))
            visit(child, inner)

    visit(ast.parse(path.read_text(encoding="utf-8")), None)
    return found


@pytest.fixture(scope="module")
def imports() -> list[Import]:
    return [item for path in _source_files() for item in _imports(path)]


def _report(problems: list[str], rule: str) -> None:
    assert not problems, f"{rule} (tests/test_architecture.py, ARCHITECTURE.md):\n  " + "\n  ".join(problems)


# --------------------------------------------------------------------------------------- the rules
def test_qt_only_in_the_user_interface(imports):
    """docs/adr/0002: PySide6 is imported in ui/, app.py and qt_plugins.py only."""
    allowed = ("ui/", "app.py", "qt_plugins.py")
    _report([str(item) for item in imports
             if item.module.split(".")[0] == "PySide6" and not item.file.startswith(allowed)],
            "Qt outside the user interface")


def test_the_headless_layers_do_not_import_the_user_interface(imports):
    """docs/adr/0002: core, drivers, decoders, flows, circuits, the API and the command line run
    without a display; not even a lazy import reaches ui/ (exceptions: LAZY_EXCEPTIONS)."""
    problems = [str(item) for item in imports
                if _layer_of(item.file) in HEADLESS and item.layer in ("ui", "app")
                and (item.file, item.layer) not in LAZY_EXCEPTIONS]
    _report(problems, "A headless layer imports the user interface")


def test_module_level_imports_follow_the_layers(imports):
    """ARCHITECTURE.md, *Layers and dependency rules*: what each layer may import at module level."""
    problems = []
    for item in imports:
        layer = _layer_of(item.file)
        if item.lazy or not item.layer or item.layer == layer or layer not in MODULE_LEVEL:
            continue
        target = item.module.split(".", 1)[1]
        if not any(target == allowed or target.startswith(allowed + ".") for allowed in MODULE_LEVEL[layer]):
            problems.append(f"{item}: {layer} may import {', '.join(MODULE_LEVEL[layer])} at module level")
    _report(problems, "A module-level import crosses the layers")


def test_lazy_imports_across_the_headless_layers_are_known(imports):
    """A lazy import may cross a layer where the dependency is only needed now and then - but each one
    is a decision, listed in LAZY_EXCEPTIONS. Loading the plugins (``openscilab.plugins``) is allowed."""
    problems = []
    for item in imports:
        layer = _layer_of(item.file)
        if not item.lazy or layer not in MODULE_LEVEL or layer in ("cli", "plugins") or not item.layer:
            continue
        target = item.module.split(".", 1)[1]
        if item.layer == layer or target == "plugins" or (item.file, item.layer) in LAZY_EXCEPTIONS:
            continue
        if any(target == allowed or target.startswith(allowed + ".") for allowed in MODULE_LEVEL[layer]):
            continue
        problems.append(str(item))
    _report(problems, "A lazy import crosses the layers without an entry in LAZY_EXCEPTIONS")


def test_the_lazy_exceptions_are_still_needed(imports):
    used = {(item.file, item.layer) for item in imports if item.lazy}
    _report([f"{file} -> {layer}" for file, layer in LAZY_EXCEPTIONS if (file, layer) not in used],
            "LAZY_EXCEPTIONS lists imports that are gone: remove them")


def test_plugins_import_their_user_interface_in_setup_ui(imports):
    """docs/adr/0004: the command line and device processes load plugins without Qt."""
    problems = [str(item) for item in imports
                if _layer_of(item.file) == "plugins" and item.layer == "ui" and item.function != "setup_ui"]
    _report(problems, "A plugin imports ui/ outside setup_ui()")


def _device_imports(imports) -> Iterator[tuple[Import, str]]:
    for item in imports:
        parts = item.module.split(".")
        if item.layer == "driver" and len(parts) > 2 and parts[2] in DEVICE_PACKAGES \
                and not item.file.startswith(DEVICE_USERS):
            yield item, parts[2]


def test_only_a_device_s_own_code_uses_its_driver(imports):
    """docs/adr/0003: ask for facets and capabilities, never for the kind of device. The drivers of
    real devices are used by their folder, their plugin and ui/devices/ (exceptions: DEVICE_EXCEPTIONS)."""
    problems = [f"{item}: ask for a facet or a capability, or move it to ui/devices/{device}"
                for item, device in _device_imports(imports) if (item.file, device) not in DEVICE_EXCEPTIONS]
    _report(problems, "Code outside a device's own folders uses its driver")


def test_the_device_exceptions_are_still_needed(imports):
    used = {(item.file, device) for item, device in _device_imports(imports)}
    _report([f"{file} -> driver.{device}" for file, device in DEVICE_EXCEPTIONS if (file, device) not in used],
            "DEVICE_EXCEPTIONS lists imports that are gone: remove them")


def test_documents_and_plugins_do_not_import_the_main_window(imports):
    """docs/adr/0014: documents follow ui/documents/base.py, plugins get ui/shell/services.py."""
    problems = [str(item) for item in imports
                if item.module == f"openscilab.{MAIN_WINDOW}" and not item.file.startswith(MAIN_WINDOW_USERS)]
    _report(problems, "The main window is imported outside the shell")


def test_the_device_package_does_not_need_openscilab():
    """openscilab_device runs on any computer with Python: the standard library, numpy only when it
    is there (a lazy import)."""
    root = ROOT / "openscilab_device"
    problems = []
    for path in _source_files(root):
        for item in _imports(path, root):
            top = item.module.split(".")[0]
            if top in ("openscilab", "PySide6") or (top == "numpy" and not item.lazy):
                problems.append(f"openscilab_device/{item.file}:{item.line} imports {item.module}")
    _report(problems, "openscilab_device imports more than the standard library")


def test_no_import_cycles_at_module_level(imports):
    """Modules import each other at module level in one direction only (a package importing its own
    submodules is not a cycle); a cycle makes the import order matter and the layers meaningless."""
    graph: dict[str, set[str]] = {}
    for item in imports:
        if item.lazy or not item.layer:
            continue
        name = "openscilab." + item.file.removesuffix(".py").replace("/", ".").removesuffix(".__init__")
        if item.module != name and not name.startswith(item.module + "."):
            graph.setdefault(name, set()).add(item.module)
    cycles, index, low, stack, on_stack = [], {}, {}, [], set()

    def connect(node: str) -> None:  # Tarjan's strongly connected components
        index[node] = low[node] = len(index)
        stack.append(node)
        on_stack.add(node)
        for other in graph.get(node, ()):
            if other not in index:
                connect(other)
                low[node] = min(low[node], low[other])
            elif other in on_stack:
                low[node] = min(low[node], index[other])
        if low[node] == index[node]:
            component = []
            while True:
                other = stack.pop()
                on_stack.discard(other)
                component.append(other)
                if other == node:
                    break
            if len(component) > 1:
                cycles.append(" -> ".join(sorted(component)))

    import sys

    limit = sys.getrecursionlimit()
    sys.setrecursionlimit(max(limit, 10_000))
    try:
        for node in list(graph):
            if node not in index:
                connect(node)
    finally:
        sys.setrecursionlimit(limit)
    _report(cycles, "Modules import each other at module level")


def test_large_modules_do_not_grow():
    """ARCHITECTURE.md, *Known hotspots*: put new behaviour into a module of its own next to them."""
    problems = []
    for path in _source_files():
        file = path.relative_to(PACKAGE).as_posix()
        lines = len(path.read_text(encoding="utf-8").splitlines())
        limit = LARGE_MODULES.get(file, LINE_LIMIT)
        if lines > limit:
            problems.append(f"openscilab/{file}: {lines} lines, at most {limit}")
        elif file in LARGE_MODULES and lines <= LINE_LIMIT:
            problems.append(f"openscilab/{file}: {lines} lines - no hotspot any more, remove it from LARGE_MODULES")
    _report(problems, "A module grew past its limit (split it; see ARCHITECTURE.md)")


def test_the_rules_see_the_imports():
    """The reader of the imports itself: relative imports, lazy ones and names that are modules."""
    items = _imports(PACKAGE / "plugins" / "pico.py")
    ui = [item for item in items if item.layer == "ui"]
    assert ui and all(item.function == "setup_ui" for item in ui)
    assert any(item.module == "openscilab.driver.kinds" and not item.lazy for item in items)
