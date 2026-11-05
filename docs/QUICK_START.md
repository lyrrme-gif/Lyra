# Lyra Quick Start Guide

## 🎤 Voice Mode Commands

### Starting Lyrr
```powershell
cd C:\Users\lyrrm\lyrr-project\lyrr
python -m engine.core.main
```

### Wake Word
**You MUST say the wake word first!**

1. Wait for: `"Standby: listening for wake word..."`
2. Say: **"Hey Lyra"** (or "Hey Leer", "Hey Lear" - phonetic variations work)
3. Lyrr responds: **"Yes, sir?"**
4. Now give your command

### Example Conversation
```
You: "Hey Lyra"
Lyrr: "Yes, sir?"
You: "Open Spotify"
Lyrr: "Opening Spotify, sir."
```

### Inline Commands (Advanced)
Say the wake word + command in one breath:
```
You: "Hey Lyra, what time is it?"
Lyrr: [responds immediately]
```

---

## 🔄 Switching Modes

### Voice → Text Mode
**Option 1 (Voice):** Say **"switch to text mode"** or **"change to typing mode"**
**Option 2 (Keyboard):** Press `Ctrl+T`

### Text → Voice Mode
**Option 1 (Text):** Type `/voice` and press Enter
**Option 2 (Keyboard):** Press `Ctrl+T`

---

## 💬 Text Mode Commands

In text mode, just type commands directly:
```
> open spotify
> what time is it
> /voice  (to switch back)
> /exit   (to quit)
```

---

## ⚠️ Troubleshooting

### "I keep talking but nothing happens"
**Problem:** You didn't say the wake word
**Solution:** Say **"Hey Lyra"** first, then give your command

### "Lyrr hears me but ignores me"
**Problem:** Your speech doesn't contain "lyrr" in standby
**Solution:** Make sure to clearly say **"Hey Lyra"** to wake up

### "How do I know if Lyra is listening?"
Check the console:
- `Standby: listening for wake word...` = Say "Hey Lyra" to wake
- `Listening...` = Lyra is waiting for your command (already awake)

---

## 🎯 Common Commands

### Safe Commands (Execute Immediately)
- "Open Spotify/Discord/Chrome/etc"
- "What time is it?"
- "Volume up/down"
- "Take a screenshot"
- "Lock the screen"
- "What's my CPU usage?"

### Confirm First (Asks Permission)
- "Shut down the computer" → Lyrr asks: "Are you sure?"
- "Delete [file/folder]" → Lyrr asks for confirmation
- "Rename [file]" → Lyrr asks for confirmation

### Mode Control
- "Switch to text mode" → Switches to typing
- "Go to sleep" / "Standby" → Returns to wake word listening
- "Goodbye forever" → Exits Lyrr

---

## 🔑 Environment Setup

Your Groq API key should be set as an environment variable:
```
setx GROQ_API_KEY "your-key-here"
```

This enables:
- Chat/conversation features
- LLM-powered command correction
- Web search integration (if Tavily key also set)

---

## 📝 Quick Tips

1. **Speak clearly** - Whisper STT is accurate but needs clear audio
2. **Say the wake word** - Don't forget "Hey Lyra" in standby!
3. **Use inline commands** - "Hey Lyra open spotify" works in one go
4. **Permission prompts** - Say "yes" or "no" clearly when asked
5. **Mode switching** - Use voice commands or Ctrl+T anytime

---

## 🚀 What's Next?

**Phase 1: Shortcuts** is coming next!

You'll be able to create custom voice shortcuts like:
- "Get me ready for work" → Opens Spotify, VS Code, Chrome, work folder, sets volume
- "Gaming mode" → Launches games, Discord, sets RGB, adjusts volume
- "Study mode" → Opens focus apps, starts timer, blocks distractions

Stay tuned! 🎯
