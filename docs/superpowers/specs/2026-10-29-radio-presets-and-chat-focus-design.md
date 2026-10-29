# Radio Presets and Cardputer Chat Focus — Design

Date: 2026-10-29. Owner: mjgodat. Status: draft for review.

Two pieces of work, built in this order:

1. **Radio presets** — named, numbered radio settings that can be listed,
   created and loaded from the CLI on the ThinkNode M5 repeater, the T114
   companion and the Cardputer companion, plus Fn+number keys on the Cardputer.
2. **Cardputer chat focus and message menu** — the chat screen gets a focus
   model (messages vs. text box), and Enter on a message opens a menu with
   Reply / Info / Resend / Copy. Messages keep the metadata the Info view needs.

## Why

The owner runs a public-mesh repeater (M5, "CaptCallahan") and personal
devices. They want to move their repeater and their own devices onto radio
settings only they use ("private to my settings") and back again quickly, without
retyping `set radio` tuples on every device each time. The owner understands
this is not encryption; it keeps his traffic off the shared mesh. Real privacy
remains private channels/DMs and is out of scope.

On the Cardputer, reading back through a busy chat and seeing *how* a message
got there (hops, path, signal, repeats heard) is what the phone apps offer and
the Cardputer lacks. Today an Enter press anywhere in a chat sends; the owner
wants Enter outside the text box to never send.

## Goals

- Up to 5 preset slots per device, persisted in flash.
- Same three commands on M5, T114 and Cardputer.
- A remote preset load on a repeater cannot strand it.
- Cardputer: Fn+1…Fn+5 load slots 1–5.
- Cardputer chat: arrows always navigate; typing only when the text box is
  focused; Enter on a message opens its menu; Info shows hops, path, SNR/RSSI,
  time, and for sent messages repeats heard / delivery.

## Non-goals

- No encryption or channel changes. No change to `set radio` semantics.
- No preset UI on the M5 screen/button, and no T114 phone-app integration
  (T114 is CLI-only for presets).
- No head (CYD) firmware changes: its Repeater console already sends
  arbitrary CLI, and its Login button provides the confirming login.
  A Presets button on the head is a possible later addition, not this work.
- No Fn+R (reply is covered by the focus + message menu). No Fn commands
  beyond those listed in "Cardputer Fn additions".

## Anti-goals (failure even if it "works")

- A remote `load preset` that leaves the repeater unreachable with no way back.
- A preset tuple format that differs from `set radio` in any way.
- Enter on a highlighted message sending the text-box contents.
- Existing Cardputer chat history on SD becoming unreadable.

## Part 1 — Radio presets

### Commands (owner decided)

| Command | Behavior |
|---|---|
| `get preset` | List slots 1–5: `<n> <name> <freq>/<bw>/SF<sf>/CR<cr>`, mark the one matching the live radio as `(active)`, empty slots as `(empty)`. |
| `add preset <name> <slot> <radio tuple>` | Create or overwrite slot `<slot>`. `<radio tuple>` is **exactly** what `set radio` accepts, including its optional preamble, parsed and validated by the same code path (`RadioProfileCLI::parseSuffix` + `cli::parseRadioTupleStrict` + the role's `acceptsPrimary…` check). Invalid → error, nothing saved. The preset stores the preamble; `get preset` shows it only when non-default, and "(active)" matching includes it. |
| `load preset <slot>` | Apply that slot as the primary radio (see load semantics). Empty slot → error, no change. |

Owner example: `add preset Private 2 915.000,62.5,7,5`.

Builder's call (stated, not owner-decided): `del preset <slot>` to clear a
slot; name is one token, max 12 chars, printable ASCII; slot range 1–5.
Command words are also accepted with `preset` first (`preset load 2`) only if
it is free to do so — not required.

### Slot 1

On first use (no preset file), slot 1 is the US/Canada standard (owner
decided 2026-10-29: most users will be US-based): name `USA-Canada`, tuple
`910.525,62.5,7,5` (the `USA/Canada` entry `build.sh` already defaults to).
It does not copy the device's current radio; a device on other settings shows
as custom until a slot is loaded. Fn+1 on a fresh Cardputer therefore selects
the US/CA mesh. The default is overridable at build time (`-D` defines) for
non-US builds. After first use slot 1 is an ordinary slot and `add preset` may
overwrite it.

### Storage

A small versioned file on each device's internal filesystem (e.g.
`/presets`), separate from `/com_prefs`/NodePrefs so no existing preference
offsets move. Survives reboot and app-only reflash. Corrupt or wrong-version
file → treated as absent (slot 1 re-seeded), never a crash.

