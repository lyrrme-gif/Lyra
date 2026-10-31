# Lyra plugins

A plugin is one `.py` file. Drop it in a plugins folder, say **"reload plugins"**, and Lyra answers it immediately. No restart.

## Where to put it
- `%APPDATA%\Lyra\plugins` — your own plugins (the folder is created the first time Lyra starts; Settings → Features → Plugins shows the exact path).
- `backend\plugins` — the plugins shipped with Lyra (`whatsapp.py`).
  When you run Lyra from source both are the same folder.

If two plugins have the same `name`, the one in your own folder wins.

## The file
```python
def handler(command):
    # `command` is what the user said or typed. Return the reply to speak/show,
    # or None if this isn't really for you (Lyra then carries on as usual).
    return "Hello from my plugin!"

PLUGIN = {
    "name": "Hello",                       # unique, shown in Settings
    "description": "Says hello.",          # shown in Settings; also tells Lyra's agent when to use it
    "patterns": [r"^\s*say hello\s*$"],    # regexes, case-insensitive, matched with re.search
    "handler": handler,
    "permission": "SAFE",                  # "SAFE": runs at once. "CONFIRM": Lyra asks "are you sure?" first
}
```
`permission` is optional; if you leave it out it is `"CONFIRM"`. Use `"CONFIRM"` for anything that changes files, sends something, or spends money.

## Good to know
- Patterns are checked before Lyra's other commands, so anchor them (`^…$`) or make them specific, otherwise your plugin may catch phrases meant for something else.
- A plugin that raises an error, has a bad regex, or takes longer than 15 seconds is skipped. Lyra keeps working, and the problem is written to the log and listed in Settings → Features → Plugins.
- Every enabled plugin is also available to Lyra's multi-step agent as a tool called `plugin_<name>`.
- Switch single plugins off in Settings → Features → Plugins, or the whole thing off with the **Plugins** switch there.
- Plugins are normal Python with full access to your PC. Only add files you trust.


## App plugins (apps on the notch)
A plugin can also be an **app**: something that lives on the notch, like Spotify's playlists or WhatsApp chats. It adds an `app` part to `PLUGIN` (with or without voice commands). It never draws its own screens: it returns plain data and Lyra shows it in the notch's own style.

```python
PLUGIN = {
    "name": "Example",
    "description": "What it does.",
    "app": {
        "id": "example",            # short slug
        "title": "Example",
        "icon": "chat",
        "status":     lambda: {"connected": True, "account": "me"},
        "connect":    lambda: {"url": "https://..."},      # optional: opens a sign-in page
        "disconnect": lambda: None,                        # optional
        "view":       lambda route, params: {"type": "list", "title": "Home", "items": [{"id": "1", "title": "One"}]},
        "action":     lambda name, params: {"ok": True, "message": "Done"},   # optional
        "poll":       lambda: [{"id": "m1", "sender": "Sara", "text": "hi"}], # optional: new items become notch toasts
        "poll_seconds": 30,
    },
}
```
Only `id`, `title` and `view` are required. Views are `list`, `thread` (messages and a reply box) or `message`; see `plugins/apps.py` for the exact fields. Lyra cleans every view (known fields only, short text, at most 100 items, https images only).
Save tokens with `apps.store("example")` (a small JSON file in `%APPDATA%\Lyra\plugin_data`). The first answer of `poll` after start-up is treated as backlog and is not shown. Routes: `GET /apps`, `POST /apps/view`, `/apps/action`, `/apps/connect`, `/apps/disconnect`.

Chat apps can also show **photos and voice messages**: a thread message may carry `media` (`kind` voice/audio/image, `state`, `url`, `seconds`, `transcript`, ...), and the app serves the file through an optional `"media": lambda name: path_or_None`; the notch fetches it from `GET /apps/media?id=<app>&f=<name>`. A `poll` item may carry `"chat"` so a click on its toast opens that conversation. A thread's `compose` may carry `draft` (text for the reply box) and `suggest` (a button whose action answers `{"ok": True, "suggestions": [...]}`). Suggestions only fill the reply box; nothing is sent until the user presses Send.
