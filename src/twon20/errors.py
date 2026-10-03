class SetupError(Exception):
    """A safe public error. Never attach response bodies or key material."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message
