# WhatsApp helper

`bridge.bundle.js` is what Lyra runs (`node bridge.bundle.js <auth folder> [media folder]`). It is `bridge.js` plus the Baileys
library, packed into one file, so the PC only needs Node.js 20+; no `npm install`, no Git, no internet setup.

Why bundled: one of Baileys' packages (libsignal) is fetched from GitHub through Git, which fails on a PC without
Git. Bundling moves that to the build.

Rebuild after changing `bridge.js` or the Baileys version (needs Git and internet, on the developer's PC only):

    npm install
    npm run build

## Photos and voice messages
`bridge.js` describes every photo / voice message (`media` on the `message` event) and, only while Lyra has sent
`{"cmd":"config","media":true}`, saves it to the media folder under a random name and sends one `{"event":"media"}`
follow-up (the file, or why not). View-once media is never saved. The full list of events and commands is at the top
of `bridge.js`.

`bridge.bundle.js` currently carries this `bridge.js` in its last section (`// bridge.js`), put in place by hand
without esbuild. Run `npm install` and `npm run build` once on a PC with internet to regenerate it the usual way.

Test it without a network or phone (fake Baileys): `python -m unittest tests.test_wa_media` from the backend folder.
