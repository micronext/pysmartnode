# Author: Kevin Köck
# Copyright Kevin Köck 2019 Released under the MIT license
# Created on 2019-09-28 

__updated__ = "2019-09-29"
__version__ = "0.2"

from pysmartnode.components.switches.switch_extension import Switch, ComponentSwitch, _mqtt, \
    COMPONENT_NAME, BaseMode
import uasyncio as asyncio
import time


class safety_off(BaseMode):
    """
    Shut down device after configured amount of time
    """

    def __init__(self, extended_switch: Switch, component: ComponentSwitch, component_on,
                 component_off):
        self._on_time = 30  # default value to be adapted by mqtt
        count = component._count if hasattr(component, "_count") else ""
        _name = component._name if hasattr(component, "_name") else "{!s}{!s}".format(
            COMPONENT_NAME, count)
        topic = _mqtt.getDeviceTopic("{!s}/safety_off/on_time".format(_name), is_request=True)
        extended_switch._subscribe(topic, self._changeOnTime)
        self._coro = None
        self.topic = topic

    async def _init(self):
        await _mqtt.subscribe(self.topic, qos=1, await_connection=False)

    async def _changeOnTime(self, topic, msg, retain):
        self._on_time = int(msg)
        return True

    async def on(self, extended_switch, component, component_on, component_off):
        """Turn device on"""
        if component.state() is True and self._coro is not None:
            return True
        if self._coro is None:
            if await component_on() is True:
                self._coro = self._wait_off(component_off)
                asyncio.get_event_loop().create_task(self._coro)
                return True
            else:
                return False
        else:
            raise TypeError("Should never happen")

    async def _wait_off(self, component_off):
        print("wait_off started")
        st = time.ticks_ms()
        try:
            while time.ticks_diff(time.ticks_ms(), st) < self._on_time * 1000:
                await asyncio.sleep(0.2)
        except asyncio.CancelledError:
            print("wait_off canceled")
        finally:
            self._coro = None  # prevents cancelling the cancelled coro
            await component_off()
            print("wait_off exited")

    async def off(self, extended_switch, component, component_on, component_off):
        """Turn device off"""
        if self._coro is not None:
            asyncio.cancel(self._coro)
        else:
            await component_off()
        return True

    async def activate(self, extended_switch, component, component_on, component_off):
        """Triggered whenever the mode changes and this mode has been activated"""
        if component.state() is True:
            return await self.on(extended_switch, component, component_on, component_off)
        return True

    async def deactivate(self, extended_switch, component, component_on, component_off):
        """Triggered whenever the mode changes and this mode has been deactivated"""
        return await self.off(extended_switch, component, component_on, component_off)

    def __str__(self):
        """Name of the mode, has to be the same as the classname/module"""
        return "safety_off"
