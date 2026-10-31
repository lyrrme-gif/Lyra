"""Built-in Agent tools backed by Lyra's existing action layer."""
from tools.base import ToolDefinition, ToolParameter, ToolCategory, ToolResult
from tools.registry import register_tool

def _call(fn):
    try:
        result = fn()
        if isinstance(result, ToolResult): return result
        if isinstance(result, tuple) and len(result)==2: return ToolResult(bool(result[0]), str(result[1]))
        return ToolResult(True, str(result))
    except Exception as e:
        return ToolResult(False, f"{type(e).__name__}: {e}")

def _volume(direction, steps=5):
    from actions.system import adjust_volume
    return _call(lambda: adjust_volume(direction, int(steps)))

def _set_volume(level):
    from actions.system import set_volume_level
    try:
        ok,msg=set_volume_level(int(level)); return ToolResult(bool(ok), str(msg), verified=bool(ok))
    except Exception as e: return ToolResult(False,str(e))

def _mute():
    from actions.system import _send_media_key, VK_VOLUME_MUTE
    _send_media_key(VK_VOLUME_MUTE); return ToolResult(True,"Mute key sent",verified=None)

def _media(key, action):
    from actions.system import _send_media_key
    import session_context, time
    _send_media_key(key)
    session_context.get().note_media(action, window_title=session_context.current_window_title())
    try:
        import media_control
        want="playing" if action=="play" else "paused" if action=="pause" else ""
        state=media_control.verify_state(want) if want else media_control.now_playing()
        if state.get("available"):
            label=" - ".join(x for x in (state.get("title"),state.get("artist")) if x) or "current media"
            return ToolResult(True,f"{action.title()} verified: {label}.",data=state,verified=True)
    except Exception:
        pass
    return ToolResult(True,f"Media {action} key sent, but playback could not be verified.",verified=False)

def _shortcut(name):
    import shortcuts
    want=(name or '').strip().lower()
    for item in shortcuts._manager.list_shortcuts():
        if want in (item['id'].lower(),item['name'].lower()) or want in [t.lower() for t in item['triggers']]:
            sc=shortcuts._manager.get_shortcut(item['id'])
            ok,msg=shortcuts._executor.execute_shortcut(item['name'],sc.get('actions',[]),None)
            return ToolResult(ok,msg,verified=ok)
    return ToolResult(False,f"No shortcut named '{name}'")

def _wait(seconds):
    import time; time.sleep(max(0,min(float(seconds),10))); return ToolResult(True,f"Waited {seconds}s",verified=True)

register_tool(ToolDefinition('run_shortcut','Run Shortcut','Run a saved Lyra shortcut.',ToolCategory.APP,[ToolParameter('name','string','Shortcut name')],'SAFE',_shortcut))
register_tool(ToolDefinition('volume_up','Volume Up','Increase system volume.',ToolCategory.SYSTEM,[ToolParameter('steps','number','Volume steps',False,5)],'SAFE',lambda steps=5:_volume('up',steps)))
register_tool(ToolDefinition('volume_down','Volume Down','Decrease system volume.',ToolCategory.SYSTEM,[ToolParameter('steps','number','Volume steps',False,5)],'SAFE',lambda steps=5:_volume('down',steps)))
register_tool(ToolDefinition('set_volume','Set Volume','Set system volume from 0 to 100.',ToolCategory.SYSTEM,[ToolParameter('level','number','Volume level 0-100')],'SAFE',_set_volume))
register_tool(ToolDefinition('mute','Mute','Toggle system mute.',ToolCategory.SYSTEM,[],'SAFE',_mute))
register_tool(ToolDefinition('media_play','Play Media','Toggle/play system media.',ToolCategory.MEDIA,[],'SAFE',lambda:_media(0xB3,'play')))
register_tool(ToolDefinition('media_pause','Pause Media','Toggle/pause system media.',ToolCategory.MEDIA,[],'SAFE',lambda:_media(0xB3,'pause')))
register_tool(ToolDefinition('media_next','Next Media','Skip to next system media track.',ToolCategory.MEDIA,[],'SAFE',lambda:_media(0xB0,'next')))
register_tool(ToolDefinition('media_previous','Previous Media','Go to previous system media track.',ToolCategory.MEDIA,[],'SAFE',lambda:_media(0xB1,'previous')))
register_tool(ToolDefinition('wait','Wait','Wait briefly between actions.',ToolCategory.SYSTEM,[ToolParameter('seconds','number','Seconds',False,1)],'SAFE',_wait))


