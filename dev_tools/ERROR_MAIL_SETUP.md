# Gmail error mail

Mail is disabled in `config/error_mail/settings.json`. Existing Bark settings and game
recovery behavior are unchanged. Only the new helper uses this separate setting.
Sender and recipient are both `your-account@gmail.com`; subject prefix is `[OAS-ERROR]`.

## Local password input

Run `dev_tools/Setup-ErrorMail.ps1` in Windows PowerShell with `-STA`.
The window is titled **OAS Gmail Error Mail Setup**. Generate an app password
yourself on Google's official page: https://myaccount.google.com/apppasswords.
Paste it into the hidden field, click **Save credential**, then approve the local
confirmation. The helper writes only the current Windows user's generic
credential `OAS/ErrorMail/Gmail/your-account@gmail.com`. It never reads an existing
credential. It does not enable mail, send mail, or interact with the game.
Google may require two-step verification and may not offer app passwords for
some accounts. Never provide a normal login password or put an app password in
chat, a shell command, environment variable, JSON configuration, or a screenshot.

The safe progress file `config/error_mail/setup_status.json` contains only a
status and time. No password is written to a file or to tool output.

## One approved SMTP test

After local saving and explicit user approval, run:

```
.\toolkit\python.exe -B .\module\notify\error_mail.py --test
```

This sends a fixed, synthetic setup email using this computer's Gmail SMTP
connection. It contains no account, task, log, screenshot or game data. The
separate test does not enable real error mail. A persistent one-attempt marker
prevents blindly repeating an uncertain delivery. Read only
`config/error_mail/test_status.json` for the safe result. `accepted` means Gmail
SMTP accepted the email; confirm final arrival independently in Gmail.

## Real error reports

Enable only after the agreed test and user confirmation by changing the separate
configuration's `enabled` to `true`. Do not change any account's `notify_config`.
Reports contain only two-digit account number, canonical task name, exception
type and the last six project-relative file/function/line frames. Exception
messages, source lines, locals, external stack paths, device identifiers, logs
and screenshots are excluded. Initialization errors use task `Initialize`.

The rate limit is one attempt per account per five minutes, one identical report
per fifteen minutes, and twelve attempts total per hour across all processes.
Failed attempts consume quota too. Only account numbers, times and SHA-256
fingerprints are saved in the separate rate-state file. SMTP uses certificate
verification and a five-second socket timeout. The entire isolated SMTP/DNS
child has a fifteen-second timeout, and no automatic delivery retry is made.
Reporting failure never changes existing Bark, recovery, restart or scheduling.

Already-running Python processes keep their loaded `script.py`; hooks take
effect on a later normal process start. No running process was restarted during
setup. Once the new code is loaded, the separate enabled setting is reread at
each error, so later setting changes do not require another restart.

Offline tests (SMTP, credentials and process sending are mocked):

```
.\toolkit\python.exe -B .\tests\test_error_mail.py
```

To disable, set `enabled` to `false`. To revoke Gmail access, revoke that app
password on Google and optionally remove the named Windows generic credential
manually. Neither action requires changing Bark.

The dedicated `config/error_mail/` folder is excluded from Git and from the
root-level account configuration enumeration. No account JSON files are changed.
