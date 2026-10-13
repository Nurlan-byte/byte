"""Application errors shared by independent workflows."""


class ServiceError(Exception):
    def __init__(self, status_code: int, detail):
        self.status_code, self.detail = status_code, detail
        super().__init__(str(detail))
