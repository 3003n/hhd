import logging
from functools import reduce
from typing import (
    Any,
    Literal,
    Mapping,
    MutableMapping,
    Sequence,
    TypedDict,
    cast,
    Protocol,
)
import time
from copy import copy

from .conf import Config

#
# UI settings
#
logger = logging.getLogger(__name__)


class ButtonSetting(TypedDict):
    """Just a button, emits an event. Used for resets, etc."""

    type: Literal["action"]
    tags: Sequence[str]
    title: str
    hint: str | None


class BooleanSetting(TypedDict):
    """Checkbox container."""

    type: Literal["bool"]
    tags: Sequence[str]
    title: str
    hint: str | None

    default: bool | None


class MultipleSetting(TypedDict):
    """Select one container."""

    type: Literal["multiple"]
    tags: Sequence[str]
    title: str
    hint: str | None

    options: Mapping[str, str]
    default: str | None


class DiscreteSetting(TypedDict):
    """Ordered and fixed numerical options (etc. tdp)."""

    type: Literal["discrete"]
    tags: Sequence[str]
    title: str
    hint: str | None

    options: Sequence[int | float]
    default: int | float | None


class NumericalSetting(TypedDict):
    """Floating numerical option."""

    type: Literal["float"]
    tags: Sequence[str]
    title: str
    hint: str | None
    unit: str | None

    min: float | None
    max: float | None
    smin: int | None
    smax: int | None
    step: int | None

    default: float | None


class IntegerSetting(TypedDict):
    """Floating numerical option."""

    type: Literal["int"]
    tags: Sequence[str]
    title: str
    hint: str | None
    unit: str | None

    min: int | None
    max: int | None
    smin: int | None
    smax: int | None
    step: int | None

    default: int | None


class Color(TypedDict):
    red: int
    green: int
    blue: int


class ColorSetting(TypedDict):
    """RGB color setting."""

    type: Literal["color"]
    tags: Sequence[str]
    title: str
    hint: str | None

    default: Color | None


class DisplaySetting(TypedDict):
    """Shows a text value in the UI."""

    type: Literal["display"]
    tags: Sequence[str]
    title: str
    hint: str | None

    config: Any | None
    default: Any | None


class CustomSetting(TypedDict):
    """Custom plugin setting.

    Can be used for any required custom setting that is not covered by the
    default ones (e.g., fan curves, deadzones).

    The setting type is defined by tags.
    Then, the config variable can be used to supply option specific information
    (e.g., for fan curves how many temperature points are available).

    To validate this setting, each loaded plugin's validate function is called,
    with the tags, config data, and the supplied value."""

    type: Literal["custom"]
    tags: Sequence[str]
    title: str | None
    hint: str | None

    config: Any | None
    default: Any | None


Setting = (
    ButtonSetting
    | BooleanSetting
    | MultipleSetting
    | DiscreteSetting
    | NumericalSetting
    | IntegerSetting
    | ColorSetting
    | CustomSetting
    | DisplaySetting
)

#
# Containers for settings
#


class Container(TypedDict):
    """Holds a variety of settings."""

    type: Literal["container"]
    tags: Sequence[str]
    title: str
    hint: str | None

    children: MutableMapping[str, "Setting | Container | Mode"]


class Mode(TypedDict):
    """Holds a number of containers, only one of whih can be active at a time."""

    type: Literal["mode"]
    tags: Sequence[str]
    title: str
    hint: str | None

    modes: MutableMapping[str, Container]
    default: str | None


STATE_HEADER = (
    "\n"
    + "# Handheld Daemon State Config\n"
    + "#\n"
    + "# This file contains plugin software-only configuration that will be retained\n"
    + "# across reboots. You may edit this file in lueu of using a frontend.\n"
    + "# This header is on the bottom to make editing easier with e.g., nano.\n"
    + "#\n"
    + "# Parameters that are stored in hardware (TDP, RGB colors, etc) and\n"
    + "# risky parameters that might cause instability and should be reset\n"
    + "# across sessions are not part of this file.\n"
    + "# Use profiles to apply changes to these settings.\n"
    + "#\n"
    + "# Persisted (software) parameters are marked by having a default value.\n"
    + "# Non-persisted/hardware parameters do not have a default value.\n"
    + "#\n"
    + "# This file and comments are autogenerated. Your comments will be discarded\n"
    + "# during configuration changes. Parameters with the value `default` are\n"
    + "# ignored and are meant as a template for you to change them.\n"
    + "#\n"
    + "# - CONFIGURATION PARAMETERS\n"
    + "#"
)

