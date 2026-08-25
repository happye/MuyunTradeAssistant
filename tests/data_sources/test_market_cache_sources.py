import os
import sys
from tempfile import TemporaryDirectory
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse


sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.scanner.market_cache import MarketCache, _get_sina_market_url


class _FakeResponse:
    status_code = 200

    def __init__(self, url: str):
        self._url = url

    def json(self):
        page = int(parse_qs(urlparse(self._url).query)["page"][0])
        code = f"{page:06d}"
        return [{
            "code": code,
            "name": f"股票{page}",
            "trade": "10.00",
            "changepercent": "1.00",
            "settlement": "9.90",
            "open": "9.95",
            "high": "10.10",
            "low": "9.80",
            "volume": "1000",
            "amount": "10000",
        }]


class _FakeSession:
    def __init__(self):
        self.trust_env = True
        self.proxies = {}
        self.urls = []

    def get(self, url, **kwargs):
        self.urls.append(url)
        return _FakeResponse(url)


def test_sina_market_defaults_to_https_without_local_override():
    with TemporaryDirectory() as temp_dir:
        missing_config = Path(temp_dir) / "network.local.yaml"
        with patch("src.scanner.market_cache._LOCAL_NETWORK_CONFIG", missing_config), \
                patch.dict(os.environ, {"MUYUN_SINA_MARKET_URL": ""}):
            assert _get_sina_market_url().startswith("https://")


def test_sina_market_accepts_gitignored_local_http_override():
    with TemporaryDirectory() as temp_dir:
        local_config = Path(temp_dir) / "network.local.yaml"
        local_config.write_text(
            "network:\n"
            "  sina_market_url: http://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/Market_Center.getHQNodeData\n",
            encoding="utf-8",
        )
        session = _FakeSession()
        with patch("src.scanner.market_cache._LOCAL_NETWORK_CONFIG", local_config), \
                patch.dict(os.environ, {"MUYUN_SINA_MARKET_URL": ""}), \
                patch("requests.Session", return_value=session):
            frame = MarketCache()._fetch_sina_market()

    assert len(frame) == 73
    assert session.trust_env is False
    assert session.proxies == {"http": None, "https": None}
    assert session.urls
    assert all(url.startswith("http://vip.stock.finance.sina.com.cn/") for url in session.urls)


if __name__ == "__main__":
    test_sina_market_defaults_to_https_without_local_override()
    test_sina_market_accepts_gitignored_local_http_override()
    print("ALL MARKET CACHE SOURCE TESTS PASSED")