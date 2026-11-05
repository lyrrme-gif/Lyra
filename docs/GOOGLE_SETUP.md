# Connect Lyra to Google (read-only)

Lyra only asks for two **read-only** permissions: Google Calendar (view) and Gmail (read). It cannot send,
delete or change anything. Replies are drafted as text only.

1. Open <https://console.cloud.google.com/> and create a project (e.g. "Lyra").
2. **APIs & Services → Library**: enable **Google Calendar API** and **Gmail API**.
3. **APIs & Services → OAuth consent screen**: user type **External**, fill in the app name and your email, and
   add your own Google account under **Test users**. (Scopes can stay empty here; Lyra requests them at sign-in.)
4. **APIs & Services → Credentials → Create credentials → OAuth client ID**, application type **Desktop app**.
   Download the JSON.
5. Save it as `client_secret.json` in the Lyra data folder:
   `%APPDATA%\Lyra\google\client_secret.json`  (create the `google` folder if needed).
6. `pip install -r requirements.txt` (adds `google-auth`, `google-auth-oauthlib`), restart Lyra.
7. Open **Settings → Features → Google Calendar & Gmail** and press **Connect Google** (it shows what is missing
   if something is). Or say or type **"connect Google"**. Your browser opens; approve the two read-only permissions
   (an "unverified app" warning is normal for your own project: Advanced → Continue).
   Or run `python -m engine.features.google_workspace connect` from the repo root.

The sign-in token is stored in `%APPDATA%\Lyra\google\token.json` (never in the project) and survives restarts.
Note: while your OAuth consent screen is in **Testing** mode, Google expires the token after 7 days; just say
"connect Google" again, or set the consent screen to "In production" (stays unverified, fine for personal use).

Commands: "what's my next event", "today's agenda", "read my latest emails", "any unread emails",
"summarize the first email", "draft a reply to the first email saying ...", "disconnect Google".

Disconnecting (Settings or by voice) deletes the local sign-in **and** asks Google to cancel the access; if Google can't
be reached you can also remove Lyra at <https://myaccount.google.com/permissions>.
