# Firebase relay setup (~10 minutes, one time)

The relay is the cloud channel the field tablets and the office desktop talk
through for **live crew tracking** in the Monitor view. It's a free Firebase
Realtime Database. Until it's configured, the Monitor view shows simulated
crews ("● LIVE (simulated)").

## 1. Create the project
1. Go to <https://console.firebase.google.com> → **Add project**. Name it
   (e.g. `beetent-relay`). You can disable Google Analytics. Free "Spark" plan
   is plenty.

## 2. Enable the Realtime Database
1. Left menu → **Build → Realtime Database → Create Database**.
2. Pick a location, then choose **Start in locked mode** (we set rules next).
3. Note the database URL shown at the top, e.g.
   `https://beetent-relay-default-rtdb.firebaseio.com`.

## 3. Set the rules
In the Realtime Database **Rules** tab, paste this and **Publish**:

```json
{
  "rules": {
    "crews": { ".read": true, ".write": true },
    "$other": { ".read": false, ".write": false }
  }
}
```

> This lets anyone who knows the database URL read/write the `crews` node only.
> Crew GPS positions are low-sensitivity and the URL lives only in gitignored
> config (never in the public repo), so this is fine to start. We can harden it
> later with anonymous auth if you want.

## 4. Get the web config (for the tablet)
1. Project **Settings** (gear) → **General** → scroll to **Your apps** →
   **Web app** (`</>`). Register an app (nickname `tablet`).
2. Copy the `firebaseConfig` object it shows you.
3. In `tablet/`, copy `firebase-config.example.js` → **`firebase-config.js`**
   and paste your values. (This file is gitignored.)

## 5. Point the desktop at it
1. In the repo root, copy `firebase_config.example.json` →
   **`firebase_config.json`** (gitignored).
2. Set `databaseURL` to the URL from step 2. Leave `token` empty (open rules).

## 6. Use it
- **Desktop:** open **Menu → 📡 Monitor**. The banner turns green **● LIVE**.
- **Tablet:** open the app, tap **Fields**, set your crew name ("Change").
  While a field is active it publishes position + progress every 2 s; the
  crew appears on the office Monitor map and drops off when the tablet leaves.

## Notes
- Publishing is **online-only** (needs internet to reach Firebase). With no
  signal the tablet still navigates fully; it just isn't visible to the office
  until it reconnects.
- Free tier limits (100 concurrent connections, 10 GB/month) are far beyond a
  handful of crews pushing a tiny JSON twice a second.
