"""Module A: Behavior + Contract."""
from b import LoginIntent
class UserBehavior:
    def authenticate(self, t): return self.contract(t)
    def contract(self, t): return bool(t)
