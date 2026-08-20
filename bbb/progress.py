from contextlib import contextmanager
import threading


class ProgressReporter:
    def __init__(self, verbosity: str = 'progress', callback=None):
        self.verbosity = verbosity
        self._callback = callback
        self._lock = threading.Lock()
        self._current_phase = None
        self._current_description = None
        self._current_total = 0
        self._current_n = 0

    @contextmanager
    def phase(self, name: str, total: int, desc: str = None):
        self._start(name, total, desc)
        try:
            yield
        finally:
            self._finish(name)

    def update(self, phase: str, step: int = 1, message: str = None):
        if self._callback is None or phase != self._current_phase:
            return
        with self._lock:
            self._current_n += step
            self._callback(phase_id=phase, description=self._current_description,
                           step=self._current_n, total=self._current_total,
                           message=message)

    def _start(self, phase: str, total: int, desc: str = None):
        if self._callback is None:
            return
        with self._lock:
            self._current_phase = phase
            self._current_description = desc
            self._current_total = total
            self._current_n = 0
            callback_total = total
        self._callback(phase_id=phase, description=desc, step=0, total=callback_total)

    def _finish(self, phase: str):
        if self._callback is None or phase != self._current_phase:
            return
        with self._lock:
            if self._current_phase:
                self._callback(phase_id=phase, description=self._current_description,
                               step=self._current_total, total=self._current_total,
                               message="Done")
            self._current_phase = None
            self._current_description = None
            self._current_total = 0
            self._current_n = 0