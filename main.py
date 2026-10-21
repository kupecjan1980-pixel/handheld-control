import asyncio
import os

import decky

from hhctl.controller import Controller

TICK = 2.0


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
        self.ctl = await asyncio.to_thread(Controller, settings)
        self.ctl.install_exit_hooks()
        await self._run(self.ctl.apply_all)
        self.loop_task = asyncio.get_event_loop().create_task(self._loop())

    async def _unload(self):
        if self.loop_task:
            self.loop_task.cancel()
        if self.ctl:
            await self._run(self.ctl.restore)

    async def _uninstall(self):
        pass

    # --- frontend API -----------------------------------------------------

    async def get_state(self):
        return await self._run(self.ctl.state)

    async def set_tdp(self, values: dict):
        return await self._run(self.ctl.update, "tdp", values)

    async def set_epp(self, values: dict):
        return await self._run(self.ctl.update, "epp", values)

    async def set_fan(self, values: dict):
        return await self._run(self.ctl.update, "fan", values)

    async def set_rgb(self, values: dict):
        return await self._run(self.ctl.update, "rgb", values)

    async def set_battery(self, values: dict):
        return await self._run(self.ctl.update, "battery", values)

    async def set_ec(self, values: dict):
        return await self._run(self.ctl.update, "ec", values)

    async def set_brightness(self, percent: int):
        return await self._run(self.ctl.set_brightness, percent)

    async def set_display(self, group: str, enabled: bool):
        return await self._run(self.ctl.set_display, group, enabled)
