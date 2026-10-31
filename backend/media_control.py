"""Windows Global System Media Transport Controls (optional verification)."""
from __future__ import annotations
import asyncio

def _get():
    try:
        from winsdk.windows.media.control import GlobalSystemMediaTransportControlsSessionManager as Manager
    except Exception as e:
        return None, e
    async def go():
        mgr=await Manager.request_async(); return mgr.get_current_session()
    try: return asyncio.run(go()), None
    except Exception as e: return None,e

def now_playing():
    session,err=_get()
    if not session: return {"available":False,"error":str(err or "no media session")}
    async def go():
        props=await session.try_get_media_properties_async()
        return {"title":getattr(props,"title","") or "","artist":getattr(props,"artist","") or ""}
    try:
        data=asyncio.run(go())
        info=session.get_playback_info()
        status=str(getattr(info,"playback_status","")).lower()
        return {"available":True,**data,"status":status}
    except Exception as e: return {"available":False,"error":str(e)}

def verify_state(want: str, timeout=2.5):
    import time
    end=time.time()+timeout; last=now_playing()
    while time.time()<end:
        last=now_playing()
        if last.get("available"):
            status=last.get("status","")
            if want=="playing" and "playing" in status: return last
            if want=="paused" and ("paused" in status or "stopped" in status): return last
        time.sleep(.25)
    return last
