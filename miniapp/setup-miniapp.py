#!/usr/bin/env python3
"""Interactive setup wizard for the Aside Telegram Mini App.

Run:  python3 miniapp/setup-miniapp.py

Installs the Mini App server alongside the existing chat bridge. The only
real prerequisite is Node 20+; everything else -- the cloudflared tunnel
binary included -- is fetched by the server itself at runtime.

Idempotent: safe to re-run any time to reconfigure. This never touches
setup.py, install.sh, or the running bridge.
"""
import argparse
import json
import os
import plistlib
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Optional

MINIAPP_DIR = os.path.dirname(os.path.realpath(__file__))
BRIDGE_DIR = os.path.dirname(MINIAPP_DIR)
# setup.py writes config.json into the checkout; the ~/.aside path is the
# older documented home. Search both so running this wizard standalone
# finds the config a wizard install actually produced.
CONFIG_CANDIDATES = (
    os.path.join(BRIDGE_DIR, "config.json"),
    os.path.expanduser("~/.aside/u/0/telegram-bridge/config.json"),
)
LABEL = "com.aside.miniapp"
PLIST_PATH = os.path.expanduser("~/Library/LaunchAgents/%s.plist" % LABEL)

MIN_NODE_MAJOR = 20
DEFAULT_PORT = 8790

BOLD = "\033[1m"
DIM = "\033[2m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
RED = "\033[31m"
RESET = "\033[0m"


@dataclass(frozen=True)
class MigrationResult:
    ok: bool
    changed: bool
    restored: bool
    message: str
    backup_dir: Optional[str]


def say(msg=""):
    print(msg)


def ok(msg):
    print("%s  ✓ %s%s" % (GREEN, msg, RESET))


def warn(msg):
    print("%s  ! %s%s" % (YELLOW, msg, RESET))


def fail(msg):
    print("%s  ✗ %s%s" % (RED, msg, RESET))


def step(n, total, msg):
    print("\n%s[%d/%d] %s%s" % (BOLD, n, total, msg, RESET))


def ask(prompt, default=None):
    suffix = " [%s]" % default if default else ""
    try:
        v = input("%s%s%s%s: " % (BOLD, prompt, suffix, RESET)).strip()
    except (EOFError, KeyboardInterrupt):
        say("\nsetup cancelled.")
        sys.exit(1)
    return v or (default or "")


def ask_yes_no(prompt, default=False):
    hint = "Y/n" if default else "y/N"
    answer = ask("%s (%s)" % (prompt, hint), "")
    if not answer:
        return default
    return answer.strip().lower().startswith("y")


# --- step 1: Node --------------------------------------------------------


def check_node():
    """Node 20+ is the one thing we cannot install for the user."""
    node = shutil.which("node")
    if not node:
        fail("Node.js is not installed.")
        say()
        say("  Install it, then re-run this script:")
        say("    %s- Homebrew:  brew install node%s" % (DIM, RESET))
        say("    %s- Installer: https://nodejs.org/ (LTS)%s" % (DIM, RESET))
        sys.exit(1)

    try:
        raw = subprocess.check_output([node, "--version"], text=True).strip()
        major = int(raw.lstrip("v").split(".")[0])
    except Exception:
        warn("could not read the Node version; continuing anyway")
        return node

    if major < MIN_NODE_MAJOR:
        fail("Node %s is too old -- the server needs %d or newer."
             % (raw, MIN_NODE_MAJOR))
        say("    %sbrew upgrade node%s   (or reinstall from nodejs.org)"
            % (DIM, RESET))
        sys.exit(1)

    ok("Node %s" % raw)
    return node


# --- step 2: build -------------------------------------------------------


def build():
    npm = shutil.which("npm")
    if not npm:
        fail("npm not found even though Node is installed.")
        sys.exit(1)

    for args, label in (
        (["install"], "installing dependencies"),
        (["run", "build"], "building the server and web app"),
    ):
        say("  %s%s...%s" % (DIM, label, RESET))
        proc = subprocess.run([npm] + args, cwd=MINIAPP_DIR)
        if proc.returncode != 0:
            fail("`npm %s` failed" % " ".join(args))
            sys.exit(1)
    ok("built")


# --- step 3: config ------------------------------------------------------


