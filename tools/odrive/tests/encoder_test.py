
import test_runner

import time
from math import pi
import os

from fibre.utils import Logger
from test_runner import EncoderTestContext, test_assert_eq, program_teensy

def modpm(val, range):
    return ((val + (range / 2)) % range) - (range / 2)

class TestIncrementalEncoder():

    def is_compatible(self, enc_ctx: EncoderTestContext):
        return True

    def run_delta_test(self, encoder, true_cps, with_cpr):
        encoder.config.cpr = with_cpr

        for i in range(100):
            now = time.monotonic()
            new_shadow_count = encoder.shadow_count
            new_count_in_cpr = encoder.count_in_cpr
            new_phase = encoder.phase
            new_pos_estimate = encoder.pos_estimate
            new_pos_cpr = encoder.pos_cpr

            if i > 0:
                dt = now - before
                test_assert_eq((new_shadow_count - last_shadow_count) / dt, true_cps, accuracy = 0.05)
                test_assert_eq(modpm(new_count_in_cpr - last_count_in_cpr, with_cpr) / dt, true_cps, accuracy = 0.3)
                #test_assert_eq(modpm(new_phase - last_phase, 2*pi) / dt, 2*pi*true_rps, accuracy = 0.1)
                test_assert_eq((new_pos_estimate - last_pos_estimate) / dt, true_cps, accuracy = 0.3)
                test_assert_eq(modpm(new_pos_cpr - last_pos_cpr, with_cpr) / dt, true_cps, accuracy = 0.3)
                test_assert_eq(encoder.vel_estimate, true_cps, accuracy = 0.05)
 
            before = now
            last_shadow_count = new_shadow_count
            last_count_in_cpr = new_count_in_cpr
            last_phase = new_phase
            last_pos_estimate = new_pos_estimate
            last_pos_cpr = new_pos_cpr
 
            time.sleep(0.01)

    def run_test(self, enc_ctx: EncoderTestContext, logger: Logger):
        true_cps = 8192*-0.5 # counts per second generated by the virtual encoder
        # TODO: read teensy config from YAML file
        if enc_ctx.num == 0:
            hexfile = 'enc0_sim_-4096cps.ino.hex'
        else:
            hexfile = 'enc1_sim_-4096cps.ino.hex'
        program_teensy(os.path.join(os.path.dirname(__file__), hexfile), 26, logger)
        time.sleep(1.0) # wait for PLLs to stabilize

        encoder = enc_ctx.handle

        # The true encoder count and PLL output should be roughly the same.
        # At 8192 CPR and 0.5 RPM, the delta because of sequential reading is
        # around 3.25 counts. The exact value depends on the connection.
        # The tracking error of the PLL is below 1 count.

        #logger.debug("check if count_in_cpr == pos_cpr")
        #configured_cpr = 8192
        #encoder.config.cpr = configured_cpr
        #expected_delta = true_cps/1200
        #for _ in range(1000):
        #    first = enc_ctx.handle.axis0.encoder.count_in_cpr
        #    second = enc_ctx.handle.axis0.encoder.pos_cpr
        #    test_assert_eq(modpm(second - first, configured_cpr), expected_delta, range=abs(true_cps/500))
        #    time.sleep(0.001)

        logger.debug("check if variables move at the correct velocity (8192 CPR)...")
        self.run_delta_test(encoder, true_cps, 8192)
        logger.debug("check if variables move at the correct velocity (65536 CPR)...")
        self.run_delta_test(encoder, true_cps, 65536)
        encoder.config.cpr = 8192



if __name__ == '__main__':
    test_runner.run(TestIncrementalEncoder())