PROFILE_HEADER = (
    "\n"
    + "# Handheld Daemon Profile Config\n"
    + "#\n"
    + "# This file contains the configuration options that will be set when\n"
    + "# applying the profile which shares this file name.\n"
    + "# This header is on the bottom to make editing easier with e.g., nano.\n"
    + "#\n"
    + "# Settings are applied once, when applying the profile, and only the ones\n"
    + "# that are stated change. Therefore, they may drift as the system state changes\n"
    + "# (e.g., using native TDP shortcuts, or controller profile shortcuts).\n"
    + "#\n"
    + "# It is possible to set all supported parameters using profiles, and\n"
    + "# it is encouraged for you to stack profiles together.\n"
    + "#\n"
    + "# For example, you can have TDP only profiles that control the energy budget,\n"
    + "# and controller profiles that switch controller behavior.\n"
    + "# Then, depending on the game, you can apply the appropriate 2 profiles\n"
    + "# together.\n"
    + "#\n"
    + "# This file and comments are autogenerated. Your comments will be discarded\n"
    + "# during configuration changes. Parameters with the value `unset` are\n"
    + "# ignored and are meant to act as a template for you to change them.\n"
    + "#\n"
    + "# - CONFIGURATION PARAMETERS\n"
    + "#"
)


Section = Mapping[str, Container]

HHDSettings = Mapping[str, Section]


def parse(d: Setting | Container | Mode, prev: Sequence[str], out: MutableMapping):
    new_prev = list(prev)
    match d["type"]:
        case "container":
            for k, v in d["children"].items():
                parse(v, new_prev + [k], out)
        case "mode":
            out[".".join(new_prev) + ".mode"] = d.get("default", None)

            for k, v in d["modes"].items():
                parse(v, new_prev + [k], out)
        case other:
            out[".".join(new_prev)] = d.get("default", None)


def parse_defaults(sets: HHDSettings):
    out = {}
    for name, sec in sets.items():
        for cname, cont in sec.items():
            parse(cont, [name, cname], out)
    return out


def pick_tag(tag, default, a, b):
    if not b:
        return a.get(tag, default)
    return b.get(tag, a.get(tag, default))


DEFAULT_TAGS = {
    "type": None,
    "title": "",
    "hint": "",
    "unit": "",
    "tags": [],
    "default": None,
}

TYPE_TAGS = {
    "multiple": {"options": {}},
    "discrete": {"options": []},
    "int": {
        "min": None,
        "max": None,
        "step": None,
        "unit": None,
        "smin": None,
        "smax": None,
    },
    "float": {
        "min": None,
        "max": None,
        "step": None,
        "unit": None,
        "smin": None,
        "smax": None,
    },
    "custom": {"config": None},
}


def merge_reduce(
    a: Setting | Container | Mode, b: Setting | Container | Mode | None = None
):
    s = {}
    for tag, default in DEFAULT_TAGS.items():
        s[tag] = pick_tag(tag, default, a, b)

    for tag, default in TYPE_TAGS.get(a["type"], {}).items():
        s[tag] = pick_tag(tag, default, a, b)

    if b and b.get("type", None) == a.get("type", None):
        match s["type"]:
            case "container":
                new_children = dict(a.get("children", {}))
                for k, v in b.get("children", {}).items():
                    if k in new_children:
                        new_children[k] = merge_reduce(new_children[k], v)  # type: ignore
                    else:
                        new_children[k] = merge_reduce(v)  # type: ignore
                s["children"] = new_children
            case "mode":
                new_children = dict(a.get("modes", {}))
                for k, v in b.get("modes", {}).items():
                    if k in new_children:
                        new_children[k] = merge_reduce(new_children[k], v)  # type: ignore
                    else:
                        new_children[k] = merge_reduce(v)  # type: ignore
                s["modes"] = new_children
    else:
        if a.get("type", None) == "container":
            s["children"] = {
                k: merge_reduce(v) for k, v in (a.get("children", None) or {}).items()
            }

        if a.get("type", None) == "mode":
            s["modes"] = {
                k: merge_reduce(v) for k, v in (a.get("modes", None) or {}).items()
            }
    return s


