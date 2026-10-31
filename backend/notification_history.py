"""Small in-memory notification preview history for Lyra tools."""
from collections import deque
from threading import Lock
from datetime import datetime
_lock=Lock(); _items=deque(maxlen=50)

def add(app,kind,sender,text):
    with _lock: _items.append({"app":app,"kind":kind,"sender":sender,"text":text,"time":datetime.now().isoformat(timespec="seconds")})

def get_notifications(app=None,sender=None,limit=10):
    with _lock: items=list(_items)
    if app: items=[x for x in items if x["app"]==app.lower()]
    if sender: items=[x for x in items if sender.lower() in x["sender"].lower()]
    return items[-max(1,min(int(limit),50)):][::-1]

def clear():
    with _lock: _items.clear()


def mark_read(app=None,sender=None):
    # Windows toast DB is intentionally read-only. This acknowledges Lyra's
    # stored preview history without pretending to mark the real chat read.
    with _lock:
        items=list(_items); _items.clear()
        for x in items:
            if (app and x['app'] != app.lower()) or (sender and sender.lower() not in x['sender'].lower()):
                _items.append(x)
    return True
