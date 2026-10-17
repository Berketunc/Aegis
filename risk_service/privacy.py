"""Explicit adapter for C's privacy/egress implementation; never a no-op."""
import importlib


class PrivacyUnavailable(RuntimeError):
    pass


class PrivacyAdapter:
    def __init__(self, adapter_path: str | None):
        self.ready = False
        self.error = "privacy_adapter_not_configured"
        self.function = None
        if adapter_path:
            try:
                module, name = adapter_path.split(":", 1)
                self.function = getattr(importlib.import_module(module), name)
                if not callable(self.function):
                    raise TypeError("Adapter must be callable")
                self.ready, self.error = True, None
            except Exception:
                self.error = "privacy_adapter_load_failed"

    def process(self, description: str, context: dict):
        if not self.ready:
            raise PrivacyUnavailable(self.error)
        result = self.function(description=description, context=context)
        if (not isinstance(result, dict)
                or not isinstance(result.get("redacted_record"), dict)
                or not isinstance(result.get("egress_payload_id"), str)
                or not result["egress_payload_id"]):
            raise PrivacyUnavailable("privacy_adapter_contract_invalid")
        # Do not retain the unredacted request or a token map in this service.
        return {"redacted_record": result["redacted_record"], "egress_payload_id": result["egress_payload_id"]}
