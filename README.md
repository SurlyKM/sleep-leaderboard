# Sleep Leaderboard 😴

A friendly competition tracking nightly Garmin sleep scores, weekly averages, and activity stats across a group of friends.
---

## Want to join?

You will need a Garmin watch that tracks sleep and a Garmin Connect account.

### Step 1 - Install Python

Download and install Python 3.9 or later from [python.org](https://www.python.org/downloads/).

During installation on Windows, check the box that says **Add Python to PATH**.

### Step 2 - Download the setup script

Download [`garmin_setup_friend.py`](garmin_setup_friend.py) from this repo (click the file, then the download button).

### Step 3 - Install the Garmin library

Open Terminal (Mac) or Command Prompt (Windows) and run:

```
pip install garminconnect
```

### Step 4 - Run the setup script

In the same terminal, navigate to where you downloaded the file and run:

```
python garmin_setup_friend.py
```

It will ask for your Garmin email, password, and MFA code. Your password is used once to log in and is never saved or sent anywhere.

### Step 5 - Send your token file

The script generates a file called `yourname_garmin_token.txt`. Send that file to the leaderboard admin.

It contains no password or email address, just a secure session token that lets the leaderboard read your sleep data.

---

## What data is shown?

- Last night's sleep score (the Garmin 0-100 score)
- 7-day rolling average and sparkline
- Sleep stage breakdown (deep, REM, light, awake)
- Heart rate, HRV, stress, SpO2, respiration overnight
- Weekly steps, running, cycling, and swimming

## How often does it update?

Automatically every morning. The leaderboard admin controls when it refreshes.

## Can I remove myself?

Yes, just ask the admin. Your token will be removed and your data will no longer be fetched or displayed.
