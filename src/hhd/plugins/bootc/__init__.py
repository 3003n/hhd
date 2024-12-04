import json
import logging
import os
import signal
import subprocess
from threading import Thread
from typing import Literal, Sequence

from hhd.i18n import _
from hhd.plugins import Context, HHDPlugin, HHDSettings, load_relative_yaml
from hhd.plugins.conf import Config

logger = logging.getLogger(__name__)

BOOTC_ENABLED = os.environ.get("HHD_BOOTC", "0") == "1"
BOOTC_PATH = os.environ.get("HHD_BOOTC_PATH", "bootc")
BRANCHES = os.environ.get(
    "HHD_BOOTC_BRANCHES", "stable:Stable,testing:Testing,unstable:Unstable"
)
DEFAULT_PREFIX = "> "

BOOTC_STATUS_CMD = [
    BOOTC_PATH,
    "status",
    "--format",
    "json",
]

RPM_OSTREE_RESET = [
    "rpm-ostree",
    "reset",
]

BOOTC_CHECK_CMD = [
    BOOTC_PATH,
    "update",
    "--check",
]

BOOTC_ROLLBACKCMD = [
    BOOTC_PATH,
    "rollback",
]

BOOTC_UPDATE_CMD = [
    BOOTC_PATH,
    "update",
]

SKOPEO_REBASE_CMD = lambda ref: ["skopeo", "inspect", "docker://" + ref]


STAGES = Literal[
    "init",
    "ready",
    "ready_check",
    "ready_updated",
    "ready_reverted",
    "ready_rebased",
    "incompatible",
    "rebase_dialog",
    "loading",
    "loading_rebase",
    "loading_cancellable",
]


def get_bootc_status():
    try:
        output = subprocess.check_output(BOOTC_STATUS_CMD).decode("utf-8")
        return json.loads(output)
    except Exception as e:
        logger.error(f"Failed to get bootc status: {e}")
        return {}


def get_ref_from_status(status: dict | None):
    return (status or {}).get("spec", {}).get("image", {}).get("image", "")


def get_branch(ref: str, branches: dict, fallback: bool = True):
    if ":" not in ref:
        return next(iter(branches))
    curr_tag = ref[ref.rindex(":") + 1 :]

    for branch in branches:
        if branch in curr_tag:
            return branch

    if not fallback:
        return None
    # If no tag, assume it is the first one
    return next(iter(branches))


def get_rebase_refs(ref: str, tags, lim: int = 5, branches: dict = {}):
    logger.info(f"Getting rebase refs for {ref}")
    try:
        output = subprocess.check_output(SKOPEO_REBASE_CMD(ref)).decode("utf-8")
        data = json.loads(output)
        versions = data.get("RepoTags", [])

        for branch in branches:
            same_branch = [v for v in versions if v.startswith(branch) and v != branch]
            same_branch.sort(reverse=True)
            tags[branch] = same_branch[:lim]

        logger.info(f"Finished getting refs")
    except Exception as e:
        logger.error(f"Failed to get rebase refs: {e}")


def run_command_threaded(cmd: list, output: bool = False):
    try:
        return subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE if output else None,
        )
    except Exception as e:
        logger.error(f"Failed to run command: {e}")


def is_incompatible(status: dict):
    if status.get("apiVersion", None) != "org.containers.bootc/v1":
        return True

    if ((status.get("status", None) or {}).get("booted", None) or {}).get(
        "incompatible", False
    ):
        return True

    return False