def merge_reduce_sec(a: Section, b: Section):
    out = {k: cast(Container, merge_reduce(v)) for k, v in a.items()}
    for k, v in b.items():
        if k in out:
            out[k] = cast(Container, merge_reduce(out[k], v))
        else:
            out[k] = cast(Container, merge_reduce(v))

    return out


def merge_reduce_secs(a: HHDSettings, b: HHDSettings):
    out = {k: merge_reduce_sec({}, v) for k, v in a.items()}
    for k, v in b.items():
        out[k] = merge_reduce_sec(out.get(k, {}), v)

    return out


def merge_settings(sets: Sequence[HHDSettings]):
    if not sets:
        return {}
    if len(sets) > 1:
        return reduce(merge_reduce_secs, sets)
    return merge_reduce_secs({}, sets[0])


def generate_desc(s: Setting | Container | Mode):
    desc = f"*{s['title']}*\n"
    if h := s.get("hint", None):
        line = ""
        for token in h.split(" "):
            if len(line) + len(token) > 80:
                desc += f"{line}\n"
                line = ""
            line += f"{token} "
        if line:
            desc += f"{line}\n"

    match s["type"]:
        case "mode":
            desc += f"- modes: [{', '.join(map(str, s['modes']))}]\n"
        case "number":
            desc += f"- numerical: ["
            desc += f"{s['min'] if s.get('min', None) is not None else '-inf'}, "
            desc += f"{s['max'] if s.get('max', None) is not None else '+inf'}]\n"
        case "bool":
            desc += f"- boolean: [False, True]\n"
        case "multiple" | "discrete":
            desc += f"- options: [{', '.join(map(str, s['options']))}]\n"
        case "action":
            desc += f"- action: Set to True to run.\n"

    if (d := s.get("default", None)) is not None:
        desc += f"- default: {d}\n"
    return desc[:-1]


def traverse_desc(set: Setting | Container | Mode, prev: Sequence[str]):
    out = []
    out.append(
        (
            prev,
            generate_desc(set),
            max(len(prev) - 1, 0),
            set["type"] in ("mode", "container"),
        )
    )
    match set["type"]:
        case "container":
            for child_name, child in set["children"].items():
                out.extend(traverse_desc(child, [*prev, child_name]))
        case "mode":
            for mode_name, mode in set["modes"].items():
                out.extend(traverse_desc(mode, [*prev, mode_name]))
    return out


def tranverse_desc_sec(set: HHDSettings):
    out = []
    for sec_name, sec in set.items():
        for cont_name, cnt in sec.items():
            out.extend(traverse_desc(cnt, [sec_name, cont_name]))
    return out


def dump_comment(set: HHDSettings, header: str = STATE_HEADER):
    from hhd import RASTER

    out = "#\n#  "
    out += "\n#  ".join(RASTER.split("\n"))
    out += header
    descs = tranverse_desc_sec(set)
    for i, (path, desc, ofs, is_container) in enumerate(descs):
        out += f"\n# {'│' * max((ofs - 1), 0)}┌> {'.'.join(path)}\n# {'│' * ofs} "
        lines = desc.split("\n")
        out += ("\n# " + "│" * ofs + " ").join(lines[:-1])

        next_ofs = descs[i + 1][2] if i < len(descs) - 1 else 0
        if not is_container:
            next_ofs -= 1
        next_ofs = max(min(next_ofs, ofs), 0)
        out += f"\n# {'│' * next_ofs}{'└' * (ofs - next_ofs)} {lines[-1]}"
        out += f"\n# {'│' * next_ofs}"
    # out += "\n\n"
    return out


def dump_setting(
    set: Container | Mode,
    prev: Sequence[str],
    conf: Config,
    unmark: Literal["unset", "default"] = "default",
):
    """Finds the current settings that are set to a default value and swaps them
    for the value `default`. For settings without a default value (temporary),
    it sets them to None to avoid setting them."""
    match set["type"]:
        case "container":
            out = {}
            for child_name, child in set["children"].items():
                match child["type"]:
                    case "container" | "mode":
                        s = dump_setting(child, [*prev, child_name], conf, unmark)
                        if s:
                            out[child_name] = s
                    case _:
                        m = conf.get([*prev, child_name], None)
                        # Skip writing default values
                        default = child.get("default", None)
                        if default is None and unmark != "unset":
                            out[child_name] = None
                        elif m is None:
                            out[child_name] = unmark
                        elif default == m and unmark == "default":
                            out[child_name] = unmark
            return out
        case "mode":
            out = {}
            m = conf.get([*prev, "mode"], None)
            # Skip writing default values
            default = set.get("default", None)
            if default is None and unmark != "unset":
                out["mode"] = None
            elif m is None:
                out["mode"] = unmark
            elif default == m and unmark == "default":
                out["mode"] = unmark

            for mode_name, mode in set["modes"].items():
                s = dump_setting(mode, [*prev, mode_name], conf, unmark)
                if s:
                    out[mode_name] = s
            return out


