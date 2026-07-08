from contextlib import contextmanager

_callback = None
_verbosity = 'progress'
_current_phase = None
_current_description = None
_current_total = 0
_current_n = 0

def init(verbosity: str = 'progress', callback=None):
    global _verbosity, _callback
    _verbosity = verbosity
    _callback = callback

def get_verbosity():
    return _verbosity

def get_callback():
    return _callback

@contextmanager
def phase(name: str, total: int, desc: str = None):
    _start(name, total, desc)
    try:
        yield
    finally:
        _finish(name)

def _start(phase: str, total: int, desc: str = None):
    global _current_phase, _current_description, _current_total, _current_n
    if _callback is None:
        return
    _current_phase = phase
    _current_description = desc
    _current_total = total
    _current_n = 0
    _callback(phase_id=phase, description=desc, step=0, total=_current_total)

def update(phase: str, step: int = 1, message: str = None):
    global _current_phase, _current_description, _current_total, _current_n
    if _callback is None or phase != _current_phase:
        return
    _current_n += step
    _callback(phase_id=_current_phase, description=_current_description, step=_current_n, total=_current_total, message=message)

def _finish(phase: str):
    global _current_phase, _current_description, _current_total, _current_n
    if _callback is None or phase != _current_phase:
        return
    if _current_phase:
        _callback(phase_id=_current_phase, description=_current_description, step=_current_total, total=_current_total, message="Done")
    _current_phase = None
    _current_description = None
    _current_total = 0
    _current_n = 0