"""Lyra — Shortcut manager for loading, matching, and managing shortcuts."""

import os
import json
import difflib
from typing import Optional

from engine.core import paths
from engine.bridge import ui

log = ui.log

# Path to shortcuts storage
SHORTCUTS_FILE = str(paths.data_path("shortcuts", "shortcuts.json"))


class ShortcutManager:
    """Manages user-defined shortcuts (multi-action voice sequences)."""

    def __init__(self):
        self.shortcuts = {}
        self.load_shortcuts()

    def load_shortcuts(self):
        """Load shortcuts from JSON file."""
        if not os.path.exists(SHORTCUTS_FILE):
            log.warning("Shortcuts file not found, starting with empty shortcuts")
            self.shortcuts = {}
            return

        try:
            with open(SHORTCUTS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)

            # Filter out metadata keys (starting with _)
            self.shortcuts = {k: v for k, v in data.items() if not k.startswith("_")}
            log.info(f"Loaded {len(self.shortcuts)} shortcuts")
        except Exception as e:
            log.error(f"Failed to load shortcuts: {e}")
            self.shortcuts = {}

    def save_shortcuts(self):
        """Save shortcuts to JSON file."""
        try:
            # Preserve metadata
            data = {}
            if os.path.exists(SHORTCUTS_FILE):
                with open(SHORTCUTS_FILE, "r", encoding="utf-8") as f:
                    existing = json.load(f)
                    data = {k: v for k, v in existing.items() if k.startswith("_")}

            # Merge shortcuts
            data.update(self.shortcuts)

            with open(SHORTCUTS_FILE, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            log.info(f"Saved {len(self.shortcuts)} shortcuts")
        except Exception as e:
            log.error(f"Failed to save shortcuts: {e}")

    def match_exact(self, command: str) -> Optional[dict]:
        """Find exact match for a command against shortcut triggers.

        Returns:
            Shortcut dict with 'id', 'name', 'actions' if found, None otherwise
        """
        command_lower = command.lower().strip()

        for shortcut_id, shortcut in self.shortcuts.items():
            triggers = shortcut.get("triggers", [])
            for trigger in triggers:
                if command_lower == trigger.lower().strip():
                    log.info(f"Exact shortcut match: '{shortcut_id}'")
                    return {
                        "id": shortcut_id,
                        "name": shortcut.get("name", shortcut_id),
                        "actions": shortcut.get("actions", [])
                    }
        return None

    def match_fuzzy(self, command: str, cutoff: float = 0.75, ambiguous_gap: float = 0.08) -> Optional[dict]:
        """Find fuzzy match for a command against shortcut triggers.

        Args:
            command: User's voice command
            cutoff: Similarity threshold (0.0-1.0)
            ambiguous_gap: if the best and second-best matches (from
                *different* shortcuts) score within this distance of each
                other, treat it as ambiguous rather than silently picking
                the higher one — a fuzzy-matched shortcut runs a whole
                action sequence with no LLM double-check, so guessing wrong
                here is more costly than guessing wrong on a single command.

        Returns:
            Shortcut dict with 'id', 'name', 'actions', 'score' if a
            confident match was found. If the top two candidates are too
            close to call, returns a dict with 'ambiguous': True and
            'candidates': [(name, score), ...] instead of guessing — the
            caller should ask the user which one they meant. None if
            nothing scored above cutoff at all.
        """
        command_lower = command.lower().strip()
        best_match = None
        best_score = cutoff
        second_best_score = 0.0
        second_best_name = None

        for shortcut_id, shortcut in self.shortcuts.items():
            triggers = shortcut.get("triggers", [])
            shortcut_top_score = 0.0
            for trigger in triggers:
                trigger_lower = trigger.lower().strip()
                # Use difflib for similarity scoring
                score = difflib.SequenceMatcher(None, command_lower, trigger_lower).ratio()
                shortcut_top_score = max(shortcut_top_score, score)
                if score > best_score:
                    if best_match and best_match["id"] != shortcut_id:
                        second_best_score = max(second_best_score, best_score)
                        second_best_name = best_match["name"]
                    best_score = score
                    best_match = {
                        "id": shortcut_id,
                        "name": shortcut.get("name", shortcut_id),
                        "actions": shortcut.get("actions", []),
                        "matched_trigger": trigger,
                        "score": score,
                    }
            if best_match and shortcut_id != best_match["id"] and shortcut_top_score > second_best_score:
                second_best_score = shortcut_top_score
                second_best_name = shortcut.get("name", shortcut_id)

        if not best_match:
            return None

        if second_best_name and (best_score - second_best_score) < ambiguous_gap:
            log.info(
                f"Ambiguous fuzzy shortcut match: '{best_match['name']}' ({best_score:.2f}) "
                f"vs '{second_best_name}' ({second_best_score:.2f})"
            )
            return {
                "ambiguous": True,
                "candidates": [(best_match["name"], best_score), (second_best_name, second_best_score)],
            }

        log.info(f"Fuzzy shortcut match: '{best_match['id']}' (score: {best_score:.2f})")
        return best_match

    def add_shortcut(self, shortcut_id: str, name: str, triggers: list, actions: list):
        """Add or update a shortcut.

        Args:
            shortcut_id: Unique identifier (kebab-case)
            name: Display name
            triggers: List of voice phrases that trigger this shortcut
            actions: List of action dicts
        """
        self.shortcuts[shortcut_id] = {
            "name": name,
            "triggers": triggers,
            "actions": actions
        }
        self.save_shortcuts()
        log.info(f"Added/updated shortcut: {shortcut_id}")

    def remove_shortcut(self, shortcut_id: str) -> bool:
        """Remove a shortcut by ID.

        Returns:
            True if removed, False if not found
        """
        if shortcut_id in self.shortcuts:
            del self.shortcuts[shortcut_id]
            self.save_shortcuts()
            log.info(f"Removed shortcut: {shortcut_id}")
            return True
        return False

    def list_shortcuts(self) -> list:
        """Get list of all shortcuts with their details.

        Returns:
            List of dicts with 'id', 'name', 'triggers', 'action_count'
        """
        result = []
        for shortcut_id, shortcut in self.shortcuts.items():
            result.append({
                "id": shortcut_id,
                "name": shortcut.get("name", shortcut_id),
                "triggers": shortcut.get("triggers", []),
                "action_count": len(shortcut.get("actions", []))
            })
        return result

    def get_shortcut(self, shortcut_id: str) -> Optional[dict]:
        """Get a shortcut by ID.

        Returns:
            Shortcut dict or None if not found
        """
        return self.shortcuts.get(shortcut_id)