def merge_dicts(a: Mapping | Any, b: Mapping | Any):
    if isinstance(a, Mapping) and isinstance(b, Mapping):
        out = dict(a)
        for k, v in b.items():
            out[k] = merge_dicts(out.get(k, None), v)
    elif isinstance(b, Mapping):
        out = {}
        for k, v in b.items():
            out[k] = merge_dicts(None, v)
    else:
        return b

    for k in list(out.keys()):
        if out[k] is None:
            del out[k]
    if not out:
        return None
    return out


def dump_settings(
    set: HHDSettings, conf: Config, unmark: Literal["unset", "default"] = "default"
):
    """Fixes default values for settings in set, drops settings without a default value,
    and retains the rest of the configuration, to not mess with plugins that
    were not loaded."""
    out: dict = {"version": get_settings_hash(set)}
    for sec_name, sec in set.items():
        out[sec_name] = {}
        for cont_name, cnt in sec.items():
            s = dump_setting(cnt, [sec_name, cont_name], conf, unmark)
            if s:
                out[sec_name][cont_name] = s

    # Merge dicts to maintain settings for plugins that did not run
    return merge_dicts({"version": None, **cast(Mapping, conf.conf)}, out)


def save_state_yaml(fn: str, set: HHDSettings, conf: Config, shash=None):
    import yaml

    if shash is None:
        shash = get_settings_hash(set)
    if conf.get("version", None) == shash and not conf.updated:
        return False

    conf["version"] = shash
    with open(fn, "w") as f:
        yaml.safe_dump(dump_settings(set, conf, "default"), f, sort_keys=False)
        f.write("\n")
        f.write(dump_comment(set, STATE_HEADER))

    return True


def save_blacklist_yaml(fn: str, avail: Sequence[str], blacklist: Sequence[str]):
    import yaml

    with open(fn, "w") as f:
        f.write(
            (
                ""
                + "# \n"
                + "# Plugin blacklist\n"
                + "# The plugin providers under blacklist will not run.\n"
                + "# \n"
                + "# Warning: this file is read only on startup.\n"
                + "# `sudo systemctl restart hhd@$(whoami)`\n"
                + "# \n"
                + "# Available providers:\n"
                + f"# [{', '.join(avail)}]\n\n"
            )
        )
        yaml.safe_dump({"blacklist": blacklist}, f, width=85, sort_keys=False)

    return True


def load_blacklist_yaml(fn: str):
    import yaml

    try:
        with open(fn, "r") as f:
            return yaml.safe_load(f)["blacklist"]
    except Exception as e:
        logger.warning(f"Plugin blacklist not found, using default (empty).")
        return ["myplugin1"]


def save_profile_yaml(
    fn: str, set: HHDSettings, conf: Config | None = None, shash=None
):
    import yaml

    if shash is None:
        shash = get_settings_hash(set)
    if conf is None:
        conf = Config({})
    elif conf.get("version", None) == shash and not conf.updated:
        return False

    conf["version"] = shash
    with open(fn, "w") as f:
        yaml.safe_dump(dump_settings(set, conf, "unset"), f, width=85, sort_keys=False)
        f.write("\n")
        f.write(dump_comment(set, PROFILE_HEADER))
    return True


def strip_defaults(c):
    if c == "default" or c == "unset":
        return None
    if not isinstance(c, Mapping):
        return c

    out = {}
    for k, v in c.items():
        l = strip_defaults(v)
        if l is not None:
            out[k] = l

    if not out:
        return None
    return out


def get_default_state(set: HHDSettings):
    return Config(parse_defaults(set))


