"""Lyra - call notetaker (Phase 7).

Records the microphone ("You") and the system audio (everyone else) to TEMPORARY wav files, turns them
into a transcript offline, optionally tells the remote speakers apart (small separate model), and
deletes the audio the moment the transcript is safely stored. Audio never goes to the network.
Only the transcript TEXT may be sent to an LLM, only if the user switched summaries on.

    notetaker.start(title, acknowledged=True) / stop() / status()
    notetaker.store: list_sessions / get / delete / purge_expired / export_markdown
"""
REMINDER = ("Tell everyone on the call that you are recording it. In many places you need their consent "
            "before you record. Lyra keeps only the written transcript on this computer; the audio is "
            "deleted as soon as the transcript is ready.")
