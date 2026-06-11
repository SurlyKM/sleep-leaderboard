"""
Garmin Sleep Leaderboard - Friend Token Setup
=============================================
Run this on your own Windows or Mac to securely generate your Garmin token.
Your email and password never leave your machine.

Requirements: Python 3.9+ (https://www.python.org/downloads/)

Just run:
    python garmin_setup_friend.py

No admin rights needed. Any packages installed are temporary and
automatically cleaned up when the script finishes.
"""

import sys
import os


def bootstrap():
    """
    If garminconnect is not installed, create a temporary venv, install it
    there, re-run this script inside the venv, then clean up. Nothing is
    left behind on the machine.
    """
    try:
        from garminconnect import Garmin  # noqa: F401
        return  # already available, nothing to do
    except ImportError:
        pass

    # Check we haven't already been re-invoked inside a temp venv
    if os.environ.get("_GARMIN_SETUP_VENV"):
        print("Could not import garminconnect even after installing. "
              "Try running:  pip install garminconnect  and try again.")
        sys.exit(1)

    import shutil
    import subprocess
    import tempfile

    print("garminconnect not found - creating a temporary environment...")

    venv_dir = tempfile.mkdtemp(prefix="garmin_setup_")
    try:
        # Create venv
        subprocess.run(
            [sys.executable, "-m", "venv", venv_dir],
            check=True, capture_output=True,
        )

        # Path to venv python
        if sys.platform == "win32":
            venv_python = os.path.join(venv_dir, "Scripts", "python.exe")
        else:
            venv_python = os.path.join(venv_dir, "bin", "python")

        # Install garminconnect quietly
        print("Installing garminconnect (temporary, will be removed after)...")
        subprocess.run(
            [venv_python, "-m", "pip", "install", "garminconnect", "-q",
             "--disable-pip-version-check"],
            check=True,
        )

        # Re-run this script inside the venv
        env = os.environ.copy()
        env["_GARMIN_SETUP_VENV"] = "1"
        result = subprocess.run(
            [venv_python, os.path.abspath(__file__)] + sys.argv[1:],
            env=env,
        )
        sys.exit(result.returncode)

    finally:
        shutil.rmtree(venv_dir, ignore_errors=True)


bootstrap()

# ---------------------------------------------------------------------------
# From here on, garminconnect is available
# ---------------------------------------------------------------------------

import base64
import getpass
import json
import tempfile
from pathlib import Path
from garminconnect import Garmin


def main():
    print()
    print("=" * 55)
    print("  Garmin Sleep Leaderboard - Friend Setup")
    print("=" * 55)
    print()
    print("This script logs into Garmin on YOUR machine and")
    print("gives you a token string to send to the leaderboard admin.")
    print("Your password is used once and never saved or sent anywhere.")
    print()

    name = input("What name should appear on the leaderboard? ").strip()
    if not name:
        print("Name cannot be empty.")
        sys.exit(1)

    print()
    email = input("Garmin email address: ").strip()
    password = getpass.getpass("Garmin password (hidden): ")

    print()
    print("Logging in to Garmin Connect...")
    print("(If you have MFA enabled you will be prompted for your code.)")
    print()

    try:
        with tempfile.TemporaryDirectory() as tmp:
            token_dir = Path(tmp) / name
            token_dir.mkdir()

            garmin = Garmin(
                email=email,
                password=password,
                prompt_mfa=lambda: input("MFA code from email/app: ").strip(),
            )
            garmin.login(str(token_dir))

            # Find the saved token file
            token_file = token_dir / "garmin_tokens.json"
            if not token_file.exists():
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

    # Write output file next to this script
    out_file = Path(__file__).parent / f"{name}_garmin_token.txt"
    out_file.write_text(f"{name}\n{encoded}\n")

    print()
    print("=" * 55)
    print("  Success!")
    print("=" * 55)
    print()
    print(f"Your token has been saved to:  {out_file.name}")
    print()
    print("Send that file to the leaderboard admin.")
    print("It contains NO password or email address.")
    print()
    print(f"  Line 1: your name    ({name})")
    print(f"  Line 2: token string ({len(encoded)} characters)")
    print()
    print("You can delete this script and the .txt file afterwards.")
    print("Nothing was permanently installed on this machine.")


if __name__ == "__main__":
    main()
