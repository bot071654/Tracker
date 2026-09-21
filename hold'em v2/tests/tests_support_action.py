"""Fakes shared by the Action Controller tests: a table and a mouse, no real input."""

import time

from automation import mouse_controller as mc

ANTE_POINT, PLAY_POINT = [140, 166], [140, 229]


class FakeTable:
    """The TEST table as the controller sees it."""

    def __init__(self):
        self.visible = {mc.ANTE: True, mc.PLAY_BUTTON: True}
        self.running = True
        self.counts = {mc.ANTE: 0, mc.PLAY_BUTTON: 0}
        self.register = True
        self.at = None
        self.centres = {mc.ANTE: list(ANTE_POINT), mc.PLAY_BUTTON: list(PLAY_POINT)}

    def validate(self, button, point):
        if not self.running:
            return False, "TEST poker table is not running (no status file)"
        if not self.visible[button]:
            return False, "TEST %s button is not visible" % button.upper()
        return True, "ok"

    def button_centre(self, button):
        return self.centres.get(button)

    def count(self, button):
        return self.counts[button]

    def wait_for_click(self, button, before, timeout):
        return self.counts[button] > before


class FakeMouse:
    def __init__(self, table, points):
        self.table, self.points = table, points
        self.moves, self.presses = [], []
        self.hold = 0.0

    def move(self, x, y, duration):
        self.moves.append((x, y))
        self.table.at = next((b for b, p in self.points.items() if list(p) == [x, y]), None)

    def press(self, hold):
        if self.hold:
            time.sleep(self.hold)
        self.presses.append(self.table.at)
        if self.table.register and self.table.at:
            self.table.counts[self.table.at] += 1


class Log:
    def __init__(self):
        self.lines = []

    def info(self, fmt, *args):
        self.lines.append(fmt % args)


def make(enabled=True, synchronous=True, **overrides):
    values = dict(automation_enabled=enabled, ante_button=ANTE_POINT, play_button=PLAY_POINT)
    values.update(overrides)
    config = mc.MouseControllerConfig(**values).validate()
    table = FakeTable()
    mouse = FakeMouse(table, {mc.ANTE: config.ante_button or ANTE_POINT,
                              mc.PLAY_BUTTON: config.play_button or PLAY_POINT})
    log = Log()
    controller = mc.ActionController(config, target=table, mouse=mouse, log=log,
                                     synchronous=synchronous)
    return controller, table, mouse, log