def discover_config():
    """The bridge config this machine actually has, or None."""
    env = os.environ.get("MINIAPP_CONFIG")
    if env:
        return os.path.expanduser(env)
    for candidate in CONFIG_CANDIDATES:
        if os.path.exists(candidate):
            return candidate
    return None


def load_config(path):
    if not os.path.exists(path):
        fail("bridge config not found at %s" % path)
        say("  The Mini App reuses the chat bridge's bot token and chat id,")
        say("  so the bridge has to be set up first:")
        say("    %spython3 %s%s"
            % (DIM, os.path.join(BRIDGE_DIR, "setup.py"), RESET))
        say("  Looked in:")
        for candidate in CONFIG_CANDIDATES:
            say("    %s%s%s" % (DIM, candidate, RESET))
        sys.exit(1)
    with open(path) as fh:
        return json.load(fh)


def desired_miniapp_section(config_path, current_section, repository_root):
    """Preserve Mini App choices while rebinding state to this checkout."""

    del config_path  # Kept in the public builder signature for symmetry.
    section = dict(current_section) if isinstance(current_section, dict) else {}
    section["state_dir"] = os.path.abspath(repository_root)
    return section


def build_service_plist(config_path, section, repository_root, node_path):
    """Build the complete launchd plist without reading config secrets."""

    repository_root = os.path.abspath(repository_root)
    config_path = os.path.abspath(config_path)
    miniapp_dir = os.path.join(repository_root, "miniapp")
    state_dir = os.path.expanduser(
        str(section.get("state_dir") or repository_root)
    )
    return {
        "Label": LABEL,
        "ProgramArguments": [
            node_path,
            os.path.join(miniapp_dir, "server", "dist", "index.js"),
        ],
        "EnvironmentVariables": {
            "MINIAPP_CONFIG": config_path,
            "MINIAPP_PORT": str(section.get("port", DEFAULT_PORT)),
            "PATH": (
                "/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin:"
                "/usr/sbin:/sbin"
            ),
        },
        "RunAtLoad": True,
        "KeepAlive": True,
        "ThrottleInterval": 10,
        "StandardOutPath": os.path.join(state_dir, "miniapp.out.log"),
        "StandardErrorPath": os.path.join(state_dir, "miniapp.err.log"),
        "WorkingDirectory": miniapp_dir,
    }


