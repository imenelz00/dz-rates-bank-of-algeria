from dataclasses import dataclass
from datetime import date
from decimal import Decimal


class SourceError(Exception):
    """The source could not be read (network, HTTP error...)."""


class RobotsDisallowed(SourceError):
    """fIn case of forbiden access to the url"""


class ParserDriftError(SourceError):
    """for failing in case The page/PDF no longer looks like what the parser expects"""


@dataclass
class RateRecord:
    currency_code: str
    rate_type: str            # reference | buy | sell | annual_avg | annual_eop
    granularity: str          # daily | annual
    value: Decimal
    publication_date: date
    value_date: date
    source_url: str
    source_kind: str
    raw_ref: str | None = None


class Source:
    """A daily source. `fetch`  records for one currency between two dates"""
    kind: str = ""
    rate_types: tuple[str, ...] = ()

    def fetch(self, currency: str, start: date, end: date) -> list[RateRecord]:
        raise NotImplementedError

    def earliest_date(self, currency: str) -> date | None:
        """Earliest date the source can serve, or None when unknown caus the website itself doesn't have historical data wierd"""
        return None
