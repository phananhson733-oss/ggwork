"""market-map-v1: the display groups, the Trends geo to GSC alpha-3 pairing and the geo plan (design 1.3, 5.7, 6.1).

Groups only arrange the data page: a group total is a display sum, never a unit of judgment. Links and cooling blocks
pair one Trends geo with one GSC country (geo_country) and nothing else, so Trends US never stands for North America and
GB never for the UK, Ireland, Australia and New Zealand. A country in no group (the Philippines, India) is shown only in
the site total. Page language never implies a country: country attribution comes from page x country rows only.

The table is frozen into every set by its version (FrozenInputs.market_map_version); a change is a new version next to
this one in MARKET_MAPS, never an edit of what market-map-v1 means. The country lists follow the UN M49 regions, with the
design's exceptions: the United Kingdom and Ireland sit with Australia and New Zealand, Bulgaria stands alone, and North
America is only the United States and Canada.
"""

from dataclasses import dataclass
from types import MappingProxyType
from typing import Literal

# UN M49 Europe (Eastern, Northern, Southern, Western) without GBR and IRL (uk_ie_au_nz) and BGR (bulgaria).
_EUROPE = (
    "BLR", "CZE", "HUN", "MDA", "POL", "ROU", "RUS", "SVK", "UKR",
    "ALA", "DNK", "EST", "FIN", "FRO", "GGY", "IMN", "ISL", "JEY", "LTU", "LVA", "NOR", "SJM", "SWE",
    "ALB", "AND", "BIH", "ESP", "GIB", "GRC", "HRV", "ITA", "MKD", "MLT", "MNE", "PRT", "SMR", "SRB", "SVN", "VAT",
    "AUT", "BEL", "CHE", "DEU", "FRA", "LIE", "LUX", "MCO", "NLD",
)  # fmt: skip
# UN M49 Latin America and the Caribbean.
_LATIN_AMERICA = (
    "ABW", "AIA", "ATG", "BES", "BHS", "BLM", "BRB", "CUB", "CUW", "CYM", "DMA", "DOM", "GLP", "GRD", "HTI", "JAM", "KNA",
    "LCA", "MAF", "MSR", "MTQ", "PRI", "SXM", "TCA", "TTO", "VCT", "VGB", "VIR",
    "BLZ", "CRI", "GTM", "HND", "MEX", "NIC", "PAN", "SLV",
    "ARG", "BOL", "BRA", "BVT", "CHL", "COL", "ECU", "FLK", "GUF", "GUY", "PER", "PRY", "SGS", "SUR", "URY", "VEN",
)  # fmt: skip

FirstRound = Literal["A", "B"] | None


@dataclass(frozen=True, slots=True)
class MarketGroup:
    """One display group. core: counted in the Europe-and-North-America figures (Latin America shows next to them but is
    not counted; Bulgaria is a separate slice)."""

    key: str
    label: str
    countries: tuple[str, ...]
    geos: tuple[str, ...]
    core: bool
    note: str | None = None


@dataclass(frozen=True, slots=True)
class GeoPlan:
    """How design 1.3 queries one Trends geo: the languages whose titles it takes, and the highest watch tier it may hold
    in the first round (None: not in the first-round list; GB joins in the stable period, BG is only sampled in stage 0).
    TR-18 applies it; this module only states it."""

    geo: str
    languages: tuple[str, ...]
    first_round: FirstRound
    note: str


@dataclass(frozen=True, slots=True)
class MarketMap:
    version: str
    groups: tuple[MarketGroup, ...]
    geo_country: MappingProxyType
    geo_plans: tuple[GeoPlan, ...]

    def group_of_country(self, country: str) -> MarketGroup | None:
        """The group showing this alpha-3 country (or ALL); None: the country only counts in the site total."""
        return next((group for group in self.groups if country in group.countries), None)

    def group_of_geo(self, geo: str) -> MarketGroup | None:
        return next((group for group in self.groups if geo in group.geos), None)

    def country_of_geo(self, geo: str) -> str | None:
        """The one GSC country a Trends geo links with (WW with ALL); None: the geo links with nothing."""
        return self.geo_country.get(geo)

    def geo_of_country(self, country: str) -> str | None:
        return next((geo for geo, paired in self.geo_country.items() if paired == country), None)


_BULGARIA_NOTE = "保语页面 98.1% 的点击来自一部剧（保语页面口径，不是国家口径）；首轮不进 Trends 清单，阶段 0 抽样"

MARKET_MAP_V1 = MarketMap(
    version="market-map-v1",
    groups=(
        MarketGroup("global", "全球", ("ALL",), ("WW",), core=False, note="Trends 的 WW 与 GSC 全站合计只并列显示为全球同向"),
        MarketGroup("north_america", "北美", ("USA", "CAN"), ("US",), core=True),
        MarketGroup("uk_ie_au_nz", "英爱澳新", ("GBR", "IRL", "AUS", "NZL"), ("GB",), core=True),
        MarketGroup("europe", "欧洲", _EUROPE, ("ES", "DE", "FR", "IT"), core=True, note="西欧、南欧、中东欧与北欧；本国 geo 首轮只做 B 档筛查"),
        MarketGroup("latin_america", "拉美", _LATIN_AMERICA, ("MX", "BR"), core=False, note="紧挨欧美显示，不计入欧美"),
        MarketGroup("bulgaria", "保加利亚", ("BGR",), ("BG",), core=False, note=_BULGARIA_NOTE),
    ),
    geo_country=MappingProxyType(
        {"WW": "ALL", "US": "USA", "GB": "GBR", "ES": "ESP", "DE": "DEU", "FR": "FRA", "IT": "ITA", "MX": "MEX", "BR": "BRA", "BG": "BGR"}
    ),
    geo_plans=(
        GeoPlan("WW", ("en",), "A", "英语剧查全球；菲律宾、印度只在这里看"),
        GeoPlan("US", ("en",), "A", "北美只查 US，不代表整个北美"),
        GeoPlan("GB", ("en",), None, "稳定期加查"),
        GeoPlan("ES", ("es",), "B", "欧洲本国 geo，首轮只做 B 档筛查"),
        GeoPlan("DE", ("de",), "B", "欧洲本国 geo，首轮只做 B 档筛查"),
        GeoPlan("FR", ("fr",), "B", "欧洲本国 geo，首轮只做 B 档筛查"),
        GeoPlan("IT", ("it",), "B", "欧洲本国 geo，首轮只做 B 档筛查"),
        GeoPlan("MX", ("es",), "A", "拉美，紧挨欧美显示"),
        GeoPlan("BR", ("pt",), "A", "拉美，紧挨欧美显示"),
        GeoPlan("BG", ("bg",), None, "首轮不进清单，阶段 0 抽样"),
    ),
)

MARKET_MAPS = MappingProxyType({MARKET_MAP_V1.version: MARKET_MAP_V1})


def market_map(version: str) -> MarketMap:
    """The table a set was frozen with (D29): an unknown version is refused, never read as the newest one."""
    found = MARKET_MAPS.get(version)
    if found is None:
        raise LookupError(f"未登记的市场表版本：{version}")
    return found
