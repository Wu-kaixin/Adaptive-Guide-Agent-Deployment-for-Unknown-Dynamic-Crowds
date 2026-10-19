"""Stall windows for ordinary tracking and for priority yield.

Ordinary tracking stalls when the mean remaining path of every active guide
drops by less than ``position_tolerance`` over ``stall_window`` steps and the
mean filtered speed is at most ``speed_tolerance``. Priority yield uses the
current priority guide's own remaining path instead of that mean, and it does
not consult the mean speed. The numeric window and the 0.03 m path threshold
are the original ones. The monitored quantity is not.
"""


def ordinary_stall(progress, window, position_tolerance, max_error, mean_speed, speed_tolerance):
    """Mean remaining-path window used outside priority yield."""
    return (len(progress) >= window + 1
            and progress[-window - 1] - progress[-1] < position_tolerance
            and max_error > position_tolerance
            and mean_speed <= speed_tolerance)


def priority_path_stall(samples, window, position_tolerance, max_error):
    """Remaining-path window of one priority guide.

    ``samples`` are metres of remaining polyline, oldest first, for a single
    path reference. A decrease below ``position_tolerance`` stalls the window.
    High speed, lateral motion and yielding by another guide are not inputs.
    """
    return (len(samples) >= window + 1
            and samples[-window - 1] - samples[-1] < position_tolerance
            and max_error > position_tolerance)


def stalled_this_step(priority_mode, priority_stalled, progress, window,
                      position_tolerance, max_error, mean_speed, speed_tolerance):
    """Priority mode does not fall through to the mean-speed test."""
    if priority_mode:
        return bool(priority_stalled)
    return ordinary_stall(
        progress, window, position_tolerance, max_error, mean_speed, speed_tolerance)