def load_state_yaml(fn: str, set: HHDSettings):
    import yaml

    defaults = parse_defaults(set)
    try:
        with open(fn, "r") as f:
            state = cast(Mapping, strip_defaults(yaml.safe_load(f)) or {})
    except FileNotFoundError:
        logger.warning(f"State file not found. Searched location:\n{fn}")
        return None
    except yaml.YAMLError:
        logger.warning(f"State file is invalid. Searched location:\n{fn}")
        return None

    return Config([defaults, state])


def load_profile_yaml(fn: str):
    import yaml

    try:
        with open(fn, "r") as f:
            state = cast(Mapping, strip_defaults(yaml.safe_load(f)) or {})
    except FileNotFoundError:
        logger.warning(
            f"Profile file not found, using defaults. Searched location:\n{fn}"
        )
        return None
    except yaml.YAMLError:
        logger.warning(
            f"Profile file is invalid, skipping loading. Searched location:\n{fn}"
        )
        return None

    return Config([state])


def get_settings_hash(set: HHDSettings):
    import hashlib, json

    return hashlib.md5(json.dumps(set).encode()).hexdigest()[:8]


def unravel(d: Setting | Container | Mode, prev: Sequence[str], out: MutableMapping):
    new_prev = list(prev)
    match d["type"]:
        case "container":
            for k, v in d["children"].items():
                unravel(v, new_prev + [k], out)
        case "mode":
            out[".".join(new_prev) + ".mode"] = d

            for k, v in d["modes"].items():
                unravel(v, new_prev + [k], out)
        case _:
            out[".".join(new_prev)] = d


def unravel_options(settings: HHDSettings):
    options: Mapping[str, Setting | Mode] = {}
    for name, sec in settings.items():
        for cname, cont in sec.items():
            unravel(cont, [name, cname], options)

    return options


class Validator(Protocol):
    def __call__(self, tags: Sequence[str], config: Any, value: Any) -> bool:
        return False


def standard_validator(tags, config, value):
    if "progress" in tags:
        if not value:
            return False
        
        # Progress contains a dict with
        # three values: value, max, unit, and text
        if not isinstance(value, Mapping):
            return False
        
        # Value is optional and should be a number
        # If it is none, the progress bar should be pulsing
        if "value" in value:
            if not isinstance(value["value"], (int, float)):
                return False

        # Max is required and should be a number (if value is present)
        if "max" not in value and "value" in value:
            return False
        
        if not isinstance(value["max"], (int, float)):
            return False
        
        # Unit is optional, should be text
        if "unit" in value and not isinstance(value["unit"], str):
            return False
        
        # Text is optional, should be text
        if "text" in value and not isinstance(value["text"], str):
            return False


def validate_config(
    conf: Config, settings: HHDSettings, validator: Validator, use_defaults: bool = True
):
    options = unravel_options(settings)

    for k, d in options.items():
        v = conf.get(k, None)
        if d["type"] == "action":
            default = False
        else:
            default = d["default"]
        if v is None:
            if use_defaults and default is not None:
                conf[k] = default
            continue

        match d["type"]:
            case "mode":
                if v not in d["modes"]:
                    if use_defaults:
                        conf[k] = default
                    else:
                        del conf[k]
            case "bool" | "action":
                if v not in (False, True):
                    conf[k] = bool(v)
            case "multiple" | "discrete":
                if v not in d["options"]:
                    if use_defaults:
                        conf[k] = default
                    else:
                        del conf[k]
            case "int" | "integer":
                if not isinstance(v, int):
                    conf[k] = int(v)
                if v < d["min"]:
                    conf[k] = d["min"]
                if v > d["max"]:
                    conf[k] = d["max"]
            case "float":
                if not isinstance(v, float):
                    conf[k] = float(v)
                if v < d["min"]:
                    conf[k] = d["min"]
                if v > d["max"]:
                    conf[k] = d["max"]
            case "color":
                invalid = False

                if not isinstance(v, Mapping):
                    invalid = True
                else:
                    for c in ("red", "green", "blue"):
                        if c not in v:
                            invalid = True
                        elif not (0 <= v[c] < 256):
                            invalid = True

                if invalid:
                    if use_defaults:
                        conf[k] = default
                    else:
                        del conf[k]
            case "custom":
                if not (
                    validator(d["tags"], d["config"], v)
                    or standard_validator(d["tags"], d["config"], v)
                ):
                    if use_defaults:
                        conf[k] = default
                    else:
                        del conf[k]
