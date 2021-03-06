# Author: Kevin Köck
# Copyright Kevin Köck 2019 Released under the MIT license
# Created on 2019-03-25

"""
example config:
{
    package: .sensors.ecMeter
    component: EC
    constructor_args: {
        r1: 200                # Resistor1, Ohms
        ra: 30                 # Microcontroller Pin Resistance
        adc: 2                  # ADC pin number, where the EC cable is connected
        power_pin: 4            # Power pin, where the EC cable is connected
        ground_pin: 23          # Ground pin, don't connect EC cable to GND
        ppm_conversion: 0.64    # depends on supplier/country conversion values, see notes
        temp_coef: 0.019        # this changes depending on what chemical are measured, see notes
        k: 2.88                 # Cell Constant, 2.88 for US plug, 1.76 for EU plug, can be calculated/calibrated
        temp_sensor: sens_name  # temperature sensor component, has to provide async temperature()
        # precision_ec: 3         # precision of the ec value published
        # interval: 600         # optional, defaults to 600
        # topic_ec: sometopic   # optional, defaults to home/<controller-id>/EC
        # topic_ppm: sometopic  # optional, defaults to home/<controller-id>/PPM
        # friendly_name_ec: null # optional, friendly name shown in homeassistant gui with mqtt discovery
        # friendly_name_ppm: null # optional, friendly name shown in homeassistant gui with mqtt discovery
    }
}
"""

"""
Notes:
** Conversion to PPM:
Hana      [USA]        PPMconverion:  0.5
Eutech    [EU]         PPMconversion:  0.64
Tranchen  [Australia]  PPMconversion:  0.7

** Temperature Compensation:
The value temp_coef depends on the chemical solution.
0.019 is generaly considered the standard for plant nutrients [google "Temperature compensation EC" for more info]

** How to connect:
Put R1 between the power pin and the adc pin.
Connect the ec cable to the adc pin and ground pin.

** Inspiration from:
https://hackaday.io/project/7008-fly-wars-a-hackers-solution-to-world-hunger/log/24646-three-dollar-ec-ppm-meter-arduino
https://www.hackster.io/mircemk/arduino-electrical-conductivity-ec-ppm-tds-meter-c48201
"""

__updated__ = "2019-09-29"
__version__ = "1.2"

from pysmartnode import config
from pysmartnode import logging
from pysmartnode.components.machine.adc import ADC
from pysmartnode.components.machine.pin import Pin
import uasyncio as asyncio
import gc
import machine
import time
from pysmartnode.utils.component import Component

COMPONENT_NAME = "ECmeter"
_COMPONENT_TYPE = "sensor"

_log = logging.getLogger(COMPONENT_NAME)
_mqtt = config.getMQTT()
gc.collect()

_unit_index = -1


# TODO: add some connectivity checks of the "sensor"
# TODO: make it work without constantly connected GND

