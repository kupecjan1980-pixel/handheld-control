import asyncio
import json
import os

import decky

from hhctl.controller import Controller

TICK = 2.0


def _version() -> str | None:
    v = getattr(decky, "DECKY_PLUGIN_VERSION", None)
    if v:
        return v
    try:
        with open(os.path.join(decky.DECKY_PLUGIN_DIR, "package.json")) as f:
            return json.load(f).get("version")
    except (OSError, ValueError):
        return None


class Plugin:
    ctl: Controller | None = None
    loop_task: asyncio.Task | None = None
    lock = asyncio.Lock()

    async def _run(self, fn, *args):
        async with self.lock:
            return await asyncio.to_thread(fn, *args)

    async def _loop(self):
        while True:
            try:
                await self._run(self.ctl.tick)
            except Exception:
                decky.logger.exception("tick failed")
            await asyncio.sleep(TICK)

    async def _main(self):
        settings = os.path.join(decky.DECKY_PLUGIN_SETTINGS_DIR, "settings.json")
        self.ctl = await asyncio.to_thread(Controller, settings, decky.DECKY_USER_HOME, _version())
        self.ctl.install_exit_hooks()
        self.ctl.plugin_dir = decky.DECKY_PLUGIN_DIR
        loop = asyncio.get_running_loop()
        # Button actions come from a listener thread; the panel opens QAM / keyboard.
        self.ctl.emit = lambda action: asyncio.run_coroutine_threadsafe(decky.emit("hhctl_button", action), loop)
        await self._run(self.ctl.apply_all)
        self.loop_task = asyncio.get_event_loop().create_task(self._loop())

    async def _unload(self):
        if self.loop_task:
            self.loop_task.cancel()
        if self.ctl:
            await self._run(self.ctl.restore, True)

    async def _uninstall(self):
        if self.ctl:
            await self._run(self.ctl.uninstall)

    # --- frontend API -----------------------------------------------------

    async def get_version(self):
        return _version()

    async def get_state(self):
        return await self._run(self.ctl.state)

    async def set_tdp(self, values: dict):
        return await self._run(self.ctl.update, "tdp", values)

    async def set_epp(self, values: dict):
        return await self._run(self.ctl.update, "epp", values)

    async def set_cpu(self, values: dict):
        return await self._run(self.ctl.update, "cpu", values)

    async def set_gpu(self, values: dict):
        return await self._run(self.ctl.update, "gpu", values)

    async def set_fan(self, values: dict):
        return await self._run(self.ctl.update, "fan", values)

    async def set_rgb(self, values: dict):
        return await self._run(self.ctl.update, "rgb", values)

    async def set_battery(self, values: dict):
        return await self._run(self.ctl.update, "battery", values)

    async def set_sleep(self, values: dict):
        return await self._run(self.ctl.update, "sleep", values)

    async def set_controller(self, values: dict):
        return await self._run(self.ctl.update, "controller", values)

    async def set_ui(self, values: dict):
        return await self._run(self.ctl.update, "ui", values)

    async def set_background(self, path: str):
        return await self._run(self.ctl.set_background, path)

    async def clear_background(self):
        return await self._run(self.ctl.clear_background)

    async def get_background(self):
        return await self._run(self.ctl.background_data)

    async def list_pictures(self, folder: str | None = None):
        return await self._run(self.ctl.list_pictures, folder)

    async def picture_data(self, path: str):
        return await self._run(self.ctl.picture_data, path)

    async def set_background_data(self, data_url: str):
        return await self._run(self.ctl.set_background_data, data_url)

    async def picture_thumb(self, path: str):
        return await self._run(self.ctl.picture_thumb, path)

    async def get_font(self, name: str):
        return await self._run(self.ctl.font_css, name)

    async def set_ec(self, values: dict):
        return await self._run(self.ctl.update, "ec", values)

    async def set_gpu_mem(self, gb: int):
        return await self._run(self.ctl.set_gpu_mem, gb)

    async def save_ip_report(self):
        return await self._run(self.ctl.save_ip_report)

    async def set_vram(self, index: int):
        return await self._run(self.ctl.set_vram, index)

    async def set_brightness(self, percent: int):
        return await self._run(self.ctl.set_brightness, percent)

    async def rgb_zone_test(self):
        return await self._run(self.ctl.rgb_zone_test)

    async def check_updates(self):
        return await self._run(self.ctl.check_updates)

    async def install_update(self, tag: str):
        return await self._run(self.ctl.install_update, tag)

    async def input_capture(self):
        return await self._run(self.ctl.run_input_capture)

    async def brightness_test(self):
        return await self._run(self.ctl.run_brightness_test)

    async def brightness_test_answer(self, answer: str):
        return await self._run(self.ctl.answer_brightness_test, answer)

    async def set_gamescope_hdr(self, mode: str):
        return await self._run(self.ctl.set_gamescope_hdr, mode)

    async def set_sdr_brightness(self, values: dict):
        return await self._run(self.ctl.update, "sdr_brightness", values)

    async def confirm_sdr_brightness(self, keep: bool):
        return await self._run(self.ctl.confirm_sdr_brightness, keep)

    async def confirm_gamescope_hdr(self, keep: bool):
        return await self._run(self.ctl.confirm_gamescope_hdr, keep)

    async def set_display(self, group: str, enabled: bool):
        return await self._run(self.ctl.set_display, group, enabled)
