from scripts.parsers.futu_parser import classify_market, infer_side, is_real_trade_candidate


def test_futu_market_classification_is_record_level():
    assert classify_market("SEHK", "HKD").market == "HK"
    assert classify_market("EDGX", "USD").market == "US"
    assert classify_market("BATS", "USD").market == "US"
    assert classify_market("SEHK", "USD").market == ""


def test_futu_trade_candidate_rejects_cash_or_holding_rows():
    base = {
        "exchange": "SEHK",
        "currency": "HKD",
        "trade_date": "2025/10/02",
        "trade_time": "09:46:07",
        "settle_date": "2025/10/06",
        "quantity": "8000",
        "price": "4.59",
        "gross_amount": "36720.00",
        "cash_change": "36652.32",
        "raw_text": "sell close normal execution row",
    }
    assert is_real_trade_candidate(base)
    assert not is_real_trade_candidate({**base, "raw_text": "Account Upgrade cash movement"})


def test_futu_side_conflict_is_not_silent():
    side, note = infer_side("buy open", "100.00")
    assert side == ""
    assert "conflict" in note
    assert infer_side("sell close", "100.00")[0] == "SELL"
