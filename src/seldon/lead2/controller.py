"""C1/C18/C23: run Fresh Lead independently of the legacy execution loop."""

from dataclasses import replace

from ..decisions import Waiting
from ..processes import UncertainCleanup
from ..providers import Frozen, ProviderFailure
from ..run import Run
from ..states import LEAD2_READY, LEAD2_WALK, TERMINAL
from . import build, chat, planning, walk
from .store import SpecStore


class FreshRun(Run):
    def _reopen(self):
        """The selected phase reconciles its own provider work."""

    def step(self):
        if self.store.cursor().state in (LEAD2_WALK, "C"):
            walk.step(self.store, SpecStore(self.store), self.provider, self.user)
            return self.store.cursor().state
        if self.store.cursor().state == LEAD2_READY:
            build.step(self.store, SpecStore(self.store), self.provider, self.user)
            return self.store.cursor().state
        return planning.step(self.store, self.provider, self.user)

    def run(self, wait=None):
        try:
            chat.set_paused(self.store, False)
            while self.store.cursor().state not in TERMINAL:
                self.step()
        except Waiting:
            pass
        except Exception as error:
            with self.store.transaction():
                self.store.record("lead2_fault", {"reason": str(error)})
                cursor = self.store.cursor()
                self.store.cursor(replace(cursor, failure=_reason(error, cursor.state == LEAD2_READY)))
        finally:
            self._lease.close()
        return self.store.cursor()


def _reason(error, building):
    if isinstance(error, planning.UnusableAnswer):
        return str(error)
    cause = error if isinstance(error, ProviderFailure) else error.__cause__
    if (isinstance(error, UncertainCleanup)
            or isinstance(cause, ProviderFailure) and not cause.cleanup_verified):
        return "Waiting for provider process cleanup to be verified."
    if isinstance(cause, ProviderFailure):
        if isinstance(cause, Frozen) or str(cause) == "Provider paused this request under its safety policy.":
            return str(cause)
        actor = "Provider" if building else "The planner"
        return actor + (" timed out." if "timed out" in str(cause).lower() else " could not answer.")
    return ("Build" if building else "Planning") + " stopped unexpectedly. See the run log."
