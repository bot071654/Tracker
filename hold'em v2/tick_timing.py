"""Low-overhead timing summaries for the tracker loop and the window that shows it.

Measurement only. Nothing here reads a screen, decides anything or changes how often anything runs: the tracker and the
window hand over a few numbers per tick, and this module writes one summary line per `every` ticks plus one line at once
for a tick slower than `slow_ms`. The numbers are milliseconds from a monotonic clock; no card, hand or account data is
ever passed in.
"""

import logging

logger = logging.getLogger(__name__)


def _percentile(sorted_values, fraction):
    return sorted_values[min(len(sorted_values) - 1, int(len(sorted_values) * fraction))]


class TimingSummary:
    """Collects rows of numbers (and one optional label per row) and logs them in groups."""

    def __init__(self, tag, every=20, slow_ms=None, slow_key="tick", log=None):
        self.tag = tag
        self.every = max(1, int(every))
        self.slow_ms = slow_ms
        self.slow_key = slow_key
        self._log = log or logger
        self._rows = []
        self._labels = {}

    def add(self, row, label=None):
        """Record one tick. `row` is {name: milliseconds or None}; `label` is a short word such as the layout source."""
        if self.slow_ms is not None and (row.get(self.slow_key) or 0.0) >= self.slow_ms:
            self._log.info("%s_SLOW %s%s", self.tag, self._format_row(row),
                           " | %s" % label if label else "")
        self._rows.append(row)
        if label:
            self._labels[label] = self._labels.get(label, 0) + 1
        if len(self._rows) >= self.every:
            self.flush()

    def flush(self):
        if not self._rows:
            return
        names = []
        for row in self._rows:
            for name in row:
                if name not in names:
                    names.append(name)
        parts = []
        for name in names:
            values = sorted(row[name] for row in self._rows if row.get(name) is not None)
            if values:
                parts.append("%s %.0f/%.0f/%.0f" % (name, _percentile(values, 0.5), _percentile(values, 0.9),
                                                    values[-1]))
        labels = ""
        if self._labels:
            labels = " | " + " ".join("%s=%d" % item for item in sorted(self._labels.items()))
        self._log.info("%s n=%d (median/p90/max ms) %s%s", self.tag, len(self._rows), " ".join(parts), labels)
        self._rows, self._labels = [], {}

    @staticmethod
    def _format_row(row):
        return " ".join("%s=%.0f" % (name, value) for name, value in row.items() if value is not None)
