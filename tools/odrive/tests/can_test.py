
import test_runner

import struct
import can
import asyncio
import time

from fibre.utils import Logger
from odrive.enums import errors
from test_runner import CANTestContext, ODriveTestContext, test_assert_eq

# Each argument is described as tuple (name, format, scale).
# Struct format codes: https://docs.python.org/2/library/struct.html
command_set = {
    'heartbeat': (0x001, [('error', 'I', 1), ('current_state', 'I', 1)]), # untested
    'estop': (0x002, []), # tested
    'get_motor_error': (0x003, [('motor_error', 'I', 1)]), # untested
    'get_encoder_error': (0x004, [('encoder_error', 'I', 1)]), # untested
    'get_sensorless_error': (0x004, [('sensorless_error', 'I', 1)]), # untested
    'set_node_id': (0x006, [('node_id', 'H', 1)]), # tested
    'set_requested_state': (0x007, [('requested_state', 'I', 1)]), # tested
    # 0x008 not yet implemented
    'get_encoder_estimates': (0x009, [('encoder_pos_estimate', 'f', 1), ('encoder_vel_estimate', 'f', 1)]), # untested
    'get_encoder_count': (0x00a, [('encoder_shadow_count', 'i', 1), ('encoder_count', 'i', 1)]), # untested
    'set_controller_modes': (0x00b, [('control_mode', 'i', 1), ('input_mode', 'i', 1)]), # tested
    'set_input_pos': (0x00c, [('input_pos', 'i', 1), ('vel_ff', 'h', 0.1), ('cur_ff', 'h', 0.01)]), # tested
    'set_input_vel': (0x00d, [('input_vel', 'i', 0.01), ('cur_ff', 'h', 0.01)]), # tested
    'set_input_current': (0x00e, [('input_current', 'i', 0.01)]), # tested
    'set_velocity_limit': (0x00f, [('velocity_limit', 'f', 1)]), # tested
    'start_anticogging': (0x010, []), # untested
    'set_traj_vel_limit': (0x011, [('traj_vel_limit', 'f', 1)]), # tested
    'set_traj_accel_limits': (0x012, [('traj_accel_limit', 'f', 1), ('traj_decel_limit', 'f', 1)]), # tested
    'set_traj_a_per_css': (0x013, [('a_per_css', 'f', 1)]), # tested
    'get_iq': (0x014, [('iq_setpoint', 'f', 1), ('iq_measured', 'f', 1)]), # untested
    'get_sensorless_estimates': (0x015, [('sensorless_pos_estimate', 'f', 1), ('sensorless_vel_estimate', 'f', 1)]), # untested
    'reboot': (0x016, []), # untested
    'get_vbus_voltage': (0x017, [('vbus_voltage', 'f', 1)]), # tested
    'clear_errors': (0x018, []), # partially tested
}

def command(bus, node_id_, cmd_name, **kwargs):
    cmd_spec = command_set[cmd_name]
    cmd_id = cmd_spec[0]
    fmt = '<' + ''.join([f for (n, f, s) in cmd_spec[1]]) # all little endian

    if (sorted([n for (n, f, s) in cmd_spec[1]]) != sorted(kwargs.keys())):
        raise Exception("expected arguments: " + str([n for (n, f, s) in cmd_spec[1]]))

    fields = [((kwargs[n] / s) if f == 'f' else int(kwargs[n] / s)) for (n, f, s) in cmd_spec[1]]
    data = struct.pack(fmt, *fields)
    msg = can.Message(arbitration_id=((node_id_ << 5) | cmd_id), data=data)
    bus.send(msg)

async def request(bus, node_id, cmd_name, timeout = 1.0):
    cmd_spec = command_set[cmd_name]
    cmd_id = cmd_spec[0]
    fmt = '<' + ''.join([f for (n, f, s) in cmd_spec[1]]) # all little endian

    reader = can.AsyncBufferedReader()
    notifier = can.Notifier(bus, [reader], timeout = timeout, loop = asyncio.get_event_loop())

    try:
        msg = can.Message(arbitration_id=((node_id << 5) | cmd_id), data=[], is_remote_frame=True)
        bus.send(msg)

        # The timeout in can.Notifier only triggers if no new messages are received at all,
        # so we need a second monitoring method.
        start = time.monotonic()
        while True:
            msg = await reader.get_message()
            if ((msg.arbitration_id == ((node_id << 5) | cmd_id)) and not msg.is_remote_frame):
                break
            if (time.monotonic() - start) > timeout:
                raise TimeoutError()
    finally:
        notifier.stop()

    fields = struct.unpack(fmt, msg.data[:(struct.calcsize(fmt))]) 
    return {n: (fields[i] * s) for (i, (n, f, s)) in enumerate(cmd_spec[1])}