def _notifications(app="", sender="", limit=10):
    import notification_history
    rows=notification_history.get_notifications(app or None,sender or None,limit)
    stored=False
    if not rows:
        # Lyra only remembers toasts that arrived while she was running; also look at what
        # Windows itself has stored (Discord / WhatsApp / Telegram / reminders).
        try:
            import notifications
            rows=notifications.recent(50)
        except Exception:
            rows=[]
        if app: rows=[x for x in rows if x["app"]==app.lower()]
        if sender: rows=[x for x in rows if sender.lower() in x["sender"].lower()]
        rows=rows[:max(1,min(int(limit),50))]
        stored=True
    if not rows:
        return ToolResult(True,"I don't see any recent Discord, WhatsApp, Telegram or reminder notifications.",data=rows,verified=True)
    head=f"{len(rows)} recent notification{'s' if len(rows)!=1 else ''}"+(" (from Windows' stored history)" if stored else "")+": "
    return ToolResult(True,head+"; ".join(f"{x['app']} from {x['sender']}: {x['text']}" for x in rows),data=rows,verified=True)

def _status():
    from actions import get_system_status
    msg=get_system_status(); return ToolResult(True,msg,data=msg,verified=True)

register_tool(ToolDefinition('get_notifications','Get Notifications','Read stored Windows notification previews only.',ToolCategory.SYSTEM,[ToolParameter('app','string','Optional app filter',False,''),ToolParameter('sender','string','Optional sender filter',False,''),ToolParameter('limit','number','Maximum previews',False,10)],'SAFE',_notifications))
register_tool(ToolDefinition('system_status','System Status','Get local CPU/RAM/system status.',ToolCategory.SYSTEM,[],'SAFE',_status))


def _now_playing():
    try:
        import media_control
        d=media_control.now_playing()
        if not d.get('available'): return ToolResult(False,"I couldn't read the current media session.",data=d,verified=False)
        label=" - ".join(x for x in (d.get('title'),d.get('artist')) if x) or "Unknown track"
        return ToolResult(True,f"Currently {label} ({d.get('status','unknown')}).",data=d,verified=True)
    except Exception as e: return ToolResult(False,f"Media session unavailable: {e}",verified=False)
register_tool(ToolDefinition('media_now_playing','Now Playing','Read the current Windows media session.',ToolCategory.MEDIA,[],'SAFE',_now_playing))


def _mark_read(app="", sender=""):
    import notification_history
    notification_history.mark_read(app or None,sender or None)
    return ToolResult(True,"Acknowledged Lyra's stored notification previews. This does not mark the underlying chat read.",verified=True)
register_tool(ToolDefinition('mark_read','Acknowledge Notifications','Remove matching previews from Lyra history; does not mark the underlying chat read.',ToolCategory.SYSTEM,[ToolParameter('app','string','Optional app filter',False,''),ToolParameter('sender','string','Optional sender filter',False,'')],'SAFE',_mark_read))


def _list_windows():
    from actions.windows_mgr import list_windows
    rows=list_windows()
    return ToolResult(True, "; ".join(rows) if rows else "No windows found.", data=rows, verified=True)

def _clipboard():
    from actions.system import get_clipboard_text
    v=get_clipboard_text(); return ToolResult(True, v or "Clipboard is empty.", data=v, verified=True)

def _running_apps():
    import psutil
    names=sorted({p.info.get('name') for p in psutil.process_iter(['name']) if p.info.get('name')})
    return ToolResult(True, ", ".join(names[:80]), data=names, verified=True)


def _close():
    from actions.windows_mgr import close_active
    return ToolResult(True, str(close_active()), verified=False)

def _type(text):
    from actions.apps import type_text
    return ToolResult(True, str(type_text(text)), verified=False)

def _file_list(path):
    from actions.files import list_directory
    msg=list_directory(path); return ToolResult(True,msg,verified=True)

def _file_delete(path):
    from actions.files import delete_path
    ok,msg=delete_path(path); return ToolResult(bool(ok),str(msg),verified=bool(ok))

def _create_item(kind, name="", location="", content="", file_type=""):
    from actions import file_ops
    ok,msg=file_ops.create_item(str(kind or "file").lower(), name or "", location or "", content or "", file_type or "")
    return ToolResult(bool(ok),msg,verified=bool(ok))

def _find_file(query, kind="any"):
    from actions import file_ops
    rows=file_ops.search_names(str(query), kind if kind in ("file","folder") else "any")
    if not rows: return ToolResult(True,f"No file or folder named like {query} was found.",data=[],verified=True)
    file_ops._remember(rows)
    lines="; ".join(f"{r['name']} ({r['path']})" for r in rows)
    return ToolResult(True,f"{len(rows)} match{'es' if len(rows)!=1 else ''}: {lines}",data=rows,verified=True)

