# Provides utilities for standalone test scripts.
# This script is not intended to be run directly.

import sys, os
sys.path.append(os.path.join(os.path.dirname(__file__), '..', '..'))

import odrive
from fibre import Logger, Event
import argparse
import yaml
from inspect import signature
import itertools
import time


# Assert utils ----------------------------------------------------------------#

class TestFailed(Exception):
    def __init__(self, message):
        Exception.__init__(self, message)

def test_assert_eq(observed, expected, range=None, accuracy=None):
    sign = lambda x: 1 if x >= 0 else -1

    # Comparision with absolute range
    if not range is None:
        if (observed < expected - range) or (observed > expected + range):
            raise TestFailed("value out of range: expected {}+-{} but observed {}".format(expected, range, observed))

    # Comparision with relative range
    elif not accuracy is None:
        if sign(observed) != sign(expected) or (abs(observed) < abs(expected) * (1 - accuracy)) or (abs(observed) > abs(expected) * (1 + accuracy)):
            raise TestFailed("value out of range: expected {}+-{}% but observed {}".format(expected, accuracy*100.0, observed))

    # Exact comparision
    else:
        if observed != expected:
            raise TestFailed("value mismatch: expected {} but observed {}".format(expected, observed))


# Test Components -------------------------------------------------------------#

class ODriveTestContext():
    def __init__(self, yaml: dict):
        self.handle = None
        self.yaml = yaml
        #self.axes = [AxisTestContext(None), AxisTestContext(None)]
        self.encoders = [EncoderTestContext(self, 0, None), EncoderTestContext(self, 1, None)]

    def __repr__(self):
        return self.yaml['name']

    def make_available(self, logger: Logger):
        """
        Connects to the ODrive
        """
        if not self.handle is None:
            return

        logger.debug('waiting for {} ({})'.format(self.yaml['name'], self.yaml['serial-number']))
        self.handle = odrive.find_any(
            path="usb", serial_number=self.yaml['serial-number'], timeout=30)#, printer=print)
        #for axis_idx, axis_ctx in enumerate(self.axes):
        #    axis_ctx.handle = self.handle.__dict__['axis{}'.format(axis_idx)]
        for encoder_idx, encoder_ctx in enumerate(self.encoders):
            encoder_ctx.handle = self.handle.__dict__['axis{}'.format(encoder_idx)].encoder

class EncoderTestContext():
    def __init__(self, odrv_ctx: ODriveTestContext, num: int, yaml: dict):
        self.handle = None
        self.odrv_ctx = odrv_ctx
        self.num = num
    
    def __repr__(self):
        return str(self.odrv_ctx) + '.encoder' + str(self.num)

    def make_available(self, logger: Logger):
        self.odrv_ctx.make_available(logger)

class CANTestContext():
    def __init__(self, yaml: dict):
        self.handle = None
        self.yaml = yaml
    
    def make_available(self, logger: Logger):
        if not self.handle is None:
            return

        # TODO: read bus name from yaml
        import can
        self.handle = can.interface.Bus(bustype='socketcan', channel=self.yaml['id'], bitrate=250000)


# Helper functions ------------------------------------------------------------#

def yaml_to_test_objects(test_rig_yaml: dict, logger: Logger):
    available_test_objects = {}

    def add_component(component):
        available_test_objects[type(component)] = available_test_objects.get(type(component), [])
        available_test_objects[type(component)].append(component)

    for component_yaml in test_rig_yaml['components']:
        if component_yaml['type'] == 'odrive':
            odrv_ctx = ODriveTestContext(component_yaml)
            add_component(odrv_ctx)
            for enc_ctx in odrv_ctx.encoders:
                add_component(enc_ctx)
        elif component_yaml['type'] == 'generalpurpose':
            for (k, v) in [(k, v) for (k, v) in component_yaml.items() if k.startswith("can")]:
                can_ctx = CANTestContext({'id': k, 'bus': v})
                add_component(can_ctx)
        else:
            logger.warn('test rig has unsupported component ' + component_yaml['type'])
            continue
        

    return available_test_objects

def run_shell(command_line, logger, timeout=None):
    """
    Runs a shell command in the current directory
    """
    import shlex
    import subprocess
    logger.debug("invoke: " + str(command_line))
    if isinstance(command_line, list):
        cmd = command_line
    else:
        cmd = shlex.split(command_line)
    result = subprocess.run(cmd, timeout=timeout,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT)
    if result.returncode != 0:
        logger.error(result.stdout.decode(sys.stdout.encoding))
        raise TestFailed("command {} failed".format(command_line))

def program_teensy(hex_file_path, program_gpio: int, logger: Logger):
    """
    Programs the specified hex file onto the Teensy.
    To reset the Teensy, a GPIO of the local system must be connected to the
    Teensy's "Program" pin. This pin must first be manually made available to
    user space:
    
        echo 26 | sudo tee /sys/class/gpio/export
        sudo chmod a+rw /sys/class/gpio/gpio26/*
        echo out > /sys/class/gpio/gpio26/direction

    """

    # Put Teensy into program mode by pulling it's program pin down
    with open("/sys/class/gpio/gpio{}/value".format(program_gpio), "w") as gpio:
        gpio.write("0")
    time.sleep(0.1)
    with open("/sys/class/gpio/gpio{}/value".format(program_gpio), "w") as gpio:
        gpio.write("1")
    
    run_shell(["teensy_loader_cli", "-mmcu=imxrt1062", "-w", hex_file_path], logger, timeout = 5)
    time.sleep(0.5) # give it some time to boot

def run(test_case):
    # Parse arguments
    parser = argparse.ArgumentParser(description='ODrive automated test tool\n')
    parser.add_argument("--ignore", metavar='DEVICE', action='store', nargs='+',
                        help="Ignore (disable) one or more components of the test rig")
                        # TODO: implement
    parser.add_argument("--test-rig-yaml", type=argparse.FileType('r'), required=True,
                        help="test rig YAML file")
    parser.set_defaults(ignore=[])

    args = parser.parse_args()

    # Load objects
    test_rig_yaml = yaml.load(args.test_rig_yaml, Loader=yaml.BaseLoader)
    logger = Logger()

    available_test_objects = yaml_to_test_objects(test_rig_yaml, logger)

    # Compile a list of list of potential objects that might be compatible with this
    # test
    possible_parameters = []
    sig = signature(test_case.is_compatible)
    for param_name in sig.parameters:
        param_type = sig.parameters[param_name].annotation
        possible_parameters.append(available_test_objects[param_type])

    # For each combination, check if the test is compatible with these objects
    for param_combination in itertools.product(*possible_parameters):
        if not test_case.is_compatible(*param_combination):
            continue
        
        for param in param_combination:
            param.make_available(logger)

        logger.notify('* running {} on {}...'.format(type(test_case).__name__,
                [str(p) for p in param_combination]))
        test_case.run_test(*param_combination, logger)


    logger.success('All tests passed!')
