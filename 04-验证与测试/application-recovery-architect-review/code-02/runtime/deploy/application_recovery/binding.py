"""Source-only imports from the one byte-verified backend; never fall back to live modules."""

from __future__ import annotations

import importlib.abc
import importlib.machinery
import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType

from deploy.application_recovery import security as s

_ACTIVE = None
PREFIXES = ("apps", "packages")


def production(name):
    return any(name == prefix or name.startswith(prefix + ".") for prefix in PREFIXES)


class ReviewedLoader(importlib.abc.SourceLoader):
    def __init__(self, path, expected):
        self.path, self.expected = path, expected

    def get_filename(self, fullname):
        return str(self.path)

    def get_data(self, path):
        if Path(path) != self.path:
            raise s.RecoveryError("UNREVIEWED_SOURCE_LOADER_PATH")
        raw = self.path.read_bytes()
        if s.sha(raw) != self.expected:
            raise s.RecoveryError("IMPORTED_BACKEND_BYTES_CHANGED")
        return raw

    def get_code(self, fullname):
        # Compile the verified source directly; never trust a pre-existing .pyc cache.
        return self.source_to_code(self.get_data(str(self.path)), str(self.path))


class FrozenBackend(importlib.abc.MetaPathFinder):
    def __init__(self, backend):
        if (
            backend.get("sha256")
            != "71fe0fb2c9dbde7d85e62d20bef2da8e188e59ac00e9eb056cc021a1bd75274e"
            or backend.get("file_count") != 308
        ):
            raise s.RecoveryError("UNREVIEWED_BACKEND_IDENTITY")
        self.root = Path(backend["source_root"]).resolve(strict=True)
        if s.file_sha(backend["manifest"]) != backend["sha256"]:
            raise s.RecoveryError("BACKEND_MANIFEST_CHANGED")
        inputs = json.loads(Path(backend["manifest"]).read_bytes())
        if len(inputs) != backend["file_count"]:
            raise s.RecoveryError("BACKEND_FILE_COUNT_CHANGED")
        self.files = {}
        for item in inputs:
            relative = Path(item["path"])
            path = self.root / relative
            if (
                relative.is_absolute()
                or ".." in relative.parts
                or not path.resolve().is_relative_to(self.root)
            ):
                raise s.RecoveryError("BACKEND_INPUT_ESCAPES_ROOT")
            if s.file_sha(path) != item["sha256"]:
                raise s.RecoveryError("BACKEND_SOURCE_CHANGED")
            self.files[path] = item["sha256"]

    def verify_module(self, name, module):
        if not production(name) or module is None:
            return
        filename = getattr(module, "__file__", None)
        if filename:
            path = Path(filename)
            if (
                path not in self.files
                or path.resolve() != path
                or s.file_sha(path) != self.files[path]
            ):
                raise s.RecoveryError("PRELOADED_MODULE_OUTSIDE_FIXED_BACKEND")
        else:
            expected = self.root.joinpath(*name.split("."))
            paths = list(getattr(module, "__path__", []))
            if not paths or any(Path(p).resolve() != expected for p in paths):
                raise s.RecoveryError("NAMESPACE_SEARCH_PATH_OUTSIDE_FIXED_BACKEND")

    def find_spec(self, fullname, path=None, target=None):
        if not production(fullname):
            return None
        relative = Path(*fullname.split("."))
        package = self.root / relative / "__init__.py"
        module = self.root / relative.with_suffix(".py")
        chosen = package if package in self.files else module if module in self.files else None
        if chosen is not None:
            loader = ReviewedLoader(chosen, self.files[chosen])
            return importlib.util.spec_from_file_location(
                fullname,
                chosen,
                loader=loader,
                submodule_search_locations=[str(chosen.parent)] if chosen == package else None,
            )
        namespace = self.root / relative
        if any(p.is_relative_to(namespace) for p in self.files):
            spec = importlib.machinery.ModuleSpec(fullname, loader=None, is_package=True)
            spec.submodule_search_locations = [str(namespace)]
            return spec
        # A miss must not continue to the next finder or another PYTHONPATH root.
        raise s.RecoveryError("PRODUCTION_MODULE_NOT_IN_FIXED_MANIFEST")

    def origins(self):
        result = []
        for name, module in sorted(sys.modules.items()):
            if production(name) and module is not None:
                self.verify_module(name, module)
                result.append(
                    {
                        "module": name,
                        "origin": getattr(module, "__file__", None),
                        "namespace_paths": list(getattr(module, "__path__", [])),
                    }
                )
        return result


def install(backend):
    global _ACTIVE
    if _ACTIVE is not None:
        if _ACTIVE.root != Path(backend["source_root"]).resolve():
            raise s.RecoveryError("BACKEND_ROOT_CANNOT_CHANGE_IN_PROCESS")
        _ACTIVE.origins()
        return _ACTIVE
    selected = FrozenBackend(backend)
    # Existing arbitrary modules cannot be made trustworthy by changing sys.path afterwards.
    for name, module in list(sys.modules.items()):
        if production(name):
            selected.verify_module(name, module)
    sys.meta_path.insert(0, selected)
    _ACTIVE = selected
    return selected


def require():
    if _ACTIVE is None:
        raise s.RecoveryError("FIXED_BACKEND_BINDING_REQUIRED")
    _ACTIVE.origins()
    return _ACTIVE


def imported(module: ModuleType):
    selected = require()
    selected.verify_module(module.__name__, module)
    return module
