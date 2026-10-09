from requests.adapters import HTTPAdapter
from .protocols import tls_context


class VerifiedTLSAdapter(HTTPAdapter):
    def __init__(self, ca, server_name=None):
        self.context, self.server_name = tls_context(ca), server_name
        super().__init__()

    def init_poolmanager(self, *args, **kwargs):
        kwargs['ssl_context'] = self.context
        if self.server_name:
            kwargs.update(assert_hostname=self.server_name, server_hostname=self.server_name)
        return super().init_poolmanager(*args, **kwargs)