def _atomic_write_bytes(path, value, mode=0o600):
    parent = os.path.dirname(os.path.abspath(path))
    os.makedirs(parent, mode=0o700, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(
        prefix=".%s." % os.path.basename(path),
        dir=parent,
    )
    try:
        os.fchmod(descriptor, mode)
        with os.fdopen(descriptor, "wb") as handle:
            descriptor = -1
            handle.write(value)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        os.chmod(path, mode)
    except Exception:
        if descriptor >= 0:
            os.close(descriptor)
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def atomic_write_json(path, value, mode=0o600):
    encoded = (json.dumps(value, indent=2) + "\n").encode("utf-8")
    _atomic_write_bytes(path, encoded, mode=mode)


def atomic_write_plist(path, value, mode=0o600):
    _atomic_write_bytes(path, plistlib.dumps(value), mode=mode)


def write_config(path, config):
    """Atomically rewrite owner-only bridge configuration."""

    atomic_write_json(path, config)


def migrate_paths(
    config_path,
    plist_path,
    repository_root,
    node_path,
    reload_service,
    check_health,
    backup_root,
):
    """Atomically rebind installed paths and restore exact bytes on failure."""

    config_path = os.path.abspath(os.path.expanduser(config_path))
    plist_path = os.path.abspath(os.path.expanduser(plist_path))
    repository_root = os.path.abspath(os.path.expanduser(repository_root))
    node_path = os.path.abspath(os.path.expanduser(node_path))
    backup_root = os.path.abspath(os.path.expanduser(backup_root))
    entry = os.path.join(
        repository_root, "miniapp", "server", "dist", "index.js"
    )

    if not os.path.isfile(node_path) or not os.access(node_path, os.X_OK):
        return MigrationResult(
            False, False, False, "Node executable is missing", None
        )
    if not os.path.isfile(entry):
        return MigrationResult(
            False, False, False, "Mini App build output is missing", None
        )

    try:
        with open(config_path, "rb") as handle:
            old_config_bytes = handle.read()
        config = json.loads(old_config_bytes.decode("utf-8"))
        with open(plist_path, "rb") as handle:
            old_plist_bytes = handle.read()
        old_plist = plistlib.loads(old_plist_bytes)
    except (OSError, UnicodeError, ValueError, plistlib.InvalidFileException):
        return MigrationResult(
            False,
            False,
            False,
            "Existing config or plist is missing or malformed",
            None,
        )
    if not isinstance(config, dict) or not isinstance(old_plist, dict):
        return MigrationResult(
            False,
            False,
            False,
            "Existing config or plist has the wrong top-level type",
            None,
        )

    current_section = config.get("miniapp")
    if current_section is not None and not isinstance(current_section, dict):
        return MigrationResult(
            False, False, False, "Mini App config section is malformed", None
        )
    desired_section = desired_miniapp_section(
        config_path, current_section or {}, repository_root
    )
    desired_config = dict(config)
    desired_config["miniapp"] = desired_section
    desired_plist = build_service_plist(
        config_path, desired_section, repository_root, node_path
    )

    if desired_config == config and desired_plist == old_plist:
        return MigrationResult(
            True, False, False, "Mini App paths are already current", None
        )

    os.makedirs(backup_root, mode=0o700, exist_ok=True)
    os.chmod(backup_root, 0o700)
    backup_dir = tempfile.mkdtemp(prefix="miniapp-paths-", dir=backup_root)
    os.chmod(backup_dir, 0o700)
    _atomic_write_bytes(
        os.path.join(backup_dir, "config.json.original"), old_config_bytes
    )
    _atomic_write_bytes(
        os.path.join(backup_dir, "service.plist.original"), old_plist_bytes
    )

    failure = None
    try:
        atomic_write_json(config_path, desired_config)
        atomic_write_plist(plist_path, desired_plist)
        if not reload_service():
            failure = "service reload failed"
        elif not check_health(int(desired_section.get("port", DEFAULT_PORT))):
            failure = "health check failed"
    except Exception as exc:  # noqa: BLE001 - restore across I/O boundaries
        failure = "migration write failed: %s" % type(exc).__name__

    if failure is None:
        return MigrationResult(
            True,
            True,
            False,
            "Mini App paths migrated and health verified",
            backup_dir,
        )

    restored = False
    try:
        _atomic_write_bytes(config_path, old_config_bytes)
        _atomic_write_bytes(plist_path, old_plist_bytes)
        reload_service()
        restored = True
    except Exception:  # noqa: BLE001 - result reports failed restoration
        restored = False
    return MigrationResult(
        False,
        True,
        restored,
        "%s; original files %s"
        % (failure, "restored" if restored else "could not be fully restored"),
        backup_dir,
    )


def configure(config_path):
    config = load_config(config_path)
    section = config.get("miniapp") or {}

    port = ask("Port for the Mini App server",
               str(section.get("port", DEFAULT_PORT)))
    try:
        port = int(port)
    except ValueError:
        port = DEFAULT_PORT

    say()
    say("  %sA Telegram Mini App must be reachable over public HTTPS."
        % DIM)
    say("  cloudflared gives you that with no account and no extra")
    say("  install -- the server downloads it on first run.%s" % RESET)
    use_tunnel = ask_yes_no("  Manage a cloudflared tunnel automatically?",
                            default=True)

    cloudflared_path = ""
    if use_tunnel:
        existing = shutil.which("cloudflared")
        say()
        say("  %sThe server downloads a pinned cloudflared release and"
            % DIM)
        say("  verifies its SHA-256 against a checksum vendored in this")
        say("  repo before running it. If you would rather run a copy you")
        say("  installed and trust yourself, point it at one.%s" % RESET)
        if existing:
            if ask_yes_no("  Use the cloudflared already at %s?" % existing,
                          default=True):
                cloudflared_path = existing
        else:
            answer = ask("  Path to your own cloudflared (blank to let the"
                         " server fetch one)", "")
            if answer:
                cloudflared_path = os.path.expanduser(answer)
                if not os.path.exists(cloudflared_path):
                    warn("  %s does not exist -- the server will fetch its"
                         " own instead" % cloudflared_path)
                    cloudflared_path = ""

    say()
    say("  %sThe menu button is the 'Aside' entry next to the message box"
        % DIM)
    say("  in your chat with the bot. Turning this on re-points it at the")
    say("  tunnel URL each time that URL changes.%s" % RESET)
    warn("  Your bot is live. This rewrites its menu button.")
    say("  %sThe menu button is bot-wide, so ONE bot can point at ONE Mini"
        % DIM)
    say("  App at a time: turning this on here takes the button away from")
    say("  any other machine or dev instance using the same bot token. If")
    say("  you want two, make a second bot with @BotFather.%s" % RESET)
    say("  %sSay no only if this bot's button is already pointed somewhere"
        % DIM)
    say("  you want to keep -- a quick tunnel's URL changes on every")
    say("  restart, and only this setting re-points the button when it"
        " does.%s" % RESET)
    auto_menu = ask_yes_no("  Register the menu button automatically?",
                           default=True)

    section.update({
        "port": port,
        "tunnel": "cloudflared" if use_tunnel else "none",
        "auto_register_menu": bool(auto_menu),
    })
    if cloudflared_path:
        section["cloudflared_path"] = cloudflared_path
    else:
        section.pop("cloudflared_path", None)
    section = desired_miniapp_section(config_path, section, BRIDGE_DIR)
    config["miniapp"] = section
    write_config(config_path, config)

    ok("config written to %s" % config_path)
    if not auto_menu:
        say("    %smenu registration stays off; flip"
            " miniapp.auto_register_menu to true when you want it%s"
            % (DIM, RESET))
    return section


# --- step 4: launchd -----------------------------------------------------


def reload_launchd_service(plist_path=PLIST_PATH):
    """Reload the Mini App service without modifying its plist."""

    uid = os.getuid()
    subprocess.run(["launchctl", "bootout", "gui/%d/%s" % (uid, LABEL)],
                   capture_output=True)
    proc = subprocess.run(
        ["launchctl", "bootstrap", "gui/%d" % uid, plist_path],
        capture_output=True, text=True)
    if proc.returncode != 0:
        proc = subprocess.run(["launchctl", "load", "-w", plist_path],
                              capture_output=True, text=True)
    return proc.returncode == 0


def install_service(config_path, section):
    node = shutil.which("node")
    if not node:
        fail("Node executable is missing")
        sys.exit(1)
    plist = build_service_plist(config_path, section, BRIDGE_DIR, node)
    entry = plist["ProgramArguments"][1]
    if not os.path.exists(entry):
        fail("build output missing at %s" % entry)
        sys.exit(1)

    atomic_write_plist(PLIST_PATH, plist)
    ok("wrote %s" % PLIST_PATH)
    if not reload_launchd_service(PLIST_PATH):
        fail("launchctl could not start the service")
        sys.exit(1)
    ok("service loaded (%s)" % LABEL)
    return plist["StandardOutPath"], plist["StandardErrorPath"]


# --- step 5: health ------------------------------------------------------


def health_check(port, attempts=20):
    url = "http://127.0.0.1:%d/api/health" % port
    for _ in range(attempts):
        try:
            with urllib.request.urlopen(url, timeout=2) as res:
                if json.load(res).get("ok"):
                    ok("server healthy on port %d" % port)
                    return True
        except (urllib.error.URLError, OSError, ValueError):
            pass
        time.sleep(1)
    fail("server did not answer %s" % url)
    return False


def find_tunnel_url(log_paths, attempts=30):
    """The server logs the tunnel hostname once cloudflared reports it."""
    for _ in range(attempts):
        for path in log_paths:
            try:
                with open(path) as fh:
                    for line in reversed(fh.read().splitlines()):
                        if "trycloudflare.com" in line:
                            start = line.find("https://")
                            if start == -1:
                                continue
                            url = line[start:].split()[0].strip('"\',')
                            return url
            except OSError:
                pass
        time.sleep(2)
    return None


# --- main ----------------------------------------------------------------


def main(argv=None):
    parser = argparse.ArgumentParser(add_help=True)
    parser.add_argument("--migrate-paths", action="store_true")
    parser.add_argument("--config")
    parser.add_argument("--repository-root")
    args = parser.parse_args(argv)

    if args.migrate_paths:
        if not args.config or not args.repository_root:
            parser.error("--migrate-paths requires --config and --repository-root")
        node = shutil.which("node") or ""
        result = migrate_paths(
            config_path=args.config,
            plist_path=PLIST_PATH,
            repository_root=args.repository_root,
            node_path=node,
            reload_service=lambda: reload_launchd_service(PLIST_PATH),
            check_health=health_check,
            backup_root=os.path.join(args.repository_root, "backups"),
        )
        print(
            "%s: %s%s"
            % (
                "ok" if result.ok else "failed",
                result.message,
                "; backup=%s" % result.backup_dir
                if result.backup_dir else "",
            )
        )
        return 0 if result.ok else 1

    say("%s\n  Aside Telegram Mini App -- setup\n%s" % (BOLD, RESET))
    total = 5

    step(1, total, "Checking prerequisites")
    check_node()
    # Resolved before the build, not after: `npm install && npm run build`
    # takes minutes, and finding out afterwards that there is no bridge
    # config to read wastes all of it.
    config_path = discover_config()
    if config_path and os.path.exists(config_path):
        ok("bridge config at %s" % config_path)
    else:
        config_path = ask("  Bridge config path", CONFIG_CANDIDATES[0])
        config_path = os.path.expanduser(config_path)
        load_config(config_path)  # exits with guidance if it is not there

    step(2, total, "Building")
    build()

    step(3, total, "Configuring")
    section = configure(config_path)

    step(4, total, "Installing the background service")
    out_log, err_log = install_service(config_path, section)

    step(5, total, "Verifying")
    port = section.get("port", DEFAULT_PORT)
    if not health_check(port):
        say("  logs: %s" % err_log)
        sys.exit(1)

    url = None
    if section.get("tunnel") == "cloudflared":
        say("  %swaiting for the tunnel (first run downloads cloudflared)"
            "...%s" % (DIM, RESET))
        url = find_tunnel_url([out_log, err_log])
        if url:
            ok("public URL: %s" % url)
        else:
            warn("no tunnel URL yet -- check %s in a moment" % err_log)

    say()
    say("%s  Done.%s" % (GREEN + BOLD, RESET))
    say()
    if section.get("tunnel") != "cloudflared":
        say("  The tunnel is off, so there is no public HTTPS URL and")
        say("  Telegram cannot reach this server yet. The Mini App needs")
        say("  one: re-run this wizard and say yes to the tunnel, or point")
        say("  miniapp.tunnel at your own.")
    elif section.get("auto_register_menu"):
        if url:
            say("  Open Telegram and tap the %sAside%s button next to the"
                % (BOLD, RESET))
            say("  message box in your chat with the bot.")
            say("  %sIf it is not there yet, give it a few seconds and"
                % DIM)
            say("  reopen the chat -- Telegram caches the button.%s" % RESET)
        else:
            say("  The tunnel had not come up yet, so the menu button is")
            say("  not registered. The server does it by itself as soon as")
            say("  the URL appears -- watch for it with:")
            say("    %sbridgemon miniapp logs%s" % (DIM, RESET))
    else:
        say("  Menu registration is off, so there is no %sAside%s button"
            % (BOLD, RESET))
        say("  in Telegram yet. To get one, either:")
        say("    - set %sminiapp.auto_register_menu%s to true in %s,"
            % (BOLD, RESET, config_path))
        say("      then %sbridgemon miniapp restart%s  (recommended --"
            % (BOLD, RESET))
        say("      it re-points the button when the URL rotates)")
        say("    - or set it by hand in @BotFather → Bot Settings →")
        say("      Menu Button%s" % (":" if url else ""))
        if url:
            say("        %s%s%s" % (BOLD, url, RESET))
            say("      %s(this URL dies on the next restart)%s"
                % (DIM, RESET))
    say()
    say("  %sNote: a quick tunnel's URL changes on every restart. The"
        % DIM)
    say("  server re-registers the menu button when it does (if that is")
    say("  enabled). For a URL that never moves, set up a named tunnel")
    say("  with your own domain and point miniapp at it instead.%s" % RESET)
    say()
    say("  Manage it from the same place as the bridge:")
    say("    %sbridgemon status            both services at a glance%s"
        % (DIM, RESET))
    say("    %sbridgemon miniapp logs      what the server is saying%s"
        % (DIM, RESET))
    say("    %sbridgemon miniapp restart   after a config change%s"
        % (DIM, RESET))
    say("    %sbridgemon update            pull, rebuild, restart both%s"
        % (DIM, RESET))
    say()
    say("  %slogs:    %s%s" % (DIM, err_log, RESET))
    say("  %srestart: launchctl kickstart -k gui/%d/%s%s"
        % (DIM, os.getuid(), LABEL, RESET))
    say()
    return 0


if __name__ == "__main__":
    sys.exit(main())
