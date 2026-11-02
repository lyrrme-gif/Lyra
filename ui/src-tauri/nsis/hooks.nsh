; NSIS installer / uninstaller hooks for Lyra.
;
; Install: nothing extra — Tauri already ships Start Menu + optional Desktop
; shortcuts and the lyra-backend/ resource folder next to Lyra.exe.
;
; Uninstall: offer to remove %APPDATA%\Lyra (settings, keys, memory, models).
; Never delete without asking. Legacy Coucou paths cleaned if still present.

!macro NSIS_HOOK_PREINSTALL
  ; An update over a running Lyra: the backend exe would be locked.
  nsExec::Exec 'taskkill /F /T /IM lyra-backend.exe'
!macroend

!macro NSIS_HOOK_POSTINSTALL
  ; Nothing here on purpose. Lyra must NOT start by itself while the installer is still running:
  ; the Finish page has its own "Run Lyra" and "Create desktop shortcut" check boxes, and only
  ; those decide what happens. (The first-launch setup wizard shows when Lyra is started.)
!macroend

!macro NSIS_HOOK_PREUNINSTALL
  nsExec::Exec 'taskkill /F /T /IM lyra-backend.exe'
  DeleteRegValue HKCU "Software\Microsoft\Windows\CurrentVersion\Run" "Lyra"
  MessageBox MB_YESNO|MB_ICONQUESTION \
    "Also delete Lyra data in %APPDATA%\Lyra?$\r$\n$\r$\nThis removes settings, API keys, memory and downloaded voice models." \
    IDNO lyra_keep_data
  RMDir /r "$APPDATA\Lyra"
  lyra_keep_data:

  ; Leftover Coucou relay paths from older shells (harmless if absent).
  RMDir /r "$LOCALAPPDATA\Coucou\bin"
  RMDir /r "$LOCALAPPDATA\Coucou\inbox"
  Delete "$LOCALAPPDATA\Coucou\coucou.log"
!macroend