def _open_file(path_or_name):
    from actions import file_ops
    import os
    target=str(path_or_name or "")
    if not os.path.exists(target):
        rows=file_ops.search_names(target,"any",limit=2)
        if not rows: return ToolResult(False,f"I couldn't find {target}.",verified=False)
        if len(rows)>1 and rows[0]["score"]==rows[1]["score"]:
            return ToolResult(False,"Several match equally: "+"; ".join(r["path"] for r in rows)+". Use the full path.",verified=False)
        target=rows[0]["path"]
    ok,how=file_ops.open_path(target)
    msg=(f"Showed {target} in Explorer (programs and scripts are never run)." if how=="shown" else f"Opened {target}.") if ok else f"Couldn't open {target}: {how}"
    return ToolResult(bool(ok),msg,verified=bool(ok))

def _app_volume(app, level):
    import app_volume
    try:
        name, v = app_volume.set_app_by_name(str(app), float(level) / 100.0)
        return ToolResult(True, f"{name} volume set to {v * 100:.0f}%.", verified=True)
    except Exception as e:
        return ToolResult(False, str(e))

def _mute_app(app, muted=None):
    import app_volume
    try:
        m = None if muted is None else bool(muted)
        name, is_muted = app_volume.mute_app_by_name(str(app), m)
        return ToolResult(True, f"{name} {'muted' if is_muted else 'unmuted'}.", verified=True)
    except Exception as e:
        return ToolResult(False, str(e))

def _list_app_volumes():
    import app_volume
    try:
        apps = app_volume.list_apps()
        if not apps:
            return ToolResult(True, "No apps currently have an audio session.", data={"apps": []}, verified=True)
        lines = [
            f"{a['name']}: {int(round(a['level'] * 100))}%{' (muted)' if a['muted'] else ''}{' · playing' if a.get('active') else ''}"
            for a in apps
        ]
        return ToolResult(True, "App volumes:\n" + "\n".join(lines), data={"apps": apps}, verified=True)
    except Exception as e:
        return ToolResult(False, str(e))

register_tool(ToolDefinition('list_windows','List Windows','List visible top-level windows.',ToolCategory.SYSTEM,[],'SAFE',_list_windows))
register_tool(ToolDefinition('clipboard_text','Clipboard','Read clipboard text.',ToolCategory.SYSTEM,[],'SAFE',_clipboard))
register_tool(ToolDefinition('running_apps','Running Apps','List running process names.',ToolCategory.SYSTEM,[],'SAFE',_running_apps))
register_tool(ToolDefinition('close_window','Close Window','Close the active window.',ToolCategory.SYSTEM,[],'CONFIRM',_close))
register_tool(ToolDefinition('type_text','Type Text','Type text into the focused window.',ToolCategory.SYSTEM,[ToolParameter('text','string','Text to type')],'CONFIRM',_type))
register_tool(ToolDefinition('list_directory','List Directory','List files in a directory.',ToolCategory.FILE,[ToolParameter('path','string','Directory path')],'SAFE',_file_list))
register_tool(ToolDefinition('delete_path','Delete Path','Delete a file or folder.',ToolCategory.FILE,[ToolParameter('path','string','Path to delete')],'CONFIRM',_file_delete))
register_tool(ToolDefinition('create_item','Create File Or Folder','Create a folder or file (txt, bat, py, md, json, html, css, js, ps1, csv, docx, pptx). With no location it goes on the Desktop; with a location (Documents, Downloads, D:\\path, a remembered folder) it goes there.',ToolCategory.FILE,[ToolParameter('kind','string','folder or file'),ToolParameter('name','string','Name, e.g. backup or backup.bat (empty = New Folder / New File)',False,''),ToolParameter('location','string','Where, e.g. documents or D:\\projects. Empty = Desktop',False,''),ToolParameter('content','string','Text to put in the file',False,''),ToolParameter('file_type','string','bat, python, text, markdown, json, html, css, javascript, powershell, csv, word, powerpoint',False,'')],'SAFE',_create_item))
register_tool(ToolDefinition('find_file','Find File By Name','Find files or folders by (part of) their name in the usual folders, then the user profile and drives.',ToolCategory.FILE,[ToolParameter('query','string','Name or part of it, e.g. budget or budget.xlsx'),ToolParameter('kind','string','file, folder or any',False,'any')],'SAFE',_find_file))
register_tool(ToolDefinition('open_file','Open File Or Folder','Open a file or folder by path or by name with its default app. Programs and scripts are only shown in Explorer, never run.',ToolCategory.FILE,[ToolParameter('path_or_name','string','Full path or a name')],'SAFE',_open_file))
register_tool(ToolDefinition('set_app_volume','App Volume','Set a running app audio session volume (0-100). Per-app, not master.',ToolCategory.SYSTEM,[ToolParameter('app','string','Application name'),ToolParameter('level','number','0-100')],'SAFE',_app_volume))
register_tool(ToolDefinition('mute_app','Mute App','Mute or unmute a running app audio session.',ToolCategory.SYSTEM,[ToolParameter('app','string','Application name'),ToolParameter('muted','boolean','True to mute, false to unmute; omit to toggle',False)],'SAFE',_mute_app))

