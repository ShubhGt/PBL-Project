import itertools
import unittest

import fire_monitor as fm


class ParseTests(unittest.TestCase):
    def test_good_line(self):
        r = fm.parse_line("D,312,40,28.0,45.0\n")
        self.assertEqual((r.smoke, r.flame, r.temp, r.hum), (312, 40, 28.0, 45.0))

    def test_dht_failure_gives_none(self):
        r = fm.parse_line("D,312,40,nan,nan")
        self.assertIsNone(r.temp)
        self.assertIsNone(r.hum)

    def test_bad_lines_rejected(self):
        for bad in ["", "hello", "D,1,2,3", "D,a,b,c,d", "X,1,2,3,4", "D,2000,0,25,40", "D,-5,0,25,40"]:
            self.assertIsNone(fm.parse_line(bad), bad)


class DecisionTests(unittest.TestCase):
    def test_all_eight_combinations(self):
        expected = {0: "SAFE", 1: "WARNING", 2: "POSSIBLE FIRE", 3: "FIRE DETECTED"}
        for s, f, t in itertools.product([False, True], repeat=3):
            level = fm.decide_level(s, f, t)
            self.assertEqual(level, s + f + t)
            self.assertEqual(fm.LEVEL_NAMES[level], expected[level])

    def test_classify_uses_thresholds(self):
        r = fm.Reading(fm.SMOKE_HIGH + 1, fm.FLAME_HIGH - 1, fm.TEMP_HIGH + 1, 40)
        self.assertEqual(fm.classify(r), (True, False, 1))

    def test_missing_temperature_is_not_high(self):
        self.assertEqual(fm.classify(fm.Reading(0, 0, None, None)), (False, False, 0))


class TemperatureTierTests(unittest.TestCase):
    def level(self, smoke, flame, temp):
        return fm.decide_level(*fm.classify(fm.Reading(smoke, flame, temp, 40)))

    def test_temperature_tiers(self):
        self.assertEqual(self.level(0, 0, 37), 0)     # normal
        self.assertEqual(self.level(0, 0, 41), 1)     # warning
        self.assertEqual(self.level(0, 0, 46), 2)     # critical heat alone
        self.assertEqual(self.level(500, 0, 46), 3)   # critical heat + smoke
        self.assertEqual(self.level(500, 0, 41), 2)   # warning heat + smoke


class SmootherTests(unittest.TestCase):
    def test_single_spike_is_softened(self):
        sm = fm.Smoother(5)
        for _ in range(4):
            sm.add(fm.Reading(100, 10, 25, 40))
        out = sm.add(fm.Reading(900, 10, 25, 40))
        self.assertLess(out.smoke, fm.SMOKE_HIGH)


class HoldTests(unittest.TestCase):
    def test_rises_at_once_falls_after_hold(self):
        h = fm.LevelHolder(10)
        self.assertEqual(h.update(3, now=100), 3)
        self.assertEqual(h.update(0, now=105), 3)
        self.assertEqual(h.update(0, now=111), 0)


class OutputTests(unittest.TestCase):
    def flags(self, level):
        return [(False, False, 0), (True, False, 0), (True, True, 0), (True, True, 1)][level]

    def test_each_level(self):
        r = fm.Reading(100, 10, 28, 40)
        c0, c1, c2, c3 = [fm.plan_outputs(i, r, self.flags(i)) for i in range(4)]
        self.assertEqual((c0.green, c0.red, c0.buzzer, c0.fan), (fm.LED_ON, fm.LED_OFF, fm.BUZZ_OFF, 0))
        self.assertEqual((c1.red, c1.buzzer, c1.fan), (fm.LED_BLINK, fm.BUZZ_SLOW, 0))
        self.assertEqual((c2.red, c2.buzzer, c2.fan), (fm.LED_BLINK_FAST, fm.BUZZ_FAST, 0))
        self.assertEqual((c3.red, c3.buzzer, c3.fan), (fm.LED_ON, fm.BUZZ_CONTINUOUS, 1))

    def test_command_format_is_safe(self):
        c = fm.Command(3, 1, 0, 3, 1, "A,B|C very long text here", "x" * 40)
        line = fm.format_command(c)
        self.assertTrue(line.startswith("C,3,1,0,3,1,"))
        self.assertTrue(line.endswith("\n"))
        text = line.strip().split(",", 6)[6]
        self.assertEqual(text.count("|"), 1)
        for part in text.split("|"):
            self.assertLessEqual(len(part), 16)


class FakeLink:
    """A tiny stand-in for the serial port: feeds fixed lines, records commands."""

    def __init__(self, lines):
        self.lines = list(lines)
        self.commands = []

    def readline(self):
        if not self.lines:
            raise EOFError("no more lines")
        return (self.lines.pop(0) + "\n").encode()

    def write(self, data):
        self.commands.append(data.decode())

    def close(self):
        pass


class WarmupTests(unittest.TestCase):
    def levels_during_warmup(self, line, n=3):
        link = FakeLink([line] * n)
        fm.run(link, warmup=100, hold=0, log_path=None)
        return [int(c.split(",")[1]) for c in link.commands], link.commands

    def test_smoke_alone_is_ignored_during_warmup(self):
        levels, cmds = self.levels_during_warmup("D,900,0,28.0,45.0")
        self.assertEqual(levels, [0, 0, 0])
        self.assertTrue(all("WARM-UP" in c for c in cmds))

    def test_flame_is_not_ignored_during_warmup(self):
        levels, _ = self.levels_during_warmup("D,100,1023,28.0,45.0")
        self.assertEqual(levels, [1, 1, 1])

    def test_flame_and_critical_heat_give_fire_during_warmup(self):
        levels, cmds = self.levels_during_warmup("D,100,1023,50.0,45.0")
        self.assertEqual(levels, [3, 3, 3])
        self.assertTrue(all(c.split(",")[5] == "1" for c in cmds))   # fan on


class EndToEndTests(unittest.TestCase):
    def test_simulated_scenarios(self):
        sim = fm.SimulatedArduino(interval=0)
        fm.run(sim, warmup=0, hold=0, log_path=None)
        levels = [int(cmd.split(",")[1]) for cmd in sim.commands]
        self.assertGreaterEqual(len(levels), 60)
        single_sensor_part = levels[6:42]
        self.assertTrue(max(single_sensor_part) <= 1, "single-sensor cases must not go above WARNING")
        self.assertIn(1, single_sensor_part)
        self.assertIn(2, levels)
        self.assertIn(3, levels)
        self.assertIn(2, levels[-6:])   # critical heat alone -> POSSIBLE FIRE
        fire_cmds = [c for c in sim.commands if c.startswith("C,3,")]
        self.assertTrue(all(",1,FIRE" in c or c.split(",")[5] == "1" for c in fire_cmds))


if __name__ == "__main__":
    unittest.main()
