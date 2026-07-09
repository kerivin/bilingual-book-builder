import shutil
from contextlib import contextmanager

def print_horizontal_line(print_func):
        terminal_width = shutil.get_terminal_size().columns
        print_func("─" * terminal_width)

@contextmanager
def temporary_log_level(logger, level):
    prev_level = logger.level
    logger.setLevel(level)
    try:
        yield
    finally:
        logger.setLevel(prev_level)