class BootcPlugin(HHDPlugin):
    def __init__(self) -> None:
        self.name = f"bootc"
        self.priority = 70
        self.log = "bupd"
        self.proc = None
        self.branch_name = None
        self.branch_ref = None
        self.checked_update = False
        self.t = None
        self.t_data = None

        self.branches = {}
        for branch in BRANCHES.split(","):
            name, display = branch.split(":")
            self.branches[name] = display

        self.status = None
        self.enabled = True
        self.state: STAGES = "init"

    def settings(self) -> HHDSettings:
        if self.enabled:
            sets = {"updates": {"bootc": load_relative_yaml("settings.yml")}}

            sets["updates"]["bootc"]["children"]["stage"]["modes"]["rebase"][
                "children"
            ]["branch"]["options"] = self.branches

            return sets
        else:
            return {}

    def open(
        self,
        emit,
        context: Context,
    ):
        self.updated = False

    def get_version(self, s):
        assert self.status
        return (
            (self.status.get("status", {}).get(s, None) or {})
            .get("image", {})
            .get("version", "")
        )

    def _init(self, conf: Config):
        self.status = get_bootc_status()
        ref = self.status.get("spec", {}).get("image", {}).get("image", "")
        img = ref
        if "/" in img:
            img = img[img.rfind("/") + 1 :]

        # Find branch and replace tag
        branch = get_branch(img, self.branches)
        rebased_ver = False
        self.branch_name = branch
        self.branch_ref = None
        if branch:
            if ":" in img:
                tag = img[img.rindex(":") + 1 :]
                if tag != branch:
                    rebased_ver = True
                    self.branch_ref = ref.split(":")[0] + ":" + branch
                img = img[: img.rindex(":") + 1] + branch
        if img:
            conf["updates.bootc.image"] = img

        # If we have a staged update, that will boot first
        s = self.get_version("staged")
        staged = False
        if s:
            conf["updates.bootc.staged"] = DEFAULT_PREFIX + s
            staged = True

        # Check if the user selected rollback
        # Then that will be the default, provided there is a rollback
        rollback = (
            not staged
            and self.status.get("spec", {}).get("bootOrder", None) == "rollback"
        )
        s = self.get_version("rollback")
        if s and rollback:
            s = DEFAULT_PREFIX + s
        else:
            rollback = False
        conf[f"updates.bootc.rollback"] = s

        # Otherwise, the booted version will be the default
        s = self.get_version("booted")
        if s and not rollback and not staged:
            s = DEFAULT_PREFIX + s
        conf[f"updates.bootc.booted"] = s

        conf["updates.bootc.status"] = ""
        self.updated = True

        cached = self.status.get("status", {}).get("booted", {}).get("cachedUpdate", {})
        cached_version = cached.get("version", "") if cached else ""
        cached_img = cached.get("image", {}).get("image", "") if cached else ""
        if "/" in cached_img:
            cached_img = cached_img[cached_img.rfind("/") + 1 :]

        if self.checked_update:
            conf[f"updates.bootc.update"] = _("No update available")
        else:
            conf[f"updates.bootc.update"] = None

        if is_incompatible(self.status):
            conf["updates.bootc.stage.mode"] = "incompatible"
            self.state = "incompatible"
        elif (
            cached_version
            and cached_img == img
            and cached_version != self.get_version("staged")
        ):
            conf["updates.bootc.stage.mode"] = "ready"
            self.state = "ready"
            conf[f"updates.bootc.update"] = cached_version
        elif self.get_version("staged"):
            conf["updates.bootc.stage.mode"] = "ready_updated"
            self.state = "ready_updated"
        elif rebased_ver:
            conf["updates.bootc.stage.mode"] = "ready_rebased"
            self.state = "ready_rebased"
        elif rollback:
            conf["updates.bootc.stage.mode"] = "ready_reverted"
            self.state = "ready_reverted"
        else:
            conf["updates.bootc.stage.mode"] = "ready_check"
            self.state = "ready_check"

    def update(self, conf: Config):

        # Detect reset and avoid breaking the UI
        if conf.get("updates.bootc.stage.mode", None) is None:
            self._init(conf)
            return

        # Try to fill in basic info
        match self.state:
            case "init":
                self._init(conf)
            # Ready
            case (
                "ready"
                | "ready_check"
                | "ready_updated"
                | "ready_reverted"
                | "ready_rebased" as e
            ):
                update = conf.get_action(f"updates.bootc.stage.{e}.update")
                revert = conf.get_action(f"updates.bootc.stage.{e}.revert")
                rebase = conf.get_action(f"updates.bootc.stage.{e}.rebase")
                reboot = conf.get_action(f"updates.bootc.stage.{e}.reboot")

                if update:
                    if e == "ready_rebased" and self.branch_ref:
                        self.checked_update = False
                        self.state = "loading_cancellable"
                        self.proc = run_command_threaded(
                            [BOOTC_PATH, "switch", self.branch_ref]
                        )
                        conf["updates.bootc.stage.mode"] = "loading_cancellable"
                        conf["updates.bootc.stage.loading_cancellable.progress"] = {
                            "text": _("Updating to latest "),
                            "unit": self.branches.get(
                                self.branch_name, self.branch_name
                            ),
                            "value": None,
                        }
                    elif e == "ready":
                        self.state = "loading_cancellable"
                        self.checked_update = False
                        self.proc = run_command_threaded(BOOTC_UPDATE_CMD, output=False)
                        conf["updates.bootc.stage.mode"] = "loading_cancellable"
                        conf["updates.bootc.stage.loading_cancellable.progress"] = {
                            "text": _("Updating... "),
                            "value": None,
                            "unit": None,
                        }
                    else:
                        self.state = "loading"
                        self.proc = run_command_threaded(BOOTC_STATUS_CMD)
                        self.checked_update = True
                        conf["updates.bootc.stage.mode"] = "loading"
                        conf["updates.bootc.stage.loading.progress"] = {
                            "text": _("Checking for updates..."),
                            "value": None,
                            "unit": None,
                        }
                elif revert:
                    self.checked_update = False
                    self.state = "loading"
                    self.proc = run_command_threaded(BOOTC_ROLLBACKCMD)
                    conf["updates.bootc.stage.mode"] = "loading"
                    if e == "ready_updated":
                        text = _("Undoing Update...")
                    elif e == "ready_reverted":
                        text = _("Undoing Revert...")
                    else:
                        text = _("Reverting to Previous version...")
                    conf["updates.bootc.stage.loading.progress"] = {
                        "text": text,
                        "value": None,
                        "unit": None,
                    }
                elif rebase:
                    self.checked_update = False
                    if not self.branches:
                        self._init(conf)
                    else:
                        # Get branch that should be default
                        curr = (
                            (self.status or {})
                            .get("spec", {})
                            .get("image", {})
                            .get("image", "")
                        )
                        default = get_branch(curr, self.branches)
                        conf["updates.bootc.stage.rebase.branch"] = default

                        # Prepare loader
                        conf["updates.bootc.stage.mode"] = "loading"
                        conf["updates.bootc.stage.loading.progress"] = {
                            "text": _("Loading Versions..."),
                            "value": None,
                            "unit": None,
                        }

                        # Launch loader thread
                        self.t_data = {}
                        self.t = Thread(
                            target=get_rebase_refs,
                            args=(curr, self.t_data),
                            kwargs={"branches": self.branches},
                        )
                        self.t.start()
                        self.state = "loading_rebase"
                elif reboot:
                    logger.info("User pressed reboot in updater. Rebooting...")
                    subprocess.run(["systemctl", "reboot"])

            # Incompatible
            case "incompatible":
                if conf.get_action("updates.bootc.stage.incompatible.reset"):
                    self.state = "loading"
                    self.proc = run_command_threaded(RPM_OSTREE_RESET, output=False)
                    conf["updates.bootc.stage.mode"] = "loading"
                    conf["updates.bootc.stage.loading.progress"] = {
                        "text": _("Removing Customizations..."),
                        "value": None,
                        "unit": None,
                    }

            # Rebase dialog
            case "rebase_dialog" | "loading_rebase" as e:
                # FIXME: this is the only match statement that
                # does early returns. Allows loading the previous
                # versions instantly.

                conf["updates.bootc.update"] = None
                if e == "loading_rebase":
                    if self.t is None:
                        self._init(conf)
                        return
                    elif not self.t.is_alive():
                        self.t = None
                        self.state = "rebase_dialog"
                        conf["updates.bootc.stage.mode"] = "rebase"
                    else:
                        return

                apply = conf.get_action("updates.bootc.stage.rebase.apply")
                cancel = conf.get_action("updates.bootc.stage.rebase.cancel")
                branch = conf.get(
                    "updates.bootc.stage.rebase.branch", next(iter(self.branches))
                )

                version = "latest"
                if not self.t_data:
                    conf["updates.bootc.stage.rebase.version_error"] = _(
                        "Failed to load previous versions"
                    )
                else:
                    conf["updates.bootc.stage.rebase.version_error"] = None
                    if branch in self.t_data:
                        bdata = {k.replace(".", ""): k for k in self.t_data[branch]}
                        version = conf.get(
                            "updates.bootc.stage.rebase.version.value", "latest"
                        )
                        conf["updates.bootc.stage.rebase.version"] = None
                        conf["updates.bootc.stage.rebase.version"] = {
                            "options": {
                                "latest": "Latest",
                                **bdata,
                            },
                            "value": version if version in bdata else "latest",
                        }
                        # Readd . since config system does not support them
                        version = bdata.get(version, "latest")

                if cancel:
                    self._init(conf)
                elif apply:
                    if version == "latest":
                        version = branch

                    curr = get_ref_from_status(self.status)
                    next_ref = (
                        (curr[: curr.rindex(":")] if ":" in curr else curr)
                        + ":"
                        + version
                    )
                    if next_ref == curr:
                        self._init(conf)
                    else:
                        self.state = "loading_cancellable"
                        self.proc = run_command_threaded(
                            [BOOTC_PATH, "switch", next_ref]
                        )
                        conf["updates.bootc.stage.mode"] = "loading_cancellable"
                        conf["updates.bootc.stage.loading_cancellable.progress"] = {
                            "text": _("Rebasing to "),
                            "unit": self.branches.get(version, version),
                            "value": None,
                        }

            # Wait for the subcommand to complete
            case "loading_cancellable":
                cancel = conf.get_action(
                    f"updates.bootc.stage.loading_cancellable.cancel"
                )
                if self.proc is None:
                    self._init(conf)
                elif self.proc.poll() is not None:
                    self._init(conf)
                    self.proc = None
                elif cancel:
                    logger.info("User cancelled update. Stopping...")
                    self.proc.send_signal(signal.SIGINT)
                    self.proc.wait()
                    self.proc = None
                    self._init(conf)
            case "loading":
                if self.proc is None:
                    self._init(conf)
                elif self.proc.poll() is not None:
                    self._init(conf)
                    self.proc = None

    def close(self):
        if self.proc:
            self.proc.send_signal(signal.SIGINT)
            self.proc.wait()
            self.proc = None
        if self.t:
            if self.t.is_alive():
                self.t.join()
            self.t = None


def autodetect(existing: Sequence[HHDPlugin]) -> Sequence[HHDPlugin]:
    if len(existing):
        return existing

    if not BOOTC_ENABLED:
        return []

    return [BootcPlugin()]