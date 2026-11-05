"""Heavy packages that are imported on first use instead of at every launch.

`import openai` alone took ~0.9 s of Lyra's startup (it builds thousands of pydantic models) and holds tens of MB.
The names below behave like the real thing but only import the package when something actually calls it:

  OpenAI(...)        the same constructor as openai.OpenAI
  LazyClient(make)   stands in for a client object; `make()` (and so the import) runs on the first attribute access
"""
from __future__ import annotations

import threading


def OpenAI(*args, **kwargs):                      # noqa: N802 (it is a constructor)
    from openai import OpenAI as _OpenAI
    return _OpenAI(*args, **kwargs)


class LazyClient:
    """A client that does not exist until it is used. `client is None` checks keep working (use None for "no key")."""

    def __init__(self, factory):
        object.__setattr__(self, "_factory", factory)
        object.__setattr__(self, "_client", None)
        object.__setattr__(self, "_lock", threading.Lock())

    def _real(self):
        c = object.__getattribute__(self, "_client")
        if c is None:
            with object.__getattribute__(self, "_lock"):
                c = object.__getattribute__(self, "_client")
                if c is None:
                    c = object.__getattribute__(self, "_factory")()
                    object.__setattr__(self, "_client", c)
        return c

    def __getattr__(self, name):                  # only called for names that are not on LazyClient itself
        return getattr(self._real(), name)
