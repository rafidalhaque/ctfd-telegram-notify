# CTFd Telegram Notify

Sends Telegram messages for:
- Every challenge solve: `🚩 <b>ChallengeName</b> solved by <b>solver</b>!`
- Halfway through the competition (midpoint of CTFd's Start/End config)
- When the scoreboard freeze time is reached (CTFd's Freeze config)

## Settings
Admin page at `/admin/telegram-notify`:
- **Bot Token** / **Chat ID** / **Topic (Thread) ID** (optional, for
  forum-style Telegram groups) / **API Base URL** (advanced; leave
  default unless self-hosting the Bot API server).
- Per-event toggles for solves / half-time / freeze.
- **Send Test Message** button to confirm your bot token/chat id work
  before the event starts.
- **Reset Half-time / Freeze Flags** button, in case you change the
  Start/End/Freeze times after one of those notifications already fired
  and want it to fire again at the new time.

All settings are stored via CTFd's own `get_config`/`set_config`
key-value store — no extra settings table.

## How it works
- **Solves**: hooks a SQLAlchemy `after_insert` event on the `Solves`
  model (fires regardless of code path — flag submission, admin-awarded
  solve, etc.), buffers the new solve's id in the request's `g`, then
  sends the actual Telegram message from an `after_request` hook once
  CTFd's own commit has completed. This keeps the notification off the
  critical path of the flag-submission transaction.
- **Half-time / freeze**: checked on every request via
  `app.before_request` (same approach as the companion
  `ctfd-challenge-scheduler` plugin) — no background thread needed,
  since an active CTF has constant traffic. Each fires at most once,
  tracked via `telegram_halftime_sent` / `telegram_freeze_sent` config
  flags, until reset from the admin page.

## Install
1. Copy this directory to `CTFd/CTFd/plugins/ctfd-telegram-notify/` on
   the server running CTFd.
2. Restart CTFd.
3. Visit `/admin/telegram-notify`, fill in your bot token/chat id, send
   a test message to confirm delivery, then enable the events you want.

## Verification performed
Tested against a disposable local CTFd + MariaDB instance, with a tiny
mock Telegram Bot API server (plain `http.server`, no real Telegram
account needed) standing in for `api.telegram.org` via the API Base URL
setting, logging every request it received:
- **Test message**: correct URL path (`/bot<token>/sendMessage`), JSON
  body, `chat_id`, and `message_thread_id`.
- **Solve notification**: registered a challenge + flag, solved it via
  the real `/api/v1/challenges/attempt` endpoint (not a direct DB
  insert) as a non-admin user — correct message with the right
  challenge name and solver name. A follow-up wrong-flag submission (and
  a duplicate correct one) correctly produced zero additional messages.
- **Half-time / freeze**: set Start/End/Freeze so both were already due;
  both fired exactly once on the next page load, and did **not** re-fire
  on subsequent requests. "Reset Flags" correctly re-armed both, and they
  fired again on the next request.
- **Per-event toggles**: turning a toggle off and solving another
  challenge produced no notification; turning it back on and solving
  again produced the expected message.

A real bug was caught and fixed during this: the toggle checkboxes
appeared checked (and solve notifications kept firing) even after being
saved as "disabled". Root cause was a type mismatch, not a caching
issue — CTFd's `get_config()` converts a stored numeric string like
`"0"` into the Python integer `0`, and the original code checked
`value in (None, "", "0", "false", "False")`, which is `False` for the
integer `0` in Python (`0 != "0"`). Fixed with a small `_is_enabled()`
helper that normalizes bools/ints/strings before checking, used
consistently in both the notification logic and the settings page's
checkbox rendering.

A template-naming collision was also caught: the settings page template
was originally named `settings.html`, which collided with CTFd's own
built-in public account-settings template of the same name — Flask's
template loader silently preferred CTFd's version, so the admin page
rendered the wrong content entirely. Fixed by renaming to
`telegram_settings.html`.
