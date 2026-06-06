"""
Garmin Sleep Leaderboard - Friend Token Setup
=============================================
Run this on your own Windows or Mac to securely generate your Garmin token.
Your email and password never leave your machine.

Requirements: Python 3.9+ (https://www.python.org/downloads/)

First time only - install the Garmin library:
    pip install garminconnect

Then run:
    python garmin_setup_friend.py

You will be given a short text string to send to the leaderboard admin.
That string contains NO password or email - just a secure session token.
"""

import base64
import getpass
import json
import sys
import tempfile
from pathlib import Path


def check_dependency():
    try:
        from garminconnect import Garmin
        return Garmin
    except ImportError:
        print("\nMissing dependency. Please run this first:\n")
        print("    pip install garminconnect\n")
        sys.exit(1)


def main():
    print("=" * 55)
    print("  Garmin Sleep Leaderboard - Friend Setup")
    print("=" * 55)
    print()
    print("This script logs into Garmin on YOUR machine and")
    print("gives you a token string to send to the leaderboard admin.")
    print("Your password is used once and never saved or sent anywhere.")
    print()

    Garmin = check_dependency()

    name = input("What name should appear on the leaderboard? ").strip()
    if not name:
        print("Name cannot be empty.")
        sys.exit(1)

    print()
    email = input("Garmin email address: ").strip()
    password = getpass.getpass("Garmin password (hidden): ")

    print()
    print("Logging in to Garmin Connect...")
    print("(If you have MFA enabled, you will be prompted for your code.)")
    print()

    try:
        with tempfile.TemporaryDirectory() as tmp:
            token_dir = Path(tmp) / name
            token_dir.mkdir()

            garmin = Garmin(email=email, password=password)
            garmin.login()
            garmin.garth.dump(str(token_dir))

            # Find the token file
            token_file = token_dir / "garmin_tokens.json"
            if not token_file.exists():
                # Fall back to any json file in the folder
                candidates = list(token_dir.glob("*.json"))
                if not candidates:
                    print("Could not find saved token file. Login may have failed.")
                    sys.exit(1)
                token_file = candidates[0]

            token_data = token_file.read_bytes()
            encoded = base64.b64encode(token_data).decode("ascii")

    except Exception as e:
        print(f"Login failed: {e}")
        print()
        print("Check your email, password, and MFA code and try again.")
        sys.exit(1)

    # Write to file
    out_file = Path(f"{name}_garmin_token.txt")
    out_file.write_text(f"{name}\n{encoded}\n")

    print()
    print("=" * 55)
    print("  Success!")
    print("=" * 55)
    print()
    print(f"Your token has been saved to:  {out_file.name}")
    print()
    print("Send that file (or its contents) to the leaderboard admin.")
    print("It contains NO password or email - just a secure session token.")
    print()
    print("The file contains two lines:")
    print(f"  Line 1: your leaderboard name  ({name})")
    print(f"  Line 2: your token string       ({len(encoded)} characters)")
    print()
    print("Done. You can delete this script and the .txt file afterwards.")


if __name__ == "__main__":
    main()