class TestSimpleCAN():
    def is_compatible(self, canbus: CANTestContext, odrive: ODriveTestContext):
        return canbus.yaml['bus'] == odrive.yaml['can'] # check if connected

    def run_test(self, canbus: CANTestContext, odrive: ODriveTestContext, logger: Logger):
        node_id = 0
        axis = odrive.handle.axis0
        axis.config.can_node_id = node_id
        time.sleep(0.1)
        
        def my_cmd(cmd_name, **kwargs): command(canbus.handle, node_id, cmd_name, **kwargs)
        def my_req(cmd_name, **kwargs): return asyncio.run(request(canbus.handle, node_id, cmd_name, **kwargs))
        def fence(): my_req('get_vbus_voltage') # fence to ensure the CAN command was sent

        test_assert_eq(my_req('get_vbus_voltage')['vbus_voltage'], odrive.handle.vbus_voltage, accuracy=0.01)

        my_cmd('set_node_id', node_id=node_id+20)
        asyncio.run(request(canbus.handle, node_id+20, 'get_vbus_voltage'))
        test_assert_eq(axis.config.can_node_id, node_id+20)

        # Reset node ID to default value
        command(canbus.handle, node_id+20, 'set_node_id', node_id=node_id)
        fence()
        test_assert_eq(axis.config.can_node_id, node_id)

        my_cmd('clear_errors')
        fence()
        test_assert_eq(axis.error, 0)

        my_cmd('estop')
        fence()
        test_assert_eq(axis.error, errors.axis.ERROR_ESTOP_REQUESTED)

        my_cmd('set_requested_state', requested_state=42) # illegal state - should assert axis error
        fence()
        test_assert_eq(axis.current_state, 1) # idle
        test_assert_eq(axis.error, errors.axis.ERROR_ESTOP_REQUESTED | errors.axis.ERROR_INVALID_STATE)

        my_cmd('clear_errors')
        fence()
        test_assert_eq(axis.error, 0)

        my_cmd('set_controller_modes', control_mode=1, input_mode=5) # current conrol, traprzoidal trajectory
        fence()
        test_assert_eq(axis.controller.config.control_mode, 1)
        test_assert_eq(axis.controller.config.input_mode, 5)

        # Reset to safe values
        my_cmd('set_controller_modes', control_mode=3, input_mode=1) # position control, passthrough
        fence()
        test_assert_eq(axis.controller.config.control_mode, 3)
        test_assert_eq(axis.controller.config.input_mode, 1)

        my_cmd('set_input_pos', input_pos=1, vel_ff=2, cur_ff=3)
        fence()
        test_assert_eq(axis.controller.input_pos, 1.0, range=0.1)
        test_assert_eq(axis.controller.input_vel, 2.0, range=0.01)
        test_assert_eq(axis.controller.input_current, 3.0, range=0.001)

        my_cmd('set_input_vel', input_vel=-10.0, cur_ff=30.1234)
        fence()
        test_assert_eq(axis.controller.input_vel, -10.0, range=0.01)
        test_assert_eq(axis.controller.input_current, 30.1234, range=0.01)

        my_cmd('set_input_current', input_current=3.1415)
        fence()
        test_assert_eq(axis.controller.input_current, 3.1415, range=0.01)

        my_cmd('set_velocity_limit', velocity_limit=23456.78)
        fence()
        test_assert_eq(axis.controller.config.vel_limit, 23456.78, range=0.001)

        my_cmd('set_traj_vel_limit', traj_vel_limit=123.456)
        fence()
        test_assert_eq(axis.trap_traj.config.vel_limit, 123.456, range=0.0001)

        my_cmd('set_traj_accel_limits', traj_accel_limit=98.231, traj_decel_limit=-12.234)
        fence()
        test_assert_eq(axis.trap_traj.config.accel_limit, 98.231, range=0.0001)
        test_assert_eq(axis.trap_traj.config.decel_limit, -12.234, range=0.0001)

        my_cmd('set_traj_a_per_css', a_per_css=55.086)
        fence()
        test_assert_eq(axis.controller.config.inertia, 55.086, range=0.0001)


if __name__ == '__main__':
    test_runner.run(TestSimpleCAN())
