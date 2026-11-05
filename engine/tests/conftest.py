"""Test-only support: packages resolve their submodules through sys.modules, like the old bare `import x` did.

Before the reorganisation every module was imported by bare name (`import config`), so a test that put a stub into
`sys.modules["config"]`, or popped a module to import it again with fresh stubs, got exactly that. With package imports
(`from engine.core import config`) Python looks at the package attribute first, which still points at the module that
was imported earlier. This file makes the engine packages ask sys.modules first, so the tests keep their meaning
without touching the production code. It only affects test runs (pytest loads this file); the app never imports it.
"""
import importlib.abc
import importlib.util
import sys
import types


class _SysModulesFirst(types.ModuleType):
    def __getattribute__(self, name):
        val = super().__getattribute__(name)
        if isinstance(val, types.ModuleType):
            full = super().__getattribute__("__name__") + "." + name
            cur = sys.modules.get(full)
            if cur is not val:
                if cur is not None:
                    return cur                      # a stub (or a fresh import) replaced it
                if getattr(val, "__name__", None) == full:
                    raise AttributeError(name)      # popped from sys.modules: the next import must load it again
        return val


class _Loader(importlib.abc.Loader):
    def __init__(self, inner):
        self._inner = inner

    def create_module(self, spec):
        return self._inner.create_module(spec)

    def exec_module(self, module):
        module.__class__ = _SysModulesFirst
        self._inner.exec_module(module)

    def __getattr__(self, name):
        return getattr(self._inner, name)


class _Finder(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path, target=None):
        if not fullname.startswith("engine."):
            return None
        for finder in sys.meta_path:
            if finder is self or not hasattr(finder, "find_spec"):
                continue
            spec = finder.find_spec(fullname, path, target)
            if spec is None:
                continue
            if spec.loader is not None and spec.submodule_search_locations is not None:    # a regular package
                spec.loader = _Loader(spec.loader)
            return spec
        return None


if not any(isinstance(f, _Finder) for f in sys.meta_path):
    sys.meta_path.insert(0, _Finder())

# Packages that are already imported, or have no __init__.py (a namespace package cannot go through the loader above).
for _name in ("engine", "engine.tools", "engine.core", "engine.voice", "engine.brain", "engine.features", "engine.bridge"):
    try:
        _mod = importlib.import_module(_name)
    except Exception:           # not created yet in an intermediate step
        continue
    if type(_mod) is types.ModuleType:
        _mod.__class__ = _SysModulesFirst
