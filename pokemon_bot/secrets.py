import os
import re
import subprocess
from typing import Callable
from urllib.parse import urlparse


def validate_discord_webhook_url(value: str) -> str:
    url = value.strip()
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.hostname != "discord.com":
        raise ValueError("Discord webhook URL must use https://discord.com")
    if not re.fullmatch(r"/api/webhooks/\d+/[^/]+", parsed.path):
        raise ValueError("Discord webhook URL has an invalid path")
    return url


def read_environment_secret(name: str) -> str:
    value = os.environ.get(name, "")
    if not value:
        raise RuntimeError("Environment variable %s is not set" % name)
    return value


def read_keychain_secret(
    service: str,
    account: str,
    runner: Callable[..., subprocess.CompletedProcess] = subprocess.run,
) -> str:
    try:
        result = runner(
            [
                "/usr/bin/security",
                "find-generic-password",
                "-s",
                service,
                "-a",
                account,
                "-w",
            ],
            check=True,
            capture_output=True,
            text=True,
        )
    except subprocess.CalledProcessError as error:
        raise RuntimeError(
            "Discord webhook was not found in macOS Keychain for service %s and account %s"
            % (service, account)
        ) from error
    value = result.stdout.strip()
    if not value:
        raise RuntimeError("macOS Keychain returned an empty Discord webhook")
    return value


def store_keychain_secret(service: str, account: str) -> None:
    # Leaving -w as the final argument makes macOS prompt securely. The secret is
    # never passed through this Python process, command arguments, or shell history.
    subprocess.run(
        [
            "/usr/bin/security",
            "add-generic-password",
            "-U",
            "-a",
            account,
            "-s",
            service,
            "-l",
            "Pokemon Card Monitor Discord Webhook",
            "-w",
        ],
        check=True,
    )
