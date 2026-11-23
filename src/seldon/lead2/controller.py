"""C1/C18/C23: run Fresh Lead planning independently of the legacy execution loop."""

from dataclasses import replace

from ..decisions import Waiting
from ..processes import UncertainCleanup
from ..providers import ProviderFailure
from ..run import Run
from ..states import TERMINAL
from . import chat, planning


class FreshRun(Run):
    def _reopen(self):
        # Planning reconciles its own provider call before it launches a replacement.
        pass

    def step(self):
        return planning.step(self.store, self.provider, self.user)

    def run(self, wait=None):
        rejected = False
        try:
            chat.set_paused(self.store, False)
            while self.store.cursor().state not in TERMINAL:
                try:
                    self.step()
                except planning.UnusableAnswer:
                    if rejected:
                        raise
                    rejected = True
                else:
                    rejected = False
        except Waiting:
            pass
        except Exception as error:
            with self.store.transaction():
                self.store.record("lead2_fault", {"reason": str(error)})
                self.store.cursor(replace(self.store.cursor(), failure=_reason(error)))
        finally:
            self._lease.close()
        return self.store.cursor()


def _reason(error):
    if isinstance(error, planning.UnusableAnswer):
        return str(error)
    cause = error.__cause__ or error
    if (isinstance(error, UncertainCleanup)
            or isinstance(cause, ProviderFailure) and not cause.cleanup_verified):
        return "Waiting for the planner's process cleanup to be verified."
    if isinstance(cause, ProviderFailure):
        return ("The planner timed out." if "timed out" in str(cause).lower()
                else "The planner could not answer.")
    return "Planning stopped unexpectedly. See the run log."
