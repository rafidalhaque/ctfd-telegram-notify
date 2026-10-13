# CTFd Telegram Notify

Sends Telegram messages for:
- Every challenge solve: `🚩 <b>ChallengeName</b> solved by <b>solver</b>!`
- First blood on each challenge (the first solve), called out separately:
  `🩸 <b>First blood!</b> <b>solver</b> was first to solve <b>ChallengeName</b>!`
- Halfway through the competition (midpoint of CTFd's Start/End config)
- When the scoreboard freeze time is reached (CTFd's Freeze config)

## Settings
Admin page at `/admin/telegram-notify`:
- **Bot Token** / **Chat ID** / **Topic (Thread) ID** (optional, for
  forum-style Telegram groups) / **API Base URL** (advanced; leave
  default unless self-hosting the Bot API server).
- Per-event toggles for solves / first blood / half-time / freeze
  (independent of each other — e.g. first blood can be on while regular
  solve notifications are off, or both together).
- **Send Test Message** button to confirm your bot token/chat id work
  before the event starts.
- **Reset Half-time / Freeze Flags** button, in case you change the
  Start/End/Freeze times after one of those notifications already fired
  and want it to fire again at the new time.

All settings are stored via CTFd's own `get_config`/`set_config`
key-value store — no extra settings table.

## How it works
- **Solves / first blood**: hooks a SQLAlchemy `after_insert` event on
  the `Solves` model (fires regardless of code path — flag submission,
  admin-awarded solve, etc.), buffers the new solve's id in the
  request's `g`, then sends the actual Telegram message(s) from an
  `after_request` hook once CTFd's own commit has completed. This keeps
  the notification off the critical path of the flag-submission
  transaction. First blood is determined by counting committed `Solves`
  rows for that challenge at that point — if it's the only one, this was
  the first solve.
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
- **First blood**: two users solving the same challenge in sequence —
  the first solver triggered both a first-blood message and the regular
  solve message (in that order), the second solver triggered only the
  regular message. Re-tested with regular solve notifications disabled
  and first blood alone enabled on a second challenge: exactly one
  message sent (first blood only), confirming the two toggles are
  independent.
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

A third bug was caught after reports of the half-time notification
never firing in practice: `telegram_halftime_sent` (and
`telegram_freeze_sent`) were marked "done" unconditionally, even when
`_send_telegram_message()` failed (bad token, bot not in the group,
a transient network error, wrong API base, etc.) right at the moment
the midpoint was crossed. Since the check is one-shot, a single failed
attempt permanently "used up" the notification with it never actually
being delivered, and the only way out was a manual Reset Flags click.
Fixed so the "sent" flag is only set when the send actually succeeds;
otherwise it logs a warning and retries on the next request. Verified
by pointing the API base at a bad hostname (forcing a real network
failure), confirming the flag stayed unset and the mock server received
nothing, then fixing the URL and confirming the very next request
retried and delivered successfully.
