# ICAI batch alert bot (Mumbai city, Vasai, Sikar)

A small robot that runs **free on GitHub** (you don't need a computer switched on) and
messages you on **Telegram** when a seat opens at the **ICAI Vasai** centre for:

* **Adv ITT** (AICITSS - Advanced Information Technology), batches like `AICITSSAdvITT__VASAI_98`
* **MCS** (Advanced (ICITSS) MCS Course, normal weekday batches), batches like `AdvICITSSMCS__VASAI_81`

It checks the public ICAI batch calendar
<https://www.icaionlineregistration.org/LaunchBatchDetail.aspx> about every 5 minutes, the same
way you would by hand: Western → VASAI → course → *Get List*.

## What messages you'll get

| Situation | Message |
|---|---|
| A **new batch** appears with seats, or a full batch **gets seats again** | 4 loud messages, 3 seconds apart, starting with **🚨 VASAI SEAT OPEN 🚨**, plus an optional phone call |
| A new batch appears but it's already full | 1 normal message |
| Seats go down, a batch fills up, dates change, a batch is removed | 1 **silent** message (no sound) |
| The ICAI site can't be read for 6 checks in a row (~30 min) | 1 warning, then 1 quiet "recovered" message later |
| Very first run | "Bot is live, watching N Adv ITT + M MCS batches at Vasai" plus any batches that have seats right now |

---

## Setup (about 15 minutes, works on a phone or a PC)

### Step 1: Make your Telegram bot
1. In Telegram, search for **@BotFather** (the one with the blue tick) and open it.
2. Send `/newbot`. Pick any name, then a username that ends in `bot` (e.g. `harsh_vasai_seat_bot`).
3. BotFather replies with a **token** that looks like `1234567890:AAH...xyz`. Copy it and keep it private.
4. Open your new bot (BotFather gives a link `t.me/...`) and press **Start**, or send it `hi`.
   *The bot can only message you after you've messaged it first.*

### Step 2: Get your chat ID
Use either option:
* **Easy:** in Telegram, open **@userinfobot** and press Start. It replies with your `Id`, a number like `123456789`. That number is your chat ID.
* **Or:** in a browser, open `https://api.telegram.org/bot<TOKEN>/getUpdates` (paste your token in place of `<TOKEN>`, and keep the word `bot` in front of it).
  Find `"chat":{"id":123456789` and copy that number. If you see `"result":[]`, send your bot another message and reload the page.

### Step 3: Make a GitHub account
Go to <https://github.com/signup> and create a free account. Verify your email.

### Step 4: Make a repository and upload the files
1. Tap **+** (top right) → **New repository**.
2. Name it something like `vasai-alert-bot`.
3. Choose **Public**. This matters: GitHub Actions minutes are **unlimited and free on public repos**.
   A private repo gets only 2,000 free minutes a month, and checking every 5 minutes uses about 8,600, so it would stop working.
   The repo only holds public batch data. Your Telegram token stays hidden in *Secrets*, never in the files.
4. Tick **Add a README file** → **Create repository**.
5. **On a PC:** unzip `vasai-alert-bot.zip`, then on the repo page click **Add file → Upload files** and drag in **everything**,
   including the `.github` folder (it can be hidden: on Mac press Cmd+Shift+. in Finder; on Windows tick View → Hidden items). Click **Commit changes**.
   **On a phone (or if `.github` won't upload):** upload `watch.py`, `requirements.txt` and `README.md` with *Upload files*. Then tap
   **Add file → Create new file**, type the name exactly as `.github/workflows/watch.yml` (the slashes create the folders),
   paste in the contents of `watch.yml` from the zip, and **Commit changes**.
6. Check that the repo shows `watch.py`, `requirements.txt`, and a `.github/workflows/watch.yml` file.

### Step 5: Add your secrets
Repo → **Settings** → **Secrets and variables** → **Actions** → **New repository secret**. Add these:

| Name (type exactly) | Value |
|---|---|
| `TELEGRAM_BOT_TOKEN` | the token from BotFather |
| `TELEGRAM_CHAT_ID` | your chat ID number |
| `CALLMEBOT_USER` *(optional)* | your Telegram username, e.g. `@harsh123`, for a **voice call** on seat alerts |

To use the optional voice call, you first have to allow CallMeBot to call you: open <https://api2.callmebot.com/txt/auth.php>
and log in with Telegram, or send `/start` to **@CallMeBot_txtbot**. Your Telegram account needs a username
(Settings → Username). CallMeBot is a free third-party service, so it can be slow or limited at times.

### Step 6: Turn on Actions and send a test
1. Repo → **Actions** tab. If you see a button like *"I understand my workflows, go ahead and enable them"*, tap it.
2. Click **Vasai batch watch** on the left → **Run workflow** → tick **"Send a SAMPLE alert…"** → **Run workflow**.
3. Within about a minute you should get 4 messages marked **[TEST ALERT – not real]** and one silent "Test finished" message.
   *Tip:* in Telegram, open the bot chat → ⋮ / bot name → **Notifications** → choose a loud custom sound, and make sure the
   chat isn't muted. On Android, also allow Telegram to override *Do Not Disturb* if you want night alerts.
4. Run it once more **without** ticking the box. That's the real first run: you get **"✅ Bot is live…"**.
   From then on it runs by itself every ~5 minutes.

To see what it saw, open `history.csv` in the repo (times are IST). `state.json` holds the latest snapshot.

---

## Good to know
* **Timing isn't exact.** GitHub's "every 5 minutes" schedule often starts runs late (5–15+ minutes at busy times), and very
  occasionally skips one. That's fine for a heads-up, but popular batches can fill in under an hour, so act fast when it rings.
  You can tap **Run workflow** any time for an instant check.
* **The 60-day rule.** GitHub pauses scheduled workflows in a repo that has had no activity for 60 days. The bot commits a
  small `heartbeat.txt` change once a day to keep it active. If GitHub ever emails you that the workflow was disabled,
  go to Actions → Vasai batch watch → **Enable workflow**.
* **To stop the bot:** Actions → Vasai batch watch → ⋯ → **Disable workflow**.
* **It's polite:** one check every 5 minutes is about 5 page loads, with a clear User-Agent and slow retries. It only reads the
  public page and never logs in or books anything. **You still register yourself** at <https://www.icaionlineregistration.org/>.
* **Weekend MCS** is a separate course on the site ("Advanced (ICITSS) MCS Course - Weekend") and is **not** watched.
  If a batch in the normal MCS list looks like a weekend one (the word weekend/Sat/Sun, or longer than 35 days), the alert
  marks it "⚠️ possibly weekend".
* **"Registered" numbers** come from data the page itself carries. Seats left + registered usually adds up to the batch size,
  but ICAI doesn't label that field, so treat it as a guide.
* If ICAI changes the page layout, you'll get the "couldn't read the page" warning after ~30 minutes instead of silence.

## Run it on a computer (optional)
```bash
pip install -r requirements.txt
python watch.py               # no secrets set → messages are printed instead of sent
TEST_ALERT=1 python watch.py  # print the sample alert
```
