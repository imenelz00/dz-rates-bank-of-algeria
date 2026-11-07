from .. import config


def get_sources(mode: str | None = None, simulate_fail=()):
    mode = mode or config.SOURCE_MODE
    if mode == "live":
        from .live_quotation import LiveQuotationSource
        from .live_reference import LiveReferenceSource
        return [LiveReferenceSource(), LiveQuotationSource()]
    from .mock import MockQuotationSource, MockReferenceSource
    return [MockReferenceSource(simulate_fail), MockQuotationSource(simulate_fail)]
