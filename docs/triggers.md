# Triggers

Triggers start agent runs without anyone pressing Run: on a schedule, from GitHub events, or from Slack. Manage them in
the agent's **Deploy** tab under **Triggers**, or through the API described in [CONTRACT.md](../CONTRACT.md#triggers).
Triggered runs use the production version unless the trigger pins one, and they show up in Runs with the source
`schedule`, `github` or `slack`. Each trigger keeps a history of what it fired, skipped or missed, and is limited to 30
runs per hour by default (`max_runs_per_hour`).

GitHub and Slack must reach your Forge server over the internet. Expose it with a tunnel and tell Forge its public
address so webhook URLs and run links use it:

```bash
cloudflared tunnel --url http://localhost:8000
export FORGE_PUBLIC_URL=https://<your-tunnel>.trycloudflare.com   # then restart `make serve`
```

Hook endpoints accept tunneled traffic because each request is checked against the trigger's own secret. Every other
builder route stays localhost only (or needs the agent's API key).

## Schedules

1. Deploy tab, **Add schedule**.
2. Pick a preset (Every weekday at 9:00, Every hour, ...) or type a 5 field cron expression
   (`minute hour day-of-month month day-of-week`). The preview shows the next three times in the chosen time zone.
3. Write the input the agent receives each time. `{date}`, `{time}` and `{weekday}` are filled in for that slot.
4. Leave **Skip a run while the previous one is still running** on unless overlapping runs are fine.

The scheduler runs inside the server. If the server was down when a slot came due, the slot still fires when it comes
back within 10 minutes; older slots are skipped and listed as missed in the history.

## GitHub

1. Deploy tab, **Add GitHub**. Choose the events:
   - **Issues**: every new issue, or only issues given the label in **Label filter**.
   - **Comments**: comments on issues or pull requests that start with the command (default `/forge`). The rest of the
     comment becomes `{comment}`, so `/forge add a regression test` sends "add a regression test".
   - **Pull requests**: opened and new commits pushed.
   - **Pushes**: optionally only to branches matching **Push branches** (`main, release/*`).
2. Optionally write an input template (`{repo} {number} {title} {body} {comment} {pr_url} {sender} {branch}
   {commits}` ...). Leave it empty for a sensible default per event.
3. Optionally paste a token so Forge comments with the result: a fine grained personal access token for the
   repository with read and write access to **Issues** and **Pull requests**.
4. Save, then copy the **webhook secret**. It is shown only once (rotate it with `POST /v1/triggers/{id}/rotate-secret`).
5. In the repository: **Settings**, **Webhooks**, **Add webhook**.
   - Payload URL: the URL shown on the trigger.
   - Content type: `application/json`.
   - Secret: the webhook secret.
   - Events: "Let me select individual events" and tick the ones you chose (Issues, Issue comments, Pull requests, Pushes).
6. GitHub sends a ping right away; the webhook page should show a green check. Events that do not match the filters
   get 204 and are not run.

Comments from bots are ignored, so Forge's own result comments never trigger another run.

## Slack

1. Deploy tab, **Add Slack**. You can save it without the signing secret and add it after creating the Slack app.
2. Create an app at https://api.slack.com/apps (**From scratch**) in your workspace.
3. **Slash Commands**: create `/forge` (or any name) with the trigger's **Request URL**.
4. Optional, for `@Forge do something` in channels: **Event Subscriptions** on, the same Request URL (Slack verifies it
   immediately), and subscribe to the `app_mention` bot event.
5. **OAuth and Permissions**: add the `commands` scope, plus `app_mentions:read` and `chat:write` for mentions, then
   **Install to Workspace**.
6. Paste the **Signing Secret** (Basic Information, App Credentials) into the trigger. For mentions, also paste the
   **Bot User OAuth Token** (`xoxb-...`).
7. Try `/forge summarize yesterday's incidents`. Slack shows "Working on it" with a run link right away, and the final
   answer is posted to the channel when the run finishes (long output is truncated with a link to the full run).

Requests whose signature is wrong or whose timestamp is more than 5 minutes old are rejected.

## Trying it without real credentials

- **Test** on a trigger fires it once with a sample payload (results are not posted to GitHub or Slack).
- Send a signed GitHub event yourself:

```bash
BODY='{"action":"opened","repository":{"full_name":"o/r"},"sender":{"login":"me"},"issue":{"number":1,"title":"Hi","body":""}}'
SIG="sha256=$(printf '%s' "$BODY" | openssl dgst -sha256 -hmac "$SECRET" | sed 's/^.* //')"
curl -i "$URL" -H "X-GitHub-Event: issues" -H "X-Hub-Signature-256: $SIG" -H "Content-Type: application/json" -d "$BODY"
```