def _recognize_music(force_audio=False):
    import music_recognition
    try:
        r = music_recognition.recognize(force_audio=bool(force_audio))
        if not r.ok:
            return ToolResult(False, r.message or "Couldn't identify the song.")
        parts = [r.title, r.artist]
        if r.album:
            parts.append(r.album)
        msg = " — ".join(p for p in parts if p)
        return ToolResult(True, msg, data=r.to_dict(), verified=True)
    except Exception as e:
        return ToolResult(False, str(e))


def _capture_screen(mode="full", monitor=0):
    import screen_capture
    try:
        mode = str(mode or "full")
        result = screen_capture.capture(mode=mode, monitor=int(monitor or 0), keep_context=True)
        if not result.get("ok"):
            return ToolResult(False, result.get("message") or "Capture failed")
        path = result.get("path") or ""
        try:
            layout = screen_capture.describe_layout()
        except Exception:
            layout = ""
        return ToolResult(
            True,
            f"Screen captured ({result.get('width')}x{result.get('height')})" + (f": {path}" if path else ".") + (f" {layout} Use mode=monitor with monitor=0.. for one screen." if layout else ""),
            data=result,
            verified=True,
        )
    except Exception as e:
        return ToolResult(False, str(e))

def _last_screen_context():
    import screen_capture
    ctx = screen_capture.last_context()
    if not ctx:
        return ToolResult(False, "No screen context captured yet.")
    path = ctx.get("path") or ""
    return ToolResult(
        True,
        f"Last capture: {ctx.get('mode')} {ctx.get('width')}x{ctx.get('height')}" + (f" at {path}" if path else ""),
        data={k: v for k, v in ctx.items() if k != "png_bytes"},
        verified=True,
    )

register_tool(ToolDefinition('capture_screen','Capture Screen','Capture the screen for Agent/vision context (full or monitor).',ToolCategory.SYSTEM,[ToolParameter('mode','string','full or monitor',False,'full'),ToolParameter('monitor','number','Monitor index when mode=monitor',False,0)],'SAFE',_capture_screen))
register_tool(ToolDefinition('last_screen_context','Last Screen Context','Return metadata for the most recent screen capture.',ToolCategory.SYSTEM,[],'SAFE',_last_screen_context))
register_tool(ToolDefinition('recognize_music','Recognize Music','Identify the song currently playing on the PC (system audio / media session).',ToolCategory.MEDIA,[ToolParameter('force_audio','boolean','Force audio fingerprint even if media session has metadata',False)],'SAFE',_recognize_music))


def _web_search(query, limit=5):
    import search
    rows = search.web_search_results(str(query or "").strip(), max_results=max(1, min(int(limit or 5), 8)))
    if not rows:
        return ToolResult(False, "No real web search results were available.", data=[])
    try:
        import ui_bridge
        ui_bridge.push_search_results(str(query or ""), rows, rows[0].get("provider", ""))
    except Exception:
        pass
    # Keep the full result set in the island, but give the agent a short,
    # speakable observation. Returning numbered titles and URLs here made the
    # caption show raw search markup, while voice._speakable stripped it all
    # and spoke its empty-response fallback.
    first = rows[0]
    title = str(first.get("title") or "").strip()
    description = str(first.get("description") or "").strip()
    summary = f"I found {title}. {description}" if description else f"I found {title}."
    return ToolResult(True, summary, data={"results": rows}, verified=True)

register_tool(ToolDefinition('web_search','Web Search','Search the live web using configured real search providers. Never invents results.',ToolCategory.WEB,[ToolParameter('query','string','Search query'),ToolParameter('limit','number','Maximum results',False,5)],'SAFE',_web_search))

register_tool(ToolDefinition('list_app_volumes','List App Volumes','List apps with audio sessions and their volume levels.',ToolCategory.SYSTEM,[],'SAFE',_list_app_volumes))
