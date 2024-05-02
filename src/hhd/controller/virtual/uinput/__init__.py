import logging
import time
from typing import Sequence

from evdev import UInput

from hhd.controller.base import Consumer, Event, Producer, can_read
from hhd.controller.const import Axis, Button

from .const import *
from .monkey import UInputMonkey

logger = logging.getLogger(__name__)


class UInputDevice(Consumer, Producer):
    def __init__(
        self,
        capabilities=GAMEPAD_CAPABILITIES,
        btn_map: dict[Button, int] = GAMEPAD_BUTTON_MAP,
        axis_map: dict[Axis, AX] = GAMEPAD_AXIS_MAP,
        vid: int = HHD_VID,
        pid: int = HHD_PID_GAMEPAD,
        bus: int = 0x03,
        name: str = "Handheld Daemon Controller",
        phys: str = "phys-hhd-gamepad",
        output_imu_timestamps: str | bool = False,
        output_timestamps: bool = False,
        input_props: Sequence[int] = [],
        ignore_cmds: bool = False,
        uniq: str | None = None,
    ) -> None:
        self.capabilities = capabilities
        self.btn_map = btn_map
        self.axis_map = axis_map
        self.dev = None
        self.name = name
        self.vid = vid
        self.pid = pid
        self.bus = bus
        self.phys = phys
        self.uniq = uniq
        self.output_imu_timestamps = output_imu_timestamps
        self.output_timestamps = output_timestamps
        self.ofs = 0
        self.sys_ofs = 0
        self.input_props = input_props
        self.ignore_cmds = ignore_cmds

        self.rumble: Event | None = None

    def open(self) -> Sequence[int]:
        logger.info(f"Opening virtual device '{self.name}'.")
        try:
            self.dev = UInputMonkey(
                events=self.capabilities,
                name=self.name,
                vendor=self.vid,
                product=self.pid,
                bustype=self.bus,
                phys=self.phys,
                input_props=self.input_props,
                uniq=self.uniq,
            )
        except Exception as e:
            logger.error(
                f"Monkey patch probably failed. Could not create evdev device with uniq:\n{e}"
            )
            self.dev = UInput(
                events=self.capabilities,
                name=self.name,
                vendor=self.vid,
                product=self.pid,
                bustype=self.bus,
                phys=self.phys,
                input_props=self.input_props,
            )

        self.touchpad_aspect = 1
        self.touch_id = 1
        self.fd = self.dev.fd

        if self.ignore_cmds:
            # Do not wake up if we ignore to save utilization
            # When the output contains a timestamp, it is fed back to evdev
            # causing double wake-ups.
            return []
        return [self.fd]

    def close(self, exit: bool) -> bool:
        if self.dev:
            self.dev.close()
        self.dev = None
        self.input = None
        self.fd = None
        return True

    def consume(self, events: Sequence[Event]):
        if not self.dev:
            return

        wrote = {}
        ts = 0
        for ev in reversed(events):
            key = (ev["type"], ev["code"])
            if key in wrote:
                # skip duplicate events that were caused due to a delay
                # only keep the last button value by iterating reversed
                continue
            match ev["type"]:
                case "axis":
                    if ev["code"] in self.axis_map:
                        ax = self.axis_map[ev["code"]]
                        if ev["code"] == "touchpad_x":
                            val = int(
                                self.touchpad_aspect
                                * (ax.scale * ev["value"] + ax.offset)
                            )
                        else:
                            val = int(ax.scale * ev["value"] + ax.offset)
                        if ax.bounds:
                            val = min(max(val, ax.bounds[0]), ax.bounds[1])
                        self.dev.write(B("EV_ABS"), ax.id, val)
                        wrote[key] = val

                        if ev["code"] == "touchpad_x":
                            self.dev.write(B("EV_ABS"), B("ABS_MT_POSITION_X"), val)
                        elif ev["code"] == "touchpad_y":
                            self.dev.write(B("EV_ABS"), B("ABS_MT_POSITION_Y"), val)

                    elif (
                        self.output_imu_timestamps is True
                        and ev["code"]
                        in (
                            "accel_ts",
                            "gyro_ts",
                            "imu_ts",
                        )
                    ) or ev["code"] == self.output_imu_timestamps:
                        # We have timestamps with ns accuracy.
                        # Evdev expects us accuracy
                        ts = (ev["value"] // 1000) % (2**32)
                        self.dev.write(B("EV_MSC"), B("MSC_TIMESTAMP"), ts)
                        wrote[key] = ts
                case "button":
                    if ev["code"] in self.btn_map:
                        if ev["code"] == "touchpad_touch":
                            self.dev.write(
                                B("EV_ABS"),
                                B("ABS_MT_TRACKING_ID"),
                                self.touch_id if ev["value"] else -1,
                            )
                            self.dev.write(
                                B("EV_KEY"),
                                B("BTN_TOOL_FINGER"),
                                1 if ev["value"] else 0,
                            )
                            self.touch_id += 1
                            if self.touch_id > 500:
                                self.touch_id = 1
                        self.dev.write(
                            B("EV_KEY"),
                            self.btn_map[ev["code"]],
                            1 if ev["value"] else 0,
                        )
                        wrote[key] = ev["value"]

                case "configuration":
                    if ev["code"] == "touchpad_aspect_ratio":
                        self.touchpad_aspect = float(ev["value"])

        if wrote and self.output_timestamps:
            # We have timestamps with ns accuracy.
            # Evdev expects us accuracy
            ts = (time.perf_counter_ns() // 1000) % (2**32)
            self.dev.write(B("EV_MSC"), B("MSC_TIMESTAMP"), ts)

        if wrote and (not self.output_imu_timestamps or ts):
            self.dev.syn()

    def produce(self, fds: Sequence[int]) -> Sequence[Event]:
        if self.ignore_cmds or not self.fd or not self.fd in fds or not self.dev:
            return []

        out: Sequence[Event] = []

        while can_read(self.fd):
            for ev in self.dev.read():
                if ev.type == B("EV_MSC") and ev.code == B("MSC_TIMESTAMP"):
                    # Skip timestamp feedback
                    # TODO: Figure out why it feedbacks
                    pass
                elif ev.type == B("EV_UINPUT"):
                    if ev.code == B("UI_FF_UPLOAD"):
                        # Keep uploaded effect to apply on input
                        upload = self.dev.begin_upload(ev.value)
                        if upload.effect.type == B("FF_RUMBLE"):
                            data = upload.effect.u.ff_rumble_effect

                            self.rumble = {
                                "type": "rumble",
                                "code": "main",
                                "weak_magnitude": data.weak_magnitude / 0xFFFF,
                                "strong_magnitude": data.strong_magnitude / 0xFFFF,
                            }
                        self.dev.end_upload(upload)
                    elif ev.code == B("UI_FF_ERASE"):
                        # Ignore erase events
                        erase = self.dev.begin_erase(ev.value)
                        erase.retval = 0
                        self.dev.end_erase(erase)
                elif ev.type == B("EV_FF") and ev.value:
                    if self.rumble:
                        out.append(self.rumble)
                    else:
                        logger.warn(
                            f"Rumble requested but a rumble effect has not been uploaded.\n{ev}"
                        )
                elif ev.type == B("EV_FF") and not ev.value:
                    out.append(
                        {
                            "type": "rumble",
                            "code": "main",
                            "weak_magnitude": 0,
                            "strong_magnitude": 0,
                        }
                    )
                else:
                    logger.info(f"Controller ev received unhandled event:\n{ev}")

        return out
