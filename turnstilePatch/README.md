# turnstilePatch

Chrome extension used when **CapMonster is off** (or as a soft helper with CapMonster).

## Behaviour

- Content script runs at `document_start` in **all frames**.
- **Only** frames whose URL looks like Cloudflare Turnstile perform a click.
- **One** delayed `checkbox.click()` after ~1.5s (`clickedOnce` guard).
- Does **not** spam-click (spam resets managed challenges).

Ported from [HSJ-BanFan/grok-register-web](https://github.com/HSJ-BanFan/grok-register-web) `turnstilePatch/script.js`.

## How it is loaded

| Driver | How the extension is applied |
| --- | --- |
| `local` | `create_browser_options()` → `ChromiumOptions.add_extension(turnstilePatch/)` |
| `roxy` | Path passed on profile **create** via Roxy API (`roxy_load_turnstile_extension`, default on). Roxy must accept extension path fields; if create ignores them, pack CRX once in Roxy GUI (see below). |
| `browser_use` | Not applied (remote cloud browser). |

## Roxy + WSL (important)

Roxy is a **Windows** app. If you run the register script under **WSL**, do **not** pass a Linux path like `/home/joseph/.../turnstilePatch` — Roxy rewrites that to `C:\home\joseph\...`, the manifest is missing, and open fails with “Fail manifes hilang”.

**Recommended:** copy the folder onto the Windows filesystem and set config:

```json
"roxy_turnstile_extension_path": "C:\\Users\\Joseph\\AppData\\Local\\grokRegister-cpa\\turnstilePatch"
```

From WSL:

```bash
mkdir -p /mnt/c/Users/Joseph/AppData/Local/grokRegister-cpa
cp -a turnstilePatch /mnt/c/Users/Joseph/AppData/Local/grokRegister-cpa/
```

The client also auto-converts `/mnt/c/...` via `wslpath -w` when possible.

## Roxy: if API path inject fails

1. In Roxy GUI: create a **template** profile, **Load unpacked** this folder (or pack CRX).
2. Set `roxy_one_profile_per_account` as usual **or** clone from that template if your Roxy build supports template IDs (`roxy_profile_create_payload`).
3. Debug: set `roxy_delete_profile_after_run: false` and confirm the extension appears under the open profile.

Absolute path of this folder is logged as `[Roxy] turnstilePatch path=...` when create runs.

## Related config

See repo `config.example.json` / README:

- `turnstile_warmup_seconds` — wait after profile fill for iframe + extension click (default `4`)
- `turnstile_extension_grace_seconds` — Python waits this long before any CDP click (default `5`)
- `turnstile_local_max_clicks` — max Python CDP clicks after grace (default `2`; was 4)
- `turnstile_local_click_interval` — min seconds between CDP clicks (default `8`)
- `turnstile_settle_seconds` — passive watch after fill before solve path (default `12`)
- `turnstile_fill_loop_extra_click` — if `true`, fill loop re-clicks when still unsolved (default `false` — spam resets managed CF)
- `turnstile_force_enable_submit` — force-enable Complete when token length is OK
- `roxy_load_turnstile_extension` — try inject path on Roxy create

### Lesson from live runs

Managed Turnstile often **fails harder when hammered**. Observed:

1. Extension once-click + idle → soft-pass token can appear.
2. Multiple rapid CDP clicks → “Pengesahan gagal” / challenge reset.
3. Stopping the script (no more clicks) sometimes let the open Roxy session finish.

So local path prefers: **extension once → long passive wait → at most 1–2 sparse CDP clicks**.
