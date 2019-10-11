# Author: Kevin Köck
# Copyright Kevin Köck 2019 Released under the MIT license
# Created on 2019-04-26 

__updated__ = "2019-10-11"
__version__ = "0.9"

from pysmartnode import config
import uasyncio as asyncio
from pysmartnode.utils import sys_vars
from .definitions import *
import gc

# This module is used to create components that interact with mqtt.
# This could be sensors, switches, binary_sensors etc.
# It provides a base class for linking components and subscribed topics and
# provides the basis for homeassistant autodiscovery.
# Helping components like arduino, i2c and similar that only provide helper objects (like Pins)
# don't need to use this module as a basis.

_mqtt = config.getMQTT()


# TODO: implement "name" for usage in friendly_name, component registration, etc?

class Component:
    """
    Use this class as a base for components. Subclass to extend. See the template for examples.
    """
    _discovery_lock = config.Lock()
    _init_queue_start = None

    # prevent multiple discoveries from running concurrently and creating Out-Of-Memory errors
    # or queue overflow errors

    def __init__(self, component_name, version, discover=True):
        self._topics = {}
        # No RAM allocation for topic strings as they are passed by reference if saved
        # in a variable in subclass.
        # self._topics is used by mqtt to know which component a message is for.
        self._next_component = None  # needed to keep a list of registered components
        config.addComponent(self)  # adds component to the chain of components (_next_component)

        # Workaround to prevent every component object from creating a new asyncio task for
        # network oriented initialization as this would lead to an asyncio queue overflow.
        self._init_next = None
        if self._init_queue_start is None:
            self._init_queue_start = self
            asyncio.get_event_loop().create_task(self.__initNetworkProcess())
        else:
            c = self._init_queue_start
            while c._init_next is not None:
                c = c._init_next
            c._init_next = self
        self.COMPONENT_NAME = component_name
        self.VERSION = version
        self.__discover = discover

    async def __initNetworkProcess(self):
        c = self._init_queue_start
        while c is not None:
            await c._init_network()
            c = c._init_next
        self._init_queue_start = None

    async def _init_network(self):
        await config._log.asyncLog("info",
                                   "Added module {!r} version {!s} as component {!r}".format(
                                       self.COMPONENT_NAME, self.VERSION,
                                       config.getComponentName(self)))
        for t in self._topics:
            await _mqtt.subscribe(t, qos=1)
        if config.MQTT_DISCOVERY_ENABLED is True and self.__discover is True:
            async with self._discovery_lock:
                await self._discovery()

    def _subscribe(self, topic, cb):
        self._topics[topic] = cb

    async def _discovery(self):
        """Implement in subclass. Is only called by self._init unless config.MQTT_DISCOVERY_ON_RECONNECT is True."""
        pass

    @staticmethod
    async def _publishDiscovery(component_type, component_topic, unique_name, discovery_type,
                                friendly_name=None):
        topic = Component._getDiscoveryTopic(component_type, unique_name)
        msg = Component._composeDiscoveryMsg(component_topic, unique_name, discovery_type,
                                             friendly_name)
        await _mqtt.publish(topic, msg, qos=1, retain=True)
        del msg, topic
        gc.collect()

    @staticmethod
    def _composeDiscoveryMsg(component_topic, name, component_type_discovery, friendly_name=None,
                             no_avail=False):
        """
        Helper function to separate dynamic system values from user defineable values.
        :param component_topic: state topic of the component. device topics (see mqtt) are supported
        :param name: name of the component, must be unique on the device, typically composed of component name and count
        :param component_type_discovery: discovery values for the component type, e.g. switch, sensor
        :param friendly_name: optional a readable name that is used in the gui and entity_id
        :param no_avail: don't add availability configs (typically only used for the availability component itself)
        :return: str
        """
        friendly_name = friendly_name or name
        component_topic = component_topic if _mqtt.isDeviceTopic(
            component_topic) is False else _mqtt.getRealTopic(
            component_topic)
        if no_avail is True:
            return DISCOVERY_BASE_NO_AVAIL.format(component_topic,  # "~" component state topic
                                                  friendly_name,  # name
                                                  sys_vars.getDeviceID(), name,  # unique_id
                                                  component_type_discovery,
                                                  # component type specific values
                                                  sys_vars.getDeviceDiscovery())  # device
        return DISCOVERY_BASE.format(component_topic,  # "~" component state topic
                                     friendly_name,  # name
                                     config.MQTT_HOME, sys_vars.getDeviceID(),
                                     # availability_topic
                                     sys_vars.getDeviceID(), name,  # unique_id
                                     component_type_discovery,  # component type specific values
                                     sys_vars.getDeviceDiscovery())  # device

    @staticmethod
    def _composeSensorType(device_class, unit_of_measurement, value_template):
        """Just to make it easier for component developers."""
        return DISCOVERY_SENSOR.format(device_class, unit_of_measurement, value_template)

    @staticmethod
    def _getDiscoveryTopic(component_type, name):
        return "{!s}/{!s}/{!s}/{!s}/config".format(config.MQTT_DISCOVERY_PREFIX, component_type,
                                                   sys_vars.getDeviceID(), name)