class EC(Component):
    DEBUG = False

    def __init__(self, r1, ra, adc, power_pin, ground_pin, ppm_conversion, temp_coef, k,
                 temp_sensor, precision_ec=3, interval=None, topic_ec=None, topic_ppm=None,
                 friendly_name_ec=None, friendly_name_ppm=None):
        # This makes it possible to use multiple instances of MySensor
        global _unit_index
        _unit_index += 1
        super().__init__(COMPONENT_NAME, __version__, _unit_index)
        self._interval = interval or config.INTERVAL_SENSOR_PUBLISH
        self._prec_ec = int(precision_ec)
        self._adc = ADC(adc)
        self._ppin = Pin(power_pin, machine.Pin.OUT)
        self._gpin = Pin(ground_pin, machine.Pin.IN)  # changing to OUTPUT GND when needed
        self._r1 = r1
        self._ra = ra
        self._ppm_conversion = ppm_conversion
        self._temp_coef = temp_coef
        self._k = k
        self._temp = temp_sensor
        if hasattr(temp_sensor, "temperature") is False:
            raise AttributeError(
                "Temperature sensor {!s}, type {!s} has no async method temperature()".format(
                    temp_sensor,
                    type(temp_sensor)))
        gc.collect()
        self._ec25 = None
        self._ppm = None
        self._time = 0
        self._topic_ec = topic_ec or _mqtt.getDeviceTopic("{!s}/{!s}".format("EC", self._count))
        self._topic_ppm = topic_ppm or _mqtt.getDeviceTopic("{!s}/{!s}".format("PPM", self._count))
        self._frn_ec = friendly_name_ec
        self._frn_ppm = friendly_name_ppm

    async def _init(self):
        await super()._init()
        gen = self._read
        interval = self._interval
        while True:
            await gen()
            await asyncio.sleep(interval)

    async def _discovery(self, register=True):
        sens = '"unit_of_meas":"mS",' \
               '"val_tpl":"{{ value|float }}",'
        name = "{!s}{!s}{!s}".format(COMPONENT_NAME, self._count, "EC25")
        if register:
            await self._publishDiscovery(_COMPONENT_TYPE, self._topic_ec, name, sens,
                                         self._frn_ec or "EC25")
        else:
            await self._deleteDiscovery(_COMPONENT_TYPE, name)
        del sens, name
        gc.collect()
        sens = '"unit_of_meas":"ppm",' \
               '"val_tpl":"{{ value|int }}",'
        name = "{!s}{!s}{!s}".format(COMPONENT_NAME, self._count, "PPM")
        await self._publishDiscovery(_COMPONENT_TYPE, self._topic_ppm, name, sens,
                                     self._frn_ppm or "PPM")
        del sens, name
        gc.collect()

    async def _read(self, publish=True, timeout=5):
        if time.ticks_diff(time.ticks_ms(), self._time) < 5000:
            self._time = time.ticks_ms()
            await asyncio.sleep(5)
        temp = await self._temp.temperature(publish=False)
        if temp is None:
            await asyncio.sleep(3)
            temp = await self._temp.temperature(publish=False)
            if temp is None:
                _log.warn("Couldn't get temperature, aborting EC measurement")
                self._ec25 = None
                self._ppm = None
                return None, None
        self._gpin.init(mode=machine.Pin.OUT)
        self._gpin.value(0)
        self._ppin.value(1)
        vol = self._adc.readVoltage()
        # vol = self._adc.readVoltage()
        # micropython on esp is probably too slow to need this. It was intended for arduino
        self._gpin.init(mode=machine.Pin.IN)
        if self.DEBUG is True:
            print("Temp", temp)
            print("V", vol)
        self._ppin.value(0)
        if vol >= self._adc.maxVoltage():
            ec25 = 0
            ppm = 0
            await _log.asyncLog("warn", "Cable not in fluid")
        else:
            if vol <= 0.5:
                _log.warn("Voltage <=0.5, change resistor")
            rc = (vol * (self._r1 + self._ra)) / (self._adc.maxVoltage() - vol)
            rc = rc - self._ra
            ec = 1000 / (rc * self._k)
            ec25 = ec / (1 + self._temp_coef * (temp - 25.0))
            ppm = int(ec25 * self._ppm_conversion * 1000)
            ec25 = round(ec25, self._prec_ec)
            if self.DEBUG:
                print("Rc", rc)
                print("EC", ec)
                print("EC25", ec25, "MilliSimens")
                print("PPM", ppm)
        self._ec25 = ec25
        self._ppm = ppm
        self._time = time.ticks_ms()

        if publish:
            await _mqtt.publish(self._topic_ec, ("{0:." + str(self._prec_ec) + "f}").format(ec25),
                                timeout=timeout, await_connection=False)
            await _mqtt.publish(self._topic_ppm, ppm, timeout=timeout, await_connection=False)
        return ec25, ppm

    async def ec(self, publish=True, timeout=5):
        if time.ticks_ms() - self._time > 5000:
            await self._read(publish, timeout)
        return self._ec25

    async def ppm(self, publish=True, timeout=5):
        if time.ticks_diff(time.ticks_ms(), self._time) > 5000:
            await self._read(publish, timeout)
        return self._ppm
