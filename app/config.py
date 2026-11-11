from dataclasses import dataclass
import os
from dotenv import load_dotenv

load_dotenv()

@dataclass(frozen=True)
class Settings:
    host: str = os.getenv("IBKR_HOST", "127.0.0.1")
    port: int = int(os.getenv("IBKR_PORT", "4002"))
    client_id: int = int(os.getenv("IBKR_CLIENT_ID", "17"))
    paper_only: bool = os.getenv("IBKR_PAPER_ONLY", "true").lower() == "true"
    allow_paper_test_orders: bool = os.getenv("ALLOW_PAPER_TEST_ORDERS", "false").lower() == "true"
    base_currency: str = os.getenv("BASE_CURRENCY", "NOK")
    starting_capital_nok: float = float(os.getenv("STARTING_CAPITAL_NOK", "100000"))
    risk_per_trade: float = float(os.getenv("RISK_PER_TRADE", "0.005"))
    max_open_risk: float = float(os.getenv("MAX_OPEN_RISK", "0.02"))
    max_positions: int = int(os.getenv("MAX_POSITIONS", "5"))
    max_position_weight: float = float(os.getenv("MAX_POSITION_WEIGHT", "0.20"))
    daily_loss_limit: float = float(os.getenv("DAILY_LOSS_LIMIT", "0.01"))
    historical_duration: str = os.getenv("HISTORICAL_DURATION", "30 D")
    historical_bar_size: str = os.getenv("HISTORICAL_BAR_SIZE", "1 day")
    market_data_type: int = int(os.getenv("MARKET_DATA_TYPE", "3"))

    def validate_paper_mode(self) -> None:
        if not self.paper_only:
            raise RuntimeError("Safety stop: IBKR_PAPER_ONLY must be true.")
        if self.port not in (4002, 7497):
            raise RuntimeError(
                "Safety stop: only standard IBKR Paper Trading ports 4002/7497 are allowed."
            )
        if self.host not in ("127.0.0.1", "localhost"):
            raise RuntimeError("Safety stop: connect to IBKR API on localhost only.")
        if not (0 < self.risk_per_trade <= 0.01):
            raise ValueError("RISK_PER_TRADE must be >0 and <=1%.")
        if not (0 < self.max_open_risk <= 0.05):
            raise ValueError("MAX_OPEN_RISK must be >0 and <=5%.")
