# Event runbook: 13 Nov 2026

One page for the offline event (~20–30 players) at https://clashforces.duckdns.org.
Every command is one paste into **Azure Cloud Shell (Bash)**. `run-command` prints the
VM's output when the command finishes; there is no SSH. Times on the VM are UTC (IST − 5:30).

## Before (afternoon, at least 1 hour before start)

- [ ] **Pause auto-deploy**, so no new `main` restarts the app during the event:

  ```bash
  az vm run-command invoke -g clashforces -n clashforces --command-id RunShellScript --query 'value[0].message' -o tsv --scripts "touch /opt/clashforces/NO_DEPLOY && echo paused"
  ```

- [ ] **Back up now** (also checks the copy's integrity and prints user/match counts):

  ```bash
  az vm run-command invoke -g clashforces -n clashforces --command-id RunShellScript --query 'value[0].message' -o tsv --scripts "/opt/clashforces/backup-now.sh && ls -lh /opt/clashforces/backups | tail -n 3"
  ```

- [ ] **Health check.** Expect `"cf_api":"ok"` and `"running_matches":0`:

  ```bash
  curl -s https://clashforces.duckdns.org/api/health; echo
  ```

- [ ] **Codeforces API status.** Expect `"status":"OK"` within a second or two. Also look at
  https://codeforces.com/contests: if an official round overlaps the event, the API will be
  slow and verdicts will lag.

  ```bash
  curl -s -m 10 -w '\nHTTP %{http_code} in %{time_total}s\n' 'https://codeforces.com/api/user.info?handles=tourist' | tail -c 200
  ```

- [ ] **VM resources** (memory, disk, containers, the last deploys):

  ```bash
  az vm run-command invoke -g clashforces -n clashforces --command-id RunShellScript --query 'value[0].message' -o tsv --scripts "free -m; df -h / | tail -n 1; docker ps --format '{{.Names}} {{.Status}}'; tail -n 5 /opt/clashforces/deploy.log"
  ```

- [ ] Tell players: **press "I submitted" (or `S`) right after submitting on Codeforces.**
  At 30 players it cuts the wait after Codeforces has judged from ~45 s (up to ~2 min) to
  under 10 s (up to ~15 s) ([load test](loadtest.md)).
- [ ] **Have everyone log in before the start.** Logins and match starts also use the
  Codeforces API: 30 people logging in at the same minute takes ~2.5 minutes, and starting
  all matches at once takes ~2 minutes.
- [ ] Tell players a result becomes final **1–2 minutes after the end** (every player gets a
  last check on Codeforces; ~80 s for a 30-player contest).

## During

**Logs** (warnings and errors from the last hour; drop the `grep` to see everything):

```bash
az vm run-command invoke -g clashforces -n clashforces --command-id RunShellScript --query 'value[0].message' -o tsv --scripts "docker logs --since 1h clashforces 2>&1 | grep -E 'WARNING|ERROR' | tail -n 40"
```

**Codeforces is down or very slow.** The match page shows "Codeforces API is not
answering. Timers keep running; results will be confirmed once it's back." Do nothing on
the server:

- Matches still end on time (our clock). Submissions made during the outage still count,
  because only Codeforces' own submission time is used.
- Results show as **pending** and finalise by themselves once the API answers (final
  checks retry every minute). A verdict stuck in TESTING keeps a result pending for up to
  an hour, then it is ignored.
- Don't restart the app and don't recreate matches; neither speeds anything up.

**Verdicts feel slow** (more than a minute): check `"lanes"` in the health check. Numbers
above ~10 mean the Codeforces queue is backed up (usually a slow API). Remind players about
"I submitted".

**Site is down** (page doesn't load, or the uptime issue opens on GitHub): look at the
containers, then restart the app. A restart is safe: running matches are picked up again
from the database; the outage only delays verdicts.

```bash
az vm run-command invoke -g clashforces -n clashforces --command-id RunShellScript --query 'value[0].message' -o tsv --scripts "docker ps -a --format '{{.Names}} {{.Status}}'; free -m | head -n 2; docker logs --tail 20 clashforces 2>&1"
```

```bash
az vm run-command invoke -g clashforces -n clashforces --command-id RunShellScript --query 'value[0].message' -o tsv --scripts "docker restart clashforces caddy && sleep 10 && docker ps --format '{{.Names}} {{.Status}}'"
```

Then run the health check from "Before" again.

**Urgent fix that must ship now** (restarts the app for ~5 s; running matches resume):

```bash
az vm run-command invoke -g clashforces -n clashforces --command-id RunShellScript --query 'value[0].message' -o tsv --scripts "FORCE=1 /opt/clashforces/update.sh; tail -n 5 /opt/clashforces/deploy.log"
```

**Database restore** (last resort; refuses while a match is running). List the backups,
then restore one by name. The script takes a safety backup first.

```bash
az vm run-command invoke -g clashforces -n clashforces --command-id RunShellScript --query 'value[0].message' -o tsv --scripts "ls -lh /opt/clashforces/backups"
```

```bash
az vm run-command invoke -g clashforces -n clashforces --command-id RunShellScript --query 'value[0].message' -o tsv --scripts "/opt/clashforces/restore.sh clashforces-20261113-1200.db.gz"
```

## After

- [ ] **Back up** (the evening's results):

  ```bash
  az vm run-command invoke -g clashforces -n clashforces --command-id RunShellScript --query 'value[0].message' -o tsv --scripts "/opt/clashforces/backup-now.sh"
  ```

- [ ] **Resume auto-deploy.** Anything merged to `main` during the event deploys within 5
  minutes, once no match is running:

  ```bash
  az vm run-command invoke -g clashforces -n clashforces --command-id RunShellScript --query 'value[0].message' -o tsv --scripts "rm -f /opt/clashforces/NO_DEPLOY && echo resumed && tail -n 5 /opt/clashforces/deploy.log"
  ```