### Load semantics

- **Local load** (USB serial on any device, or Fn+N on the Cardputer): apply
  and save as the primary radio immediately, the same as `set radio` does on
  that role. Reply/alert shows name and tuple.
- **Remote load on the repeater** (command arrived over the mesh from a logged-in
  admin, i.e. `sender_timestamp != 0`): reply first
  (`"Loading Private 915.000/62.5/SF7/CR5 in 5s; log in on new settings within
  10 min to keep"`), then switch, with a 10-minute revert window.
  - **Commit rule (owner decided):** a fresh admin login (login request
    accepted for a full admin) received after the switch commits it, whether
    it arrives over LoRa or over the ESP-NOW bridge (the CYD head). The owner
    wants to be able to switch from the head with the same log out / log in
    confirmation. Commands from an already-logged-in session do not commit.
    Accepted cost: a head login proves head reachability, not LoRa
    reachability on the new settings.
  - No commit within 10 minutes → revert to the previous primary on its own.
    A reboot during the window boots the previous primary (the new preset is
    not saved until committed).
  - **Do not reuse `tempradio`** (designer's call): on the cascade profile it
    suspends `suspend=tempradio` flood rules, switches the no-RX-reboot rule
    to temporary-mode settings, cancels pending `tempradio`/`tempradioat`
    entries and shows "(tempradio)". Use a dedicated revert timer. Refuse a
    remote `load preset` while a `tempradio` is active (reply with an error).
  - `get preset` during the window shows `(pending, reverts in Nm)`.
- Companions (T114, Cardputer) have no remote admin path for this; loads are
  always local.
- T114/Cardputer: if the phone app later pushes radio params, those win;
  `get preset` then shows no slot `(active)`.

### Where it lives

Both roles already route commands through `mesh::RadioProfileCLI`
(`src/helpers/RadioProfileCLI.*`) before their own `set radio` handling:
repeater via `CommonCLI` (`src/helpers/CommonCLI.cpp`, `set radio` at the
`memcmp(config, "radio ", 6)` branch, `applyAndSavePrimaryRadioParams`), and
companion via `examples/companion_radio/MyMesh.cpp` (`set radio ` handler,
`applyAndSaveRadioParams`). The preset store and command parsing should be one
shared helper used by both, with each role supplying its own "apply primary"
and "temporary apply" callbacks. Exact structure is the builder's call.

## Part 2 — Cardputer chat focus and message menu

All in `variants/m5stack_cardputer_adv/companion_radio/ui-cardputer/`
(`screen/main/ChatPage.*`) plus storage in
`variants/m5stack_cardputer_adv/CardputerDataStore.*` and hooks in
`companion_radio/CardputerMesh.*`.

### Focus model (owner decided)

- Exactly one focus: a message in the history, or the text box.
- The Cardputer has no dedicated arrows: Up is the `;` key and Down is the
  `.` key (`ARROW_UP == KEY_SEMICOLON`, `ARROW_DOWN == KEY_PERIOD`). Owner
  chose:
  - Message focused: plain Up/Down move focus through messages; Down from the
    newest message focuses the text box.
  - Text box focused: `;` and `.` type characters. Up leaves the text box to
    the newest message only when the box is empty, or with Fn+Up at any time.
- Typing (layout lookup → text box) only when the text box has focus. With a
  message focused, character keys do nothing.
- Enter on the text box sends (as today). Enter on a message **never sends**;
  a plain press opens the message menu (owner chose plain press over long
  press).
- Focus is visible: highlight bar on the focused message, bright border on the
  focused text box.
- Opening a chat focuses the text box. Esc on a message → focus text box. Esc
  on the text box → leave chat (as today).
- Existing Ctrl+Up (recall last sent) and Ctrl+T (cancel resend) keep working
  when the text box has focus. The old "Fn inverts arrow meaning" rule goes.

### Message menu

Items: **Reply**, **Info**, **Resend** (own messages only), **Copy**.
Up/Down + Enter to choose, Esc to close.

- Reply: channel → prefill `@[Sender] ` and focus the text box; DM → focus the
  text box.
- Copy: put the message text into the text box (focus moves there).
- Resend: send the same text again to the same contact/channel.
- Info, received message: sender, time (sender timestamp and local receive
  time), hops or "direct", SNR and RSSI of the received packet, path as
  repeater hash IDs (e.g. `C5 > D8 > 14`) when the packet carried one.
- Info, own channel message: repeats heard count and the IDs of repeaters
  heard echoing it.
- Info, own DM: delivered / failed / pending, route used (direct path hops or
  flood), round-trip time when acked.

### Message metadata and storage

Today: `HistoryMessage` holds only `text` and `out`.
`CardputerDataStore::storeMessage` appends plain text lines (`>> text` /
`<< text`) to a per-chat file on SD, with no header or version, never
rewritten. `last_message_heard_repeats` is one counter for the most recent
own channel message only (matched by `last_message_hash`), with no repeater
IDs; DM delivery state exists only for the single in-flight `last_msg`.

Designer's calls:

- **Received messages:** capture hops, SNR, RSSI, path hashes, sender
  timestamp and local receive time in the `CardputerMesh` receive hooks (the
  packet carries them). Persist them with the message on SD by extending the
  line format in a backward-compatible way (e.g. an optional metadata suffix
  or a new line prefix); old plain lines still load with Info showing
  "no details".
- **Own messages:** repeats heard (count + repeater IDs, newly captured from
  the echoed packets' paths), delivery state and RTT are tracked **in RAM
  only, for the session**, for the last several own messages (not just the
  most recent). After a reboot, Info for an old own message shows text/time
  only. Persisting these is out of scope.
- Channel messages carry the sender name inside the text (`Name: msg`); parse
  it for Reply.
- Field sizes and layout are the builder's call, bounded by SD space and the
  RAM cost of `CHAT_HISTORY_RINGBUF_SIZE` records.

## Cardputer Fn additions (owner decided)

| Key | Action |
|---|---|
| Fn+1…Fn+5 | Load preset slot N (local load). Alert shows name + tuple; empty slot → "Preset N empty", no change. |
| Fn+0 | Alert showing the active preset name and tuple, or "custom" + tuple if the live radio matches no slot. |
| Fn+Z | Ping the selected repeater (reuse the existing Tools-screen ping); alert shows reply SNR or "no answer". If no repeater is selected, say so. |
| Fn+U | Open the chat holding the newest unread message; "No unread" otherwise. |
| Fn+Q | Keyboard lock toggle: all keys ignored except Fn+Q; alert "Keys locked"/"Keys unlocked"; incoming messages still display. Lock state is not persisted. |

Also: the status bar always shows the active preset name (or "custom").
Fn+1…5 and Fn+0 must not collide with layout lookups of digits; Fn on the
Cardputer is one-shot (armed by a press, applies to the next key).

## Constraints

- Repo: `C:\Users\Micha\mc_pr`. This work goes on its own branch off
  `keymindCascade`, separate from the uncommitted `feature/cyd-espnow-head`
  work. Nothing is committed or pushed without the owner saying so.
- Windows build workarounds and one-PlatformIO-process rule apply (AGENTS.md).
- M5 is a live repeater; announce before opening its port (opening resets it).
- Secrets never in committed source.
- Must build for: ThinkNode_M5_Repeater (cascade profile), the T114 companion
  env in use, and M5Stack_Cardputer_Adv_ui. Cardputer Launcher packaging rules
  from the earlier session still apply.
- Host tests for the preset store/parser, the remote-load commit/revert state
  machine, and the history line format (old and new lines).
- T114: build the companion env that has the USB CLI
  (`ENABLE_USB_INTERFACE`), confirmed against the T114's current `ver`.

## Left to the builder

Internal class layout, file names, exact reply strings beyond those quoted,
highlight colors, record field widths, whether `del preset` exists, how the
login-commit is detected for the temp-radio window, and test structure.

## Open items

None.
