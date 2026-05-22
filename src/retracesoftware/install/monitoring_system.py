"""System integration helpers for sys.monitoring.

The monitor layer needs some of its own filesystem/classification work to run
outside retraced coordinates.  Keep that policy here so ``System`` stays a
generic proxy kernel and does not grow monitor-specific entrypoints.
"""

from retracesoftware import functional
from retracesoftware import utils


def root_disable_for(system):
    """Return a ``disable_for``-shaped adapter that runs calls in root space."""

    def disable_for(function, *, unwrap_args=True, retrace=True):
        if unwrap_args:
            call = functional.mapargs(
                function=functional.apply,
                transform=functional.walker(utils.try_unwrap),
            )
        else:
            call = utils.try_unwrap_apply

        return system.root_space.wrap(functional.partial(call, function))

    return disable_for
