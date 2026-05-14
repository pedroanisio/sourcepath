"""Module B: Behavior + Intent."""
from c import AdminBehavior
class LoginIntent:
    def behavior(self): return "login"
    def contract(self): return True